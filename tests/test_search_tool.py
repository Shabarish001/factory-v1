"""Tests for provider-neutral search, Tavily normalization, and artifacts."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.artifacts import RunArtifactRecorder
from tools.search import (
    MalformedResponseError,
    MissingCredentialError,
    ProviderError,
    ProviderTimeoutError,
    SearchRequest,
    SearchService,
    TavilySearchProvider,
)


def _provider_response(count: int = 2, *, content: str = "useful snippet") -> dict[str, object]:
    return {
        "results": [
            {
                "title": f"Result {index}",
                "url": f"https://example.com/{index}",
                "content": content,
                "score": 1 - index / 10,
            }
            for index in range(count)
        ],
        "usage": {"credits": 1},
        "request_id": "request-fixture",
    }


class CapturingTransport:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def __call__(self, endpoint, payload, headers, timeout):
        self.calls.append(
            {
                "endpoint": endpoint,
                "payload": dict(payload),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class SearchToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api_key = secrets.token_urlsafe(24)
        self.environment = patch.dict(os.environ, {"TAVILY_API_KEY": self.api_key})
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()

    def test_normalizes_results_and_defaults_to_basic(self) -> None:
        transport = CapturingTransport(_provider_response())
        provider = TavilySearchProvider(transport=transport)

        response = provider.search(SearchRequest("factory architecture"))
        data = response.to_dict()

        self.assertEqual(data["searchDepth"], "basic")
        self.assertEqual(data["provider"], "tavily")
        self.assertEqual(data["resultCount"], 2)
        self.assertEqual(data["usage"]["credits"], 1)
        self.assertEqual(data["results"][0]["query"], "factory architecture")
        self.assertEqual(transport.calls[0]["payload"]["search_depth"], "basic")
        self.assertTrue(str(transport.calls[0]["endpoint"]).startswith("https://"))
        self.assertEqual(transport.calls[0]["headers"]["Authorization"], f"Bearer {self.api_key}")
        self.assertNotIn("api_key", transport.calls[0]["payload"])

    def test_advanced_depth_result_limit_and_domain_filters(self) -> None:
        transport = CapturingTransport(_provider_response(5))
        provider = TavilySearchProvider(transport=transport)
        request = SearchRequest(
            "official documentation",
            max_results=2,
            depth="advanced",
            include_domains=["docs.example.com"],
            exclude_domains=["forum.example.com"],
            time_range="month",
        )

        response = provider.search(request)
        payload = transport.calls[0]["payload"]

        self.assertEqual(response.depth.value, "advanced")
        self.assertEqual(len(response.results), 2)
        self.assertEqual(payload["max_results"], 2)
        self.assertEqual(payload["include_domains"], ["docs.example.com"])
        self.assertEqual(payload["exclude_domains"], ["forum.example.com"])
        self.assertEqual(payload["time_range"], "month")

    def test_missing_api_key_fails_before_transport(self) -> None:
        transport = CapturingTransport(_provider_response())
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(MissingCredentialError, "TAVILY_API_KEY"):
                TavilySearchProvider(transport=transport).search(SearchRequest("query"))
        self.assertEqual(transport.calls, [])

    def test_timeout_and_provider_errors_are_safe(self) -> None:
        timeout = TavilySearchProvider(transport=CapturingTransport(TimeoutError("slow")))
        with self.assertRaises(ProviderTimeoutError):
            timeout.search(SearchRequest("query"))

        failure = TavilySearchProvider(
            transport=CapturingTransport(RuntimeError(f"request leaked {self.api_key}"))
        )
        with self.assertRaises(ProviderError) as raised:
            failure.search(SearchRequest("query"))
        self.assertNotIn(self.api_key, str(raised.exception))
        self.assertIn("[REDACTED]", str(raised.exception))

    def test_rejects_malformed_provider_response(self) -> None:
        provider = TavilySearchProvider(transport=CapturingTransport({"results": "not-a-list"}))
        with self.assertRaises(MalformedResponseError):
            provider.search(SearchRequest("query"))

        malformed_result = {"results": [{"title": "missing fields"}]}
        provider = TavilySearchProvider(transport=CapturingTransport(malformed_result))
        with self.assertRaises(MalformedResponseError):
            provider.search(SearchRequest("query"))

    def test_caps_and_redacts_result_content(self) -> None:
        content = f"password=hunter2 {self.api_key} " + "x" * 100
        provider = TavilySearchProvider(
            content_limit=32,
            transport=CapturingTransport(_provider_response(1, content=content)),
        )

        result = provider.search(SearchRequest("query")).results[0]

        self.assertLessEqual(len(result.content), 32)
        self.assertNotIn("hunter2", result.content)
        self.assertNotIn(self.api_key, result.content)

    def test_request_validation_is_provider_neutral(self) -> None:
        with self.assertRaises(ValueError):
            SearchRequest("")
        with self.assertRaises(ValueError):
            SearchRequest("query", max_results=21)
        with self.assertRaises(ValueError):
            SearchRequest("query", include_domains=["https://example.com/path"])
        with self.assertRaises(ValueError):
            SearchRequest("query", depth="deep")

    def test_records_safe_artifact_without_modifying_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factory = root / "factory"
            target = root / "External Product"
            factory.mkdir()
            target.mkdir()
            target_file = target / "README.md"
            target_file.write_text("unchanged", encoding="utf-8")
            before = target_file.read_bytes()

            transport = CapturingTransport(
                _provider_response(1, content=f"private content {self.api_key}")
            )
            provider = TavilySearchProvider(transport=transport)
            service = SearchService({"tavily": provider}, default_provider="tavily")
            recorder = RunArtifactRecorder(factory, target)
            response = service.search(
                SearchRequest(f"api_key={self.api_key} docs"), recorder=recorder
            )
            recorder.complete()

            event_path = recorder.run_directory / "search" / "search-events.jsonl"
            event_text = event_path.read_text(encoding="utf-8")
            event = json.loads(event_text)
            summary = json.loads(recorder.summary_path.read_text(encoding="utf-8"))

            self.assertEqual(response.results[0].content.startswith("private content"), True)
            self.assertEqual(event["eventType"], "search")
            self.assertEqual(event["results"][0]["title"], "Result 0")
            self.assertNotIn("content", event["results"][0])
            self.assertNotIn("Authorization", event_text)
            self.assertNotIn(self.api_key, event_text)
            self.assertEqual(summary["searchEventCount"], 1)
            self.assertEqual(summary["searchStatuses"], {"completed": 1})
            self.assertEqual(target_file.read_bytes(), before)
            self.assertEqual(list(target.rglob("*")), [target_file])

    def test_failed_search_is_recorded_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factory = root / "factory"
            target = root / "target"
            factory.mkdir()
            target.mkdir()
            provider = TavilySearchProvider(
                transport=CapturingTransport(RuntimeError(self.api_key))
            )
            service = SearchService({"tavily": provider}, default_provider="tavily")
            recorder = RunArtifactRecorder(factory, target)

            with self.assertRaises(ProviderError):
                service.search(SearchRequest("query"), recorder=recorder)
            recorder.fail("search failed")

            event_text = (
                recorder.run_directory / "search" / "search-events.jsonl"
            ).read_text(encoding="utf-8")
            self.assertNotIn(self.api_key, event_text)
            self.assertIn('"status": "failed"', event_text)


if __name__ == "__main__":
    unittest.main()
