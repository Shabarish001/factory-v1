"""Offline tests for bounded source-backed research."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.artifacts import RunArtifactRecorder
from tools.research import ResearchLimits, ResearchPlan, ResearchPlanError, ResearchWorkflow
from tools.search import ProviderError, SearchDepth, SearchResponse, SearchResult, SearchService
from tools.web import PageFetchError, PublicPageFetcher, RawPageResponse


def result(query: str, title: str, url: str, score: float | None) -> SearchResult:
    return SearchResult(title, url, "search snippet", score, "fake", query)


class FakeSearchProvider:
    name = "fake"

    def __init__(self, responses, credits=None) -> None:
        self.responses = responses
        self.credits = credits or {}
        self.calls = []

    def search(self, request):
        self.calls.append(request)
        value = self.responses.get(request.query, [])
        if isinstance(value, BaseException):
            raise value
        return SearchResponse(
            query=request.query,
            provider=self.name,
            depth=request.depth,
            results=tuple(value),
            duration_seconds=0.01,
            credits=self.credits.get(request.query, 1),
        )


class FakePageTransport:
    def __init__(self, responses) -> None:
        self.responses = responses
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        value = self.responses.get(request.url)
        if isinstance(value, PageFetchError):
            raise value
        if value is None:
            raise PageFetchError("missing fixture")
        return value


def page(url: str, text: str, title: str = "Fetched title") -> RawPageResponse:
    html = f"<html><head><title>{title}</title></head><body><main>{text}</main></body></html>"
    return RawPageResponse(
        requested_url=url,
        final_url=url,
        http_status=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=html.encode("utf-8"),
    )


def workflow(search_responses, page_responses, *, credits=None, recorder=None, limits=None):
    provider = FakeSearchProvider(search_responses, credits=credits)
    transport = FakePageTransport(page_responses)
    service = SearchService({"fake": provider}, default_provider="fake")
    runner = ResearchWorkflow(
        service,
        PublicPageFetcher(transport),
        recorder=recorder,
        limits=limits,
    )
    return runner, provider, transport


class ResearchWorkflowTests(unittest.TestCase):
    def test_validates_plan_and_configured_bounds(self) -> None:
        with self.assertRaisesRegex(ResearchPlanError, "duplicate"):
            ResearchPlan("objective", ["same query", "  Same   Query "])
        with self.assertRaisesRegex(ResearchPlanError, "justification"):
            ResearchPlan("objective", ["query"], search_depth="advanced")

        advanced = ResearchPlan(
            "objective",
            ["query"],
            search_depth="advanced",
            advanced_justification="Detailed technical evidence is required.",
        )
        self.assertEqual(advanced.search_depth, SearchDepth.ADVANCED)

        limits = ResearchLimits(max_searches=6, max_results_per_search=5, max_pages=8)
        too_many_queries = ResearchPlan(
            "objective",
            [f"query {index}" for index in range(7)],
            max_searches=7,
        )
        with self.assertRaisesRegex(ResearchPlanError, "max_searches"):
            too_many_queries.validate(limits)
        with self.assertRaisesRegex(ResearchPlanError, "max_results"):
            ResearchPlan("objective", ["query"], max_results_per_search=6).validate(limits)

    def test_passes_plan_limits_and_domains_to_search(self) -> None:
        runner, provider, _ = workflow({"query": []}, {})
        plan = ResearchPlan(
            "objective",
            ["query"],
            allowed_domains=["official.example"],
            blocked_domains=["blog.official.example"],
            max_results_per_search=3,
            max_pages=2,
        )

        runner.run(plan)
        request = provider.calls[0]

        self.assertEqual(request.max_results, 3)
        self.assertEqual(request.depth, SearchDepth.BASIC)
        self.assertEqual(request.include_domains, ("official.example",))
        self.assertEqual(request.exclude_domains, ("blog.official.example",))

    def test_deduplicates_filters_ranks_and_assigns_stable_source_ids(self) -> None:
        responses = {
            "features": [
                result("features", "Product", "https://official.example/product?utm_source=test", 0.8),
                result("features", "Blocked", "https://blog.official.example/post", 0.99),
                result("features", "Outside", "https://outside.example/page", 0.95),
            ],
            "api": [
                result("api", "Product duplicate", "https://official.example/product", 0.9),
                result("api", "API", "https://official.example/api", 0.7),
            ],
        }
        pages = {
            "https://official.example/product": page(
                "https://official.example/product", "Product evidence"
            ),
            "https://official.example/api": page("https://official.example/api", "API evidence"),
        }
        plan = ResearchPlan(
            "Compare product and API",
            ["features", "api"],
            allowed_domains=["official.example"],
            blocked_domains=["blog.official.example"],
        )

        first, _, _ = workflow(responses, pages)
        second, _, _ = workflow(responses, pages)
        first_bundle = first.run(plan).to_dict()
        second_bundle = second.run(plan).to_dict()

        self.assertEqual(first_bundle["metadata"]["deduplicatedUrlCount"], 2)
        self.assertEqual([source["sourceId"] for source in first_bundle["sources"]], ["S1", "S2"])
        self.assertEqual(first_bundle["sources"][0]["url"], "https://official.example/product")
        self.assertEqual(first_bundle["sources"][0]["searchQuery"], "api")
        self.assertEqual(first_bundle["citations"], second_bundle["citations"])

    def test_bounds_page_count_result_count_and_per_page_content(self) -> None:
        results = [
            result("query", f"Page {index}", f"https://example.com/{index}", 1 - index / 10)
            for index in range(6)
        ]
        pages = {
            f"https://example.com/{index}": page(
                f"https://example.com/{index}", "x" * 100
            )
            for index in range(6)
        }
        runner, _, transport = workflow({"query": results}, pages)

        bundle = runner.run(
            ResearchPlan(
                "objective",
                ["query"],
                max_results_per_search=3,
                max_pages=2,
                per_page_content_limit=25,
            )
        )

        self.assertEqual(len(transport.requests), 2)
        self.assertTrue(all(request.max_text_characters == 25 for request in transport.requests))
        self.assertEqual(bundle.metadata["candidateResultCount"], 3)
        self.assertEqual(bundle.metadata["pagesFetched"], 2)
        self.assertTrue(all(len(source.content) <= 25 for source in bundle.sources))

    def test_preserves_source_ids_when_a_page_fails(self) -> None:
        responses = {
            "query": [
                result("query", "Unavailable", "https://example.com/a", 0.9),
                result("query", "Available", "https://example.com/b", 0.8),
            ]
        }
        pages = {
            "https://example.com/a": PageFetchError("fixture failure"),
            "https://example.com/b": page("https://example.com/b", "Evidence"),
        }
        runner, _, _ = workflow(responses, pages)

        bundle = runner.run(ResearchPlan("objective", ["query"]))

        self.assertEqual(bundle.failed_sources[0].source_id, "S1")
        self.assertEqual(bundle.sources[0].source_id, "S2")
        self.assertEqual(bundle.to_dict()["citations"][0]["sourceId"], "S2")

    def test_aggregates_credits_and_records_search_failures(self) -> None:
        responses = {
            "first": [],
            "second": [],
            "failed": ProviderError("provider unavailable"),
        }
        runner, _, _ = workflow(
            responses,
            {},
            credits={"first": 1, "second": 2},
        )

        bundle = runner.run(ResearchPlan("objective", ["first", "second", "failed"]))

        self.assertEqual(bundle.metadata["creditsUsed"], 3.0)
        self.assertFalse(bundle.metadata["creditUsageComplete"])
        self.assertEqual(bundle.metadata["successfulSearchCount"], 2)
        self.assertEqual(bundle.search_failures[0]["query"], "failed")

    def test_enforces_total_content_and_serialized_bundle_limits(self) -> None:
        responses = {
            "query": [
                result("query", "A", "https://example.com/a", 0.9),
                result("query", "B", "https://example.com/b", 0.8),
            ]
        }
        pages = {
            "https://example.com/a": page("https://example.com/a", "a" * 5_000),
            "https://example.com/b": page("https://example.com/b", "b" * 5_000),
        }
        limits = ResearchLimits(
            max_bundle_content_characters=2_000,
            max_source_bundle_bytes=4_000,
        )
        runner, _, _ = workflow(responses, pages, limits=limits)

        bundle = runner.run(
            ResearchPlan("objective", ["query"], per_page_content_limit=5_000)
        )
        encoded = (json.dumps(bundle.to_dict(), indent=2, sort_keys=True) + "\n").encode("utf-8")

        self.assertLessEqual(sum(len(source.content) for source in bundle.sources), 2_000)
        self.assertLessEqual(len(encoded), 4_000)
        self.assertTrue(bundle.metadata["bundleContentTruncated"])

    def test_creates_research_artifacts_without_target_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factory = root / "factory"
            target = root / "External Product"
            factory.mkdir()
            target.mkdir()
            (factory / "config").mkdir()
            (factory / "config" / "defaults.json").write_text(
                json.dumps({"research": {"maxSourceBundleBytes": 500000}}),
                encoding="utf-8",
            )
            target_file = target / "README.md"
            target_file.write_text("unchanged", encoding="utf-8")
            before = target_file.read_bytes()
            recorder = RunArtifactRecorder(factory, target)
            responses = {
                "query": [result("query", "Official", "https://example.com/source", 0.9)]
            }
            pages = {
                "https://example.com/source": page(
                    "https://example.com/source", "Source-backed evidence"
                )
            }
            runner, _, _ = workflow(responses, pages, recorder=recorder)

            bundle = runner.run(ResearchPlan("objective", ["query"])).to_dict()
            recorder.record_research_result("Observed: Source-backed evidence. [S1]")
            recorder.complete()

            research = recorder.run_directory / "research"
            stored_plan = json.loads((research / "research-plan.json").read_text(encoding="utf-8"))
            stored_bundle = json.loads((research / "source-bundle.json").read_text(encoding="utf-8"))
            summary = json.loads(recorder.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(stored_plan["objective"], "objective")
            self.assertEqual(stored_bundle, bundle)
            self.assertTrue((research / "fetch-events.jsonl").is_file())
            self.assertTrue((research / "research-result.md").is_file())
            self.assertTrue((recorder.run_directory / "search" / "search-events.jsonl").is_file())
            self.assertTrue(summary["researchPlanRecorded"])
            self.assertTrue(summary["sourceBundleRecorded"])
            self.assertTrue(summary["researchResultRecorded"])
            self.assertEqual(target_file.read_bytes(), before)
            self.assertEqual(list(target.rglob("*")), [target_file])


if __name__ == "__main__":
    unittest.main()
