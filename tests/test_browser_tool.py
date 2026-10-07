"""Deterministic local tests for the Playwright browser adapter."""

from __future__ import annotations

import json
import threading
import time
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tools.artifacts import RunArtifactRecorder
from tools.browser import BrowserTestError, run_browser_test, validate_test_url


FIXTURE_HTML = b"""<!doctype html>
<html>
  <head><title>Factory Browser Fixture</title></head>
  <body>
    <h1 id="heading">Welcome</h1>
    <label>Name <input id="name" name="name"></label>
    <button data-testid="book-demo" onclick="bookDemo()">Book Demo</button>
    <script>
      console.error('fixture console error');
      setTimeout(() => { throw new Error('fixture page error'); }, 0);
      function bookDemo() {
        document.querySelector('#heading').textContent = 'Booked';
        window.location.hash = 'booked';
      }
    </script>
  </body>
</html>"""


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/slow"):
            time.sleep(0.5)
        if self.path.startswith("/missing"):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"missing")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(FIXTURE_HTML)))
        self.end_headers()
        try:
            self.wfile.write(FIXTURE_HTML)
        except OSError:
            pass

    def log_message(self, format: str, *args: object) -> None:
        return


class BrowserToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)

    @staticmethod
    def _roots(directory: str) -> tuple[Path, Path]:
        root = Path(directory)
        factory = root / "factory"
        target = root / "target"
        factory.mkdir()
        target.mkdir()
        (target / "README.md").write_text("fixture target", encoding="utf-8")
        return factory, target

    @staticmethod
    def _snapshot(target: Path) -> dict[str, bytes]:
        return {
            path.relative_to(target).as_posix(): path.read_bytes()
            for path in target.rglob("*")
            if path.is_file()
        }

    def test_rejects_invalid_and_external_urls(self) -> None:
        for url in ("", "file:///tmp/page.html", "ftp://127.0.0.1/page", "https://example.com"):
            with self.subTest(url=url), self.assertRaises(BrowserTestError):
                validate_test_url(url)
        self.assertEqual(validate_test_url(self.base_url), self.base_url)

    def test_runs_explicit_actions_and_records_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._roots(directory)
            before = self._snapshot(target)
            recorder = RunArtifactRecorder(factory, target)
            result = run_browser_test(
                self.base_url,
                [
                    {"action": "wait_for_selector", "selector": "#heading"},
                    {"action": "expect_visible", "selector": "#heading", "text": "Welcome", "exact": True},
                    {"action": "fill", "selector": "#name", "value": "Ada"},
                    {"action": "click", "selector": "[data-testid='book-demo']"},
                    {"action": "expect_url", "url": "#booked"},
                    {"action": "read_text", "selector": "#heading"},
                    {"action": "screenshot", "name": "booked-state"},
                ],
                recorder=recorder,
            )
            recorder.complete()
            after = self._snapshot(target)

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["title"], "Factory Browser Fixture")
            self.assertTrue(result["finalUrl"].endswith("#booked"))
            self.assertEqual(result["actions"][5]["text"], "Booked")
            self.assertTrue(result["consoleErrors"])
            self.assertTrue(result["pageErrors"])
            self.assertEqual(before, after)

            screenshot = recorder.run_directory / result["screenshots"][0]
            events = recorder.run_directory / "browser" / "browser-events.jsonl"
            self.assertTrue(screenshot.is_file())
            self.assertGreater(screenshot.stat().st_size, 0)
            self.assertTrue(events.is_file())
            event_rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
            self.assertGreaterEqual(len(event_rows), 9)
            self.assertNotIn("Ada", events.read_text(encoding="utf-8"))
            summary = json.loads(recorder.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["browserEventCount"], len(event_rows))

    def test_reports_http_navigation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._roots(directory)
            with RunArtifactRecorder(factory, target) as recorder:
                result = run_browser_test(f"{self.base_url}/missing", [], recorder=recorder)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["navigation"]["httpStatus"], 404)
            self.assertEqual(result["navigation"]["status"], "failed")

    def test_reports_navigation_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._roots(directory)
            with RunArtifactRecorder(factory, target) as recorder:
                result = run_browser_test(
                    f"{self.base_url}/slow",
                    [],
                    recorder=recorder,
                    timeout_seconds=0.05,
                )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["navigation"]["status"], "timed_out")


if __name__ == "__main__":
    unittest.main()
