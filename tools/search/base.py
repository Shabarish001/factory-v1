"""Provider-neutral search contracts and normalized result types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class SearchError(RuntimeError):
    """Base class for safe-to-report search failures."""


class MissingCredentialError(SearchError):
    """The selected provider credential is unavailable."""


class ProviderError(SearchError):
    """The provider rejected or failed a request."""


class ProviderRateLimitError(ProviderError):
    """The provider rejected a request because of a usage limit."""


class ProviderTimeoutError(ProviderError):
    """The provider request exceeded its bounded timeout."""


class MalformedResponseError(ProviderError):
    """The provider returned data that does not match its contract."""


class SearchDepth(str, Enum):
    BASIC = "basic"
    ADVANCED = "advanced"


class SearchTimeRange(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


def _enum_value(value: str | Enum, enum_type: type[Enum], field: str) -> str:
    candidate = value.value if isinstance(value, Enum) else value
    try:
        return str(enum_type(candidate).value)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field} must be one of: {choices}") from exc


def _domains(value: tuple[str, ...] | list[str], field: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise ValueError(f"{field} must be a sequence of domain names")
    normalized: list[str] = []
    for domain in value:
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError(f"{field} contains an invalid domain")
        candidate = domain.strip().lower().rstrip(".")
        if "://" in candidate or "/" in candidate or any(char.isspace() for char in candidate):
            raise ValueError(f"{field} entries must be host names, not URLs")
        if candidate not in normalized:
            normalized.append(candidate)
    return tuple(normalized)


@dataclass(frozen=True)
class SearchRequest:
    """A provider-independent, bounded web search request."""

    query: str
    max_results: int = 5
    depth: SearchDepth | str = SearchDepth.BASIC
    include_domains: tuple[str, ...] | list[str] = ()
    exclude_domains: tuple[str, ...] | list[str] = ()
    time_range: SearchTimeRange | str | None = None

    def __post_init__(self) -> None:
        query = self.query.strip() if isinstance(self.query, str) else ""
        if not query:
            raise ValueError("query must be a non-empty string")
        if len(query) > 400:
            raise ValueError("query must not exceed 400 characters")
        if isinstance(self.max_results, bool) or not isinstance(self.max_results, int):
            raise ValueError("max_results must be an integer")
        if not 1 <= self.max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")

        depth = _enum_value(self.depth, SearchDepth, "depth")
        time_range = (
            _enum_value(self.time_range, SearchTimeRange, "time_range")
            if self.time_range is not None
            else None
        )
        include = _domains(self.include_domains, "include_domains")
        exclude = _domains(self.exclude_domains, "exclude_domains")
        overlap = set(include).intersection(exclude)
        if overlap:
            raise ValueError(f"Domains cannot be both included and excluded: {sorted(overlap)[0]}")

        object.__setattr__(self, "query", query)
        object.__setattr__(self, "depth", SearchDepth(depth))
        object.__setattr__(self, "time_range", SearchTimeRange(time_range) if time_range else None)
        object.__setattr__(self, "include_domains", include)
        object.__setattr__(self, "exclude_domains", exclude)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    content: str
    score: float | None
    provider: str
    query: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "score": self.score,
            "provider": self.provider,
            "query": self.query,
        }


@dataclass(frozen=True)
class SearchResponse:
    query: str
    provider: str
    depth: SearchDepth
    results: tuple[SearchResult, ...]
    duration_seconds: float
    credits: float | int | None = None
    estimated_cost: float | None = None
    currency: str | None = None
    provider_request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "completed",
            "query": self.query,
            "provider": self.provider,
            "searchDepth": self.depth.value,
            "resultCount": len(self.results),
            "durationSeconds": self.duration_seconds,
            "usage": {
                "credits": self.credits,
                "estimatedCost": self.estimated_cost,
                "currency": self.currency,
            },
            "providerRequestId": self.provider_request_id,
            "results": [result.to_dict() for result in self.results],
        }


class SearchProvider(Protocol):
    """Interface implemented by each external search provider adapter."""

    name: str

    def search(self, request: SearchRequest) -> SearchResponse:
        """Execute and normalize one search request."""

