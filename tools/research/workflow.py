"""Deterministic search, source selection, fetch, and citation bundling."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tools.artifacts import RunArtifactRecorder
from tools.artifacts.recorder import redact_data, redact_text
from tools.search import SearchDepth, SearchError, SearchRequest, SearchResponse, SearchService
from tools.web import PageFetchRequest, PublicPageFetcher


DEFAULT_MAX_SEARCHES = 6
DEFAULT_MAX_RESULTS_PER_SEARCH = 5
DEFAULT_MAX_PAGES = 8
DEFAULT_PAGE_CONTENT_CHARACTERS = 12_000
DEFAULT_MAX_PAGE_CONTENT_CHARACTERS = 20_000
DEFAULT_MAX_BUNDLE_CONTENT_CHARACTERS = 100_000
DEFAULT_MAX_SOURCE_BUNDLE_BYTES = 500_000
TRACKING_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer"}


class ResearchPlanError(ValueError):
    """The requested research plan exceeds or violates configured bounds."""


def _domains(value: tuple[str, ...] | list[str], field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise ResearchPlanError(f"{field_name} must be a list of domain names")
    normalized: list[str] = []
    for value_item in value:
        if not isinstance(value_item, str) or not value_item.strip():
            raise ResearchPlanError(f"{field_name} contains an invalid domain")
        domain = value_item.strip().lower().rstrip(".")
        if "://" in domain or "/" in domain or any(character.isspace() for character in domain):
            raise ResearchPlanError(f"{field_name} entries must be host names")
        if domain not in normalized:
            normalized.append(domain)
    return tuple(normalized)


def _bounded_integer(value: Any, name: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ResearchPlanError(f"{name} must be an integer of at least {minimum}")
    return value


@dataclass(frozen=True)
class ResearchLimits:
    max_searches: int = DEFAULT_MAX_SEARCHES
    max_results_per_search: int = DEFAULT_MAX_RESULTS_PER_SEARCH
    max_pages: int = DEFAULT_MAX_PAGES
    default_page_content_characters: int = DEFAULT_PAGE_CONTENT_CHARACTERS
    max_page_content_characters: int = DEFAULT_MAX_PAGE_CONTENT_CHARACTERS
    max_bundle_content_characters: int = DEFAULT_MAX_BUNDLE_CONTENT_CHARACTERS
    max_source_bundle_bytes: int = DEFAULT_MAX_SOURCE_BUNDLE_BYTES

    def __post_init__(self) -> None:
        for name in (
            "max_searches",
            "max_results_per_search",
            "max_pages",
            "default_page_content_characters",
            "max_page_content_characters",
            "max_bundle_content_characters",
            "max_source_bundle_bytes",
        ):
            _bounded_integer(getattr(self, name), name)
        if self.max_searches > 20:
            raise ResearchPlanError("max_searches cannot exceed 20")
        if self.max_results_per_search > 20:
            raise ResearchPlanError("max_results_per_search cannot exceed 20")
        if self.max_pages > 20:
            raise ResearchPlanError("max_pages cannot exceed 20")
        if self.default_page_content_characters > self.max_page_content_characters:
            raise ResearchPlanError("default page content limit exceeds its configured maximum")


def load_research_limits(factory_root: str | Path) -> ResearchLimits:
    """Load research bounds, falling back to conservative built-in defaults."""

    path = Path(factory_root).resolve() / "config" / "defaults.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        config = value.get("research") if isinstance(value, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        config = None
    if not isinstance(config, Mapping):
        return ResearchLimits()
    return ResearchLimits(
        max_searches=int(config.get("maxSearches", DEFAULT_MAX_SEARCHES)),
        max_results_per_search=int(
            config.get("maxResultsPerSearch", DEFAULT_MAX_RESULTS_PER_SEARCH)
        ),
        max_pages=int(config.get("maxPages", DEFAULT_MAX_PAGES)),
        default_page_content_characters=int(
            config.get("defaultPageContentCharacters", DEFAULT_PAGE_CONTENT_CHARACTERS)
        ),
        max_page_content_characters=int(
            config.get("maxPageContentCharacters", DEFAULT_MAX_PAGE_CONTENT_CHARACTERS)
        ),
        max_bundle_content_characters=int(
            config.get("maxBundleContentCharacters", DEFAULT_MAX_BUNDLE_CONTENT_CHARACTERS)
        ),
        max_source_bundle_bytes=int(
            config.get("maxSourceBundleBytes", DEFAULT_MAX_SOURCE_BUNDLE_BYTES)
        ),
    )


@dataclass(frozen=True)
class ResearchPlan:
    objective: str
    queries: tuple[str, ...] | list[str]
    allowed_domains: tuple[str, ...] | list[str] = ()
    blocked_domains: tuple[str, ...] | list[str] = ()
    max_searches: int = DEFAULT_MAX_SEARCHES
    max_results_per_search: int = DEFAULT_MAX_RESULTS_PER_SEARCH
    max_pages: int = DEFAULT_MAX_PAGES
    search_depth: SearchDepth | str = SearchDepth.BASIC
    per_page_content_limit: int = DEFAULT_PAGE_CONTENT_CHARACTERS
    advanced_justification: str | None = None

    def __post_init__(self) -> None:
        objective = self.objective.strip() if isinstance(self.objective, str) else ""
        if not objective or len(objective) > 4_000:
            raise ResearchPlanError("objective must be between 1 and 4000 characters")
        if isinstance(self.queries, str) or not isinstance(self.queries, (tuple, list)):
            raise ResearchPlanError("queries must be a list")
        queries: list[str] = []
        seen_queries: set[str] = set()
        for query in self.queries:
            normalized = query.strip() if isinstance(query, str) else ""
            if not normalized or len(normalized) > 400:
                raise ResearchPlanError("each query must be between 1 and 400 characters")
            if redact_text(normalized) != normalized:
                raise ResearchPlanError("a search query appears to contain a secret")
            key = re.sub(r"\s+", " ", normalized).casefold()
            if key in seen_queries:
                raise ResearchPlanError("duplicate search queries are not allowed")
            seen_queries.add(key)
            queries.append(normalized)
        if not queries:
            raise ResearchPlanError("at least one search query is required")

        try:
            depth = self.search_depth if isinstance(self.search_depth, SearchDepth) else SearchDepth(self.search_depth)
        except ValueError as exc:
            raise ResearchPlanError("search_depth must be basic or advanced") from exc
        allowed = _domains(self.allowed_domains, "allowed_domains")
        blocked = _domains(self.blocked_domains, "blocked_domains")
        overlap = set(allowed).intersection(blocked)
        if overlap:
            raise ResearchPlanError(f"a domain cannot be both allowed and blocked: {sorted(overlap)[0]}")

        for name in ("max_searches", "max_results_per_search", "max_pages", "per_page_content_limit"):
            _bounded_integer(getattr(self, name), name)
        justification = self.advanced_justification.strip() if isinstance(self.advanced_justification, str) else None
        if depth is SearchDepth.ADVANCED and not justification:
            raise ResearchPlanError("advanced search requires an explicit justification")

        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "queries", tuple(queries))
        object.__setattr__(self, "allowed_domains", allowed)
        object.__setattr__(self, "blocked_domains", blocked)
        object.__setattr__(self, "search_depth", depth)
        object.__setattr__(self, "advanced_justification", justification)

    def validate(self, limits: ResearchLimits) -> None:
        if self.max_searches > limits.max_searches:
            raise ResearchPlanError("max_searches exceeds the configured research limit")
        if len(self.queries) > self.max_searches:
            raise ResearchPlanError("query count exceeds max_searches")
        if self.max_results_per_search > limits.max_results_per_search:
            raise ResearchPlanError("max_results_per_search exceeds the configured research limit")
        if self.max_pages > limits.max_pages:
            raise ResearchPlanError("max_pages exceeds the configured research limit")
        if self.per_page_content_limit > limits.max_page_content_characters:
            raise ResearchPlanError("per_page_content_limit exceeds the configured research limit")

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "queries": list(self.queries),
            "allowedDomains": list(self.allowed_domains),
            "blockedDomains": list(self.blocked_domains),
            "maxSearches": self.max_searches,
            "maxResultsPerSearch": self.max_results_per_search,
            "maxPages": self.max_pages,
            "searchDepth": self.search_depth.value,
            "perPageContentLimit": self.per_page_content_limit,
            "advancedJustification": self.advanced_justification,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResearchPlan":
        if not isinstance(value, Mapping):
            raise ResearchPlanError("research plan must be a JSON object")
        allowed_keys = {
            "objective", "queries", "allowedDomains", "blockedDomains", "maxSearches",
            "maxResultsPerSearch", "maxPages", "searchDepth", "perPageContentLimit",
            "advancedJustification",
        }
        unknown = set(value).difference(allowed_keys)
        if unknown:
            raise ResearchPlanError(f"unknown research plan field: {sorted(unknown)[0]}")
        try:
            return cls(
                objective=value["objective"],
                queries=value["queries"],
                allowed_domains=value.get("allowedDomains", ()),
                blocked_domains=value.get("blockedDomains", ()),
                max_searches=value.get("maxSearches", DEFAULT_MAX_SEARCHES),
                max_results_per_search=value.get(
                    "maxResultsPerSearch", DEFAULT_MAX_RESULTS_PER_SEARCH
                ),
                max_pages=value.get("maxPages", DEFAULT_MAX_PAGES),
                search_depth=value.get("searchDepth", SearchDepth.BASIC.value),
                per_page_content_limit=value.get(
                    "perPageContentLimit", DEFAULT_PAGE_CONTENT_CHARACTERS
                ),
                advanced_justification=value.get("advancedJustification"),
            )
        except KeyError as exc:
            raise ResearchPlanError(f"missing required research plan field: {exc.args[0]}") from exc


@dataclass
class ResearchSource:
    source_id: str
    title: str
    url: str
    domain: str
    search_query: str
    search_score: float | None
    content: str
    fetch_status: str
    final_url: str | None
    http_status: int | None
    content_type: str | None
    content_truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "searchQuery": self.search_query,
            "searchScore": self.search_score,
            "content": self.content,
            "contentLength": len(self.content),
            "fetchStatus": self.fetch_status,
            "finalUrl": self.final_url,
            "httpStatus": self.http_status,
            "contentType": self.content_type,
            "contentTruncated": self.content_truncated,
        }


@dataclass(frozen=True)
class FailedSource:
    source_id: str
    title: str
    url: str
    domain: str
    search_query: str
    search_score: float | None
    fetch_status: str
    error: Mapping[str, str] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "searchQuery": self.search_query,
            "searchScore": self.search_score,
            "fetchStatus": self.fetch_status,
            "error": dict(self.error) if self.error else None,
        }


@dataclass
class ResearchBundle:
    objective: str
    queries: tuple[str, ...]
    sources: list[ResearchSource]
    failed_sources: list[FailedSource]
    search_failures: list[dict[str, str]]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "queries": list(self.queries),
            "sources": [source.to_dict() for source in self.sources],
            "failedSources": [source.to_dict() for source in self.failed_sources],
            "citations": [
                {"sourceId": source.source_id, "title": source.title, "url": source.url}
                for source in self.sources
            ],
            "searchFailures": list(self.search_failures),
            "metadata": dict(self.metadata),
        }


@dataclass
class _Candidate:
    title: str
    url: str
    domain: str
    query: str
    score: float | None
    query_index: int
    result_index: int


def _domain_matches(domain: str, rule: str) -> bool:
    return domain == rule or domain.endswith(f".{rule}")


def _domain_allowed(domain: str, allowed: tuple[str, ...], blocked: tuple[str, ...]) -> bool:
    if any(_domain_matches(domain, rule) for rule in blocked):
        return False
    return not allowed or any(_domain_matches(domain, rule) for rule in allowed)


def _canonical_url(value: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme.lower() not in {"http", "https"} or not hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    host = hostname if port is None or default_port else f"{hostname}:{port}"
    if ":" in hostname and not hostname.startswith("["):
        host = f"[{hostname}]" if port is None or default_port else f"[{hostname}]:{port}"
    query_items = [
        (key, value_item)
        for key, value_item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    canonical = urlunsplit(
        (parsed.scheme.lower(), host, parsed.path or "/", urlencode(query_items, doseq=True), "")
    )
    return canonical, hostname


def _rank_key(candidate: _Candidate) -> tuple[float, int, int, str]:
    score = candidate.score if candidate.score is not None else -1.0
    return (-score, candidate.query_index, candidate.result_index, candidate.url)


class ResearchWorkflow:
    """Run one validated, bounded, source-backed research plan."""

    def __init__(
        self,
        search_service: SearchService,
        page_fetcher: PublicPageFetcher,
        *,
        recorder: RunArtifactRecorder | None = None,
        limits: ResearchLimits | None = None,
    ) -> None:
        self.search_service = search_service
        self.page_fetcher = page_fetcher
        self.recorder = recorder
        self.limits = limits or (
            load_research_limits(recorder.factory_root) if recorder is not None else ResearchLimits()
        )

    def run(self, plan: ResearchPlan) -> ResearchBundle:
        if not isinstance(plan, ResearchPlan):
            raise TypeError("plan must be a ResearchPlan")
        plan.validate(self.limits)
        started = time.monotonic()
        if self.recorder is not None:
            self.recorder.record_research_plan(plan.to_dict())

        responses: list[tuple[int, SearchResponse]] = []
        search_failures: list[dict[str, str]] = []
        credits_used: float = 0.0
        credit_usage_complete = True
        for query_index, query in enumerate(plan.queries):
            request = SearchRequest(
                query=query,
                max_results=plan.max_results_per_search,
                depth=plan.search_depth,
                include_domains=plan.allowed_domains,
                exclude_domains=plan.blocked_domains,
            )
            try:
                response = self.search_service.search(request, recorder=self.recorder)
            except SearchError as exc:
                search_failures.append({"query": query, "error": redact_text(str(exc))})
                credit_usage_complete = False
                continue
            responses.append((query_index, response))
            if response.credits is None:
                credit_usage_complete = False
            else:
                credits_used += float(response.credits)

        candidates_by_url: dict[str, _Candidate] = {}
        total_results = 0
        for query_index, response in responses:
            for result_index, result in enumerate(response.results[: plan.max_results_per_search]):
                total_results += 1
                canonical = _canonical_url(result.url)
                if canonical is None:
                    continue
                canonical_url, domain = canonical
                if not _domain_allowed(domain, plan.allowed_domains, plan.blocked_domains):
                    continue
                candidate = _Candidate(
                    title=result.title,
                    url=canonical_url,
                    domain=domain,
                    query=plan.queries[query_index],
                    score=result.score,
                    query_index=query_index,
                    result_index=result_index,
                )
                existing = candidates_by_url.get(canonical_url)
                if existing is None or _rank_key(candidate) < _rank_key(existing):
                    candidates_by_url[canonical_url] = candidate

        ranked = sorted(candidates_by_url.values(), key=_rank_key)
        selected = ranked[: plan.max_pages]
        sources: list[ResearchSource] = []
        failed_sources: list[FailedSource] = []
        per_source_bundle_limit = max(
            1,
            self.limits.max_bundle_content_characters // max(1, len(selected)),
        )
        for index, candidate in enumerate(selected, start=1):
            source_id = f"S{index}"
            response = self.page_fetcher.fetch(
                PageFetchRequest(
                    candidate.url,
                    max_text_characters=min(
                        plan.per_page_content_limit,
                        per_source_bundle_limit,
                    ),
                ),
                recorder=self.recorder,
            )
            if response.status != "completed":
                failed_sources.append(
                    FailedSource(
                        source_id=source_id,
                        title=candidate.title,
                        url=candidate.url,
                        domain=candidate.domain,
                        search_query=candidate.query,
                        search_score=candidate.score,
                        fetch_status=response.status,
                        error=response.error,
                    )
                )
                continue
            content = response.text
            sources.append(
                ResearchSource(
                    source_id=source_id,
                    title=response.title or candidate.title,
                    url=candidate.url,
                    domain=candidate.domain,
                    search_query=candidate.query,
                    search_score=candidate.score,
                    content=content,
                    fetch_status=response.status,
                    final_url=response.final_url,
                    http_status=response.http_status,
                    content_type=response.content_type,
                    content_truncated=response.text_truncated,
                )
            )

        bundle = ResearchBundle(
            objective=plan.objective,
            queries=tuple(plan.queries),
            sources=sources,
            failed_sources=failed_sources,
            search_failures=search_failures,
            metadata={
                "searchCount": len(plan.queries),
                "successfulSearchCount": len(responses),
                "candidateResultCount": total_results,
                "deduplicatedUrlCount": len(candidates_by_url),
                "pagesFetched": len(selected),
                "pagesCompleted": len(sources),
                "creditsUsed": credits_used,
                "creditUsageComplete": credit_usage_complete,
                "searchDepth": plan.search_depth.value,
                "durationSeconds": round(time.monotonic() - started, 3),
                "bundleContentCharacters": sum(len(source.content) for source in sources),
                "bundleContentTruncated": any(source.content_truncated for source in sources),
            },
        )
        self._fit_artifact_limit(bundle)
        if self.recorder is not None:
            self.recorder.record_source_bundle(bundle.to_dict())
        return bundle

    def _fit_artifact_limit(self, bundle: ResearchBundle) -> None:
        """Trim source text deterministically if JSON encoding exceeds its hard cap."""

        def encoded_size() -> int:
            safe = redact_data(bundle.to_dict())
            return len((json.dumps(safe, indent=2, sort_keys=True) + "\n").encode("utf-8"))

        if encoded_size() <= self.limits.max_source_bundle_bytes:
            return
        original = [source.content for source in bundle.sources]
        low, high = 0, sum(len(content) for content in original)
        while low < high:
            budget = (low + high + 1) // 2
            remaining = budget
            for source, content in zip(bundle.sources, original):
                source.content = content[:remaining]
                source.content_truncated = source.content_truncated or len(source.content) < len(content)
                remaining -= len(source.content)
            if encoded_size() <= self.limits.max_source_bundle_bytes:
                low = budget
            else:
                high = budget - 1
        remaining = low
        for source, content in zip(bundle.sources, original):
            source.content = content[:remaining]
            source.content_truncated = source.content_truncated or len(source.content) < len(content)
            remaining -= len(source.content)
        bundle.metadata["bundleContentCharacters"] = sum(
            len(source.content) for source in bundle.sources
        )
        bundle.metadata["bundleContentTruncated"] = True
        if encoded_size() > self.limits.max_source_bundle_bytes:
            raise ResearchPlanError("source bundle metadata exceeds the configured artifact byte limit")
