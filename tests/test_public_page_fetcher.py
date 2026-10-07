"""Offline tests for safe public webpage fetching and extraction."""

from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path

from tools.artifacts import RunArtifactRecorder
from tools.web import (
    HttpPageTransport,
    PageFetchRequest,
    PublicPageFetcher,
    RawPageResponse,
    fetch_page,
)


PUBLIC_IP = "93.184.216.34"


def public_resolver(hostname: str, port: int) -> tuple[str, ...]:
    del hostname, port
    return (PUBLIC_IP,)


def raw(
    url: str,
    *,
    status: int = 200,
    content_type: str = "text/html; charset=utf-8",
    body: bytes = b"<html><body>ok</body></html>",
    headers: dict[str, str] | None = None,
) -> RawPageResponse:
    response_headers = {"Content-Type": content_type}
    response_headers.update(headers or {})
    return RawPageResponse(url, url, status, response_headers, body)


class ScriptedRequester:
    def __init__(self, responses: list[RawPageResponse | BaseException]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def __call__(self, url, addresses, request):
        del request
        self.calls.append((url, tuple(addresses)))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def fetcher(requester: ScriptedRequester) -> PublicPageFetcher:
    return PublicPageFetcher(
        HttpPageTransport(resolver=public_resolver, request_once=requester)
    )


class PublicPageFetcherTests(unittest.TestCase):
    def test_normalizes_public_html_title_and_readable_text(self) -> None:
        page = b"""
            <html><head><title> Product &amp; Plans </title>
            <style>.hidden { display: none }</style></head>
            <body><header>Global navigation</header><nav>Products Pricing</nav>
            <main><h1>Welcome</h1><p>Useful   product\n information.</p>
            <div hidden>Invisible words</div><script>stealSecrets()</script></main>
            <footer>Copyright noise</footer></body></html>
        """
        requester = ScriptedRequester([raw("https://example.com/product", body=page)])

        result = fetcher(requester).fetch(PageFetchRequest("https://example.com/product"))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.title, "Product & Plans")
        self.assertEqual(result.text, "Welcome Useful product information.")
        self.assertEqual(result.text_length, len(result.text))
        self.assertNotIn("navigation", result.text)
        self.assertNotIn("stealSecrets", result.text)

    def test_caps_response_bytes_declared_length_and_extracted_text(self) -> None:
        oversized = ScriptedRequester(
            [raw("https://example.com/large", body=b"x" * 21, content_type="text/plain")]
        )
        result = fetcher(oversized).fetch(
            PageFetchRequest("https://example.com/large", max_response_bytes=20)
        )
        self.assertEqual(result.error["code"], "response_too_large")

        declared = ScriptedRequester(
            [
                raw(
                    "https://example.com/declared",
                    body=b"small",
                    content_type="text/plain",
                    headers={"Content-Length": "500"},
                )
            ]
        )
        result = fetcher(declared).fetch(
            PageFetchRequest("https://example.com/declared", max_response_bytes=20)
        )
        self.assertEqual(result.error["code"], "response_too_large")

        text = ScriptedRequester(
            [raw("https://example.com/text", body=b"one two three four", content_type="text/plain")]
        )
        result = fetcher(text).fetch(
            PageFetchRequest("https://example.com/text", max_text_characters=7)
        )
        self.assertEqual(result.text, "one two")
        self.assertTrue(result.text_truncated)

    def test_follows_bounded_public_redirect(self) -> None:
        requester = ScriptedRequester(
            [
                raw(
                    "https://example.com/start",
                    status=302,
                    headers={"Location": "https://www.example.org/final"},
                ),
                raw("https://www.example.org/final", body=b"<h1>Destination</h1>"),
            ]
        )

        result = fetcher(requester).fetch(PageFetchRequest("https://example.com/start"))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.final_url, "https://www.example.org/final")
        self.assertEqual(result.redirect_count, 1)
        self.assertEqual(len(requester.calls), 2)
        self.assertEqual(requester.calls[0][1], (PUBLIC_IP,))

    def test_rejects_redirect_to_private_address_before_second_request(self) -> None:
        requester = ScriptedRequester(
            [
                raw(
                    "https://example.com/start",
                    status=302,
                    headers={"Location": "http://127.0.0.1/admin"},
                )
            ]
        )

        result = fetcher(requester).fetch(PageFetchRequest("https://example.com/start"))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error["code"], "url_rejected")
        self.assertEqual(len(requester.calls), 1)

    def test_rejects_local_private_file_internal_and_credential_urls(self) -> None:
        rejected = (
            "http://localhost/",
            "http://127.0.0.1/",
            "http://[::1]/",
            "http://10.1.2.3/",
            "http://169.254.169.254/latest/meta-data/",
            "http://service.internal/",
            "http://intranet/",
            "file:///etc/passwd",
            "ftp://example.com/file",
            "https://user:password@example.com/",
        )
        for url in rejected:
            with self.subTest(url=url):
                requester = ScriptedRequester([])
                result = fetcher(requester).fetch(PageFetchRequest(url))
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.error["code"], "url_rejected")
                self.assertEqual(requester.calls, [])

    def test_rejects_hostname_when_any_dns_answer_is_private(self) -> None:
        requester = ScriptedRequester([])

        def mixed_resolver(hostname: str, port: int) -> tuple[str, ...]:
            del hostname, port
            return (PUBLIC_IP, "10.0.0.8")

        tool = PublicPageFetcher(
            HttpPageTransport(resolver=mixed_resolver, request_once=requester)
        )
        result = tool.fetch(PageFetchRequest("https://example.com/"))

        self.assertEqual(result.error["code"], "url_rejected")
        self.assertEqual(requester.calls, [])

    def test_reports_timeout_without_network_access(self) -> None:
        requester = ScriptedRequester([socket.timeout("fixture timeout")])

        result = fetcher(requester).fetch(PageFetchRequest("https://example.com/slow"))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error["code"], "timeout")

    def test_tolerates_malformed_html(self) -> None:
        malformed = b"<html><head><title>Broken</title></head><body><main><h1>Hello<p>World"
        requester = ScriptedRequester([raw("https://example.com/broken", body=malformed)])

        result = fetcher(requester).fetch(PageFetchRequest("https://example.com/broken"))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.title, "Broken")
        self.assertEqual(result.text, "Hello World")

    def test_rejects_unsupported_content_type(self) -> None:
        requester = ScriptedRequester(
            [raw("https://example.com/archive", content_type="application/zip", body=b"PK")]
        )

        result = fetcher(requester).fetch(PageFetchRequest("https://example.com/archive"))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error["code"], "unsupported_content_type")
        self.assertEqual(result.content_type, "application/zip")
        self.assertEqual(result.text, "")

    def test_records_safe_metadata_and_never_modifies_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factory = root / "factory"
            target = root / "External Product"
            factory.mkdir()
            target.mkdir()
            target_file = target / "README.md"
            target_file.write_text("unchanged", encoding="utf-8")
            before = target_file.read_bytes()
            requester = ScriptedRequester(
                [raw("https://example.com/page?token=secret", body=b"<h1>Private page text</h1>")]
            )
            recorder = RunArtifactRecorder(factory, target)

            result = fetch_page(
                "https://example.com/page?token=secret",
                recorder=recorder,
                transport=HttpPageTransport(resolver=public_resolver, request_once=requester),
            )
            recorder.complete()

            event_path = recorder.run_directory / "research" / "fetch-events.jsonl"
            event_text = event_path.read_text(encoding="utf-8")
            event = json.loads(event_text)
            summary = json.loads(recorder.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(result.text, "Private page text")
            self.assertEqual(event["requestedUrl"], "https://example.com/page")
            self.assertNotIn("secret", event_text)
            self.assertNotIn("Private page text", event_text)
            self.assertEqual(summary["fetchEventCount"], 1)
            self.assertEqual(summary["fetchStatuses"], {"completed": 1})
            self.assertEqual(target_file.read_bytes(), before)
            self.assertEqual(list(target.rglob("*")), [target_file])


if __name__ == "__main__":
    unittest.main()
