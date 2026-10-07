"""Small deterministic browser-test interface backed by Playwright Chromium."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlsplit

from tools.artifacts.recorder import RunArtifactRecorder, redact_text

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # Reported cleanly when the capability is invoked.
    PlaywrightError = RuntimeError
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None


SCHEMA_VERSION = 1
SUPPORTED_ACTIONS = {
    "click",
    "expect_url",
    "expect_visible",
    "fill",
    "read_text",
    "screenshot",
    "wait_for_selector",
}
SENSITIVE_FIELD = re.compile(
    r"(password|passwd|secret|token|api[-_]?key|authorization|credential|credit[-_]?card|cvv)",
    re.I,
)


class BrowserTestError(ValueError):
    """An expected browser configuration, plan, or safety error."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _is_loopback(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_test_url(url: str, *, allow_external: bool = False) -> str:
    """Validate an explicit HTTP(S) URL and default to loopback-only access."""

    if not isinstance(url, str) or not url.strip():
        raise BrowserTestError("A non-empty target URL is required")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise BrowserTestError("Only http:// and https:// URLs are supported")
    if not parsed.hostname:
        raise BrowserTestError("Target URL must include a hostname")
    if parsed.username or parsed.password:
        raise BrowserTestError("Credentials are not allowed in target URLs")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise BrowserTestError("Target URL contains an invalid port") from exc
    if not allow_external and not _is_loopback(parsed.hostname):
        raise BrowserTestError("Browser testing is restricted to localhost and loopback URLs")
    return url


def _load_browser_config(factory_root: Path) -> dict[str, Any]:
    defaults = {
        "defaultTimeoutSeconds": 10,
        "maxTimeoutSeconds": 30,
        "maxErrorMessages": 50,
        "maxMessageBytes": 2_048,
        "maxTextBytes": 8_192,
        "headless": True,
    }
    try:
        value = json.loads((factory_root / "config" / "defaults.json").read_text(encoding="utf-8-sig"))
        browser = ((value.get("tools") or {}).get("browser")) if isinstance(value, dict) else None
        if isinstance(browser, dict):
            defaults.update(browser)
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return defaults


def _bounded_text(value: Any, limit: int) -> tuple[str, bool]:
    redacted = redact_text(value if isinstance(value, str) else str(value))
    encoded = redacted.encode("utf-8")
    stored = encoded[:limit]
    return stored.decode("utf-8", errors="replace"), len(encoded) > limit


def _selector(action: Mapping[str, Any]) -> str:
    selector = action.get("selector")
    if not isinstance(selector, str) or not selector.strip():
        raise BrowserTestError("Action requires a non-empty selector")
    if len(selector) > 1_000:
        raise BrowserTestError("Selector exceeds 1000 characters")
    return selector


def _safe_event_action(action: Mapping[str, Any]) -> dict[str, Any]:
    safe = {"action": action.get("action")}
    for key in ("selector", "state", "name", "fullPage", "exact"):
        if key in action:
            safe[key] = action[key]
    if action.get("action") == "fill":
        safe["valueLength"] = len(str(action.get("value", "")))
    if action.get("action") == "expect_url":
        safe["expectedUrl"] = action.get("url")
    if action.get("action") == "expect_visible" and "text" in action:
        safe["expectedText"] = redact_text(str(action["text"]))
    return safe


def _check_fill_is_not_sensitive(locator: Any, selector: str) -> None:
    attributes = [
        selector,
        locator.get_attribute("type") or "",
        locator.get_attribute("name") or "",
        locator.get_attribute("id") or "",
        locator.get_attribute("autocomplete") or "",
    ]
    if any(SENSITIVE_FIELD.search(value) for value in attributes):
        raise BrowserTestError("Filling credential or sensitive fields is disabled in this browser capability")


def _check_screenshot_is_not_sensitive(page: Any, timeout_ms: int) -> None:
    """Block screenshots when known textual secrets or filled credential fields are visible."""

    body_text = page.locator("body").inner_text(timeout=timeout_ms)
    if redact_text(body_text) != body_text:
        raise BrowserTestError("Screenshot blocked because the page contains a known secret pattern")
    inputs = page.locator("input")
    for index in range(min(inputs.count(), 100)):
        locator = inputs.nth(index)
        attributes = " ".join(
            locator.get_attribute(name) or ""
            for name in ("type", "name", "id", "autocomplete")
        )
        if SENSITIVE_FIELD.search(attributes) and locator.input_value(timeout=timeout_ms):
            raise BrowserTestError("Screenshot blocked because a sensitive field contains a value")


def _perform_action(page: Any, action: Mapping[str, Any], timeout_ms: int, recorder: RunArtifactRecorder, text_limit: int) -> dict[str, Any]:
    action_name = action.get("action")
    if action_name not in SUPPORTED_ACTIONS:
        raise BrowserTestError(f"Unsupported browser action: {action_name!r}")
    details: dict[str, Any] = {}

    if action_name == "wait_for_selector":
        selector = _selector(action)
        state = str(action.get("state", "visible"))
        if state not in {"attached", "detached", "hidden", "visible"}:
            raise BrowserTestError(f"Unsupported selector state: {state}")
        page.locator(selector).wait_for(state=state, timeout=timeout_ms)
        details = {"selector": selector, "state": state}
    elif action_name == "expect_visible":
        selector = _selector(action)
        locator = page.locator(selector).first
        locator.wait_for(state="visible", timeout=timeout_ms)
        details = {"selector": selector, "visible": True, "count": page.locator(selector).count()}
        if "text" in action:
            expected = str(action["text"])
            actual = locator.inner_text(timeout=timeout_ms)
            exact = bool(action.get("exact", False))
            matches = actual == expected if exact else expected in actual
            if not matches:
                raise BrowserTestError(f"Visible text did not match for selector {selector}")
            bounded, truncated = _bounded_text(actual, text_limit)
            details.update({"text": bounded, "textTruncated": truncated, "exact": exact})
    elif action_name == "read_text":
        selector = _selector(action)
        locator = page.locator(selector).first
        locator.wait_for(state="visible", timeout=timeout_ms)
        text, truncated = _bounded_text(locator.inner_text(timeout=timeout_ms), text_limit)
        details = {"selector": selector, "text": text, "textTruncated": truncated}
    elif action_name == "click":
        selector = _selector(action)
        page.locator(selector).first.click(timeout=timeout_ms)
        details = {"selector": selector}
    elif action_name == "fill":
        selector = _selector(action)
        if "value" not in action:
            raise BrowserTestError("Fill action requires a value")
        locator = page.locator(selector).first
        locator.wait_for(state="visible", timeout=timeout_ms)
        _check_fill_is_not_sensitive(locator, selector)
        value = str(action["value"])
        locator.fill(value, timeout=timeout_ms)
        details = {"selector": selector, "valueLength": len(value)}
    elif action_name == "screenshot":
        name = str(action.get("name", "screenshot"))
        _check_screenshot_is_not_sensitive(page, timeout_ms)
        screenshot_path = recorder.browser_screenshot_path(name)
        page.screenshot(path=str(screenshot_path), full_page=bool(action.get("fullPage", False)), timeout=timeout_ms)
        details = {
            "screenshotPath": screenshot_path.relative_to(recorder.run_directory).as_posix(),
            "bytes": screenshot_path.stat().st_size,
        }
    elif action_name == "expect_url":
        expected = action.get("url")
        if not isinstance(expected, str) or not expected:
            raise BrowserTestError("expect_url requires a URL or URL pattern")
        if expected.startswith(("http://", "https://")):
            validate_test_url(expected)
            destination = expected
        elif expected.startswith(("/", "#")):
            destination = urljoin(page.url, expected)
            validate_test_url(destination)
        else:
            raise BrowserTestError("expect_url requires an explicit local URL, path, or fragment")
        page.wait_for_url(destination, timeout=timeout_ms)
        details = {"expectedUrl": destination, "actualUrl": page.url}

    return details


def run_browser_test(
    url: str,
    actions: Iterable[Mapping[str, Any]],
    *,
    recorder: RunArtifactRecorder,
    timeout_seconds: float | None = None,
    headless: bool | None = None,
) -> dict[str, Any]:
    """Execute one deterministic browser plan against an already-running local URL."""

    if sync_playwright is None:
        raise BrowserTestError(
            "Playwright is not installed. Run: python -m pip install -r requirements-browser.txt"
        )
    target_url = validate_test_url(url)
    if not isinstance(recorder, RunArtifactRecorder):
        raise BrowserTestError("An active RunArtifactRecorder is required")
    config = _load_browser_config(recorder.factory_root)
    timeout = float(config["defaultTimeoutSeconds"] if timeout_seconds is None else timeout_seconds)
    maximum = float(config["maxTimeoutSeconds"])
    if timeout <= 0 or timeout > maximum:
        raise BrowserTestError(f"Timeout must be greater than 0 and no more than {maximum:g} seconds")
    timeout_ms = max(1, int(timeout * 1_000))
    max_messages = int(config["maxErrorMessages"])
    message_limit = int(config["maxMessageBytes"])
    text_limit = int(config["maxTextBytes"])
    browser_headless = bool(config["headless"] if headless is None else headless)

    started_at = _utc_now()
    started = time.monotonic()
    action_results: list[dict[str, Any]] = []
    console_errors: list[dict[str, Any]] = []
    page_errors: list[dict[str, Any]] = []
    screenshots: list[str] = []
    navigation: dict[str, Any] = {"status": "pending", "httpStatus": None, "failure": None}
    final_url: str | None = None
    title: str | None = None
    overall_status = "completed"

    def add_error(collection: list[dict[str, Any]], message: str, kind: str) -> None:
        if len(collection) >= max_messages:
            return
        text, truncated = _bounded_text(message, message_limit)
        collection.append({"type": kind, "message": text, "truncated": truncated, "at": _utc_now()})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=browser_headless)
        context = browser.new_context(accept_downloads=False, ignore_https_errors=False)

        def route_request(route: Any, request: Any) -> None:
            parsed = urlsplit(request.url)
            if parsed.scheme in {"about", "blob", "data"}:
                route.continue_()
                return
            try:
                validate_test_url(request.url)
            except BrowserTestError:
                route.abort("blockedbyclient")
                return
            route.continue_()

        context.route("**/*", route_request)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.on("console", lambda message: add_error(console_errors, message.text, "console") if message.type == "error" else None)
        page.on("pageerror", lambda error: add_error(page_errors, str(error), "pageerror"))

        nav_started = time.monotonic()
        nav_started_at = _utc_now()
        try:
            response = page.goto(target_url, wait_until="load", timeout=timeout_ms)
            navigation.update(
                {
                    "status": "completed" if response is None or response.ok else "failed",
                    "httpStatus": response.status if response is not None else None,
                    "ok": response.ok if response is not None else None,
                    "statusText": response.status_text if response is not None else None,
                }
            )
            if response is not None and not response.ok:
                overall_status = "failed"
                navigation["failure"] = f"Navigation returned HTTP {response.status}"
        except PlaywrightTimeoutError as exc:
            overall_status = "failed"
            navigation.update({"status": "timed_out", "failure": redact_text(str(exc))})
        except PlaywrightError as exc:
            overall_status = "failed"
            navigation.update({"status": "failed", "failure": redact_text(str(exc))})
        navigation.update(
            {
                "url": target_url,
                "startedAt": nav_started_at,
                "endedAt": _utc_now(),
                "durationSeconds": round(time.monotonic() - nav_started, 3),
            }
        )
        recorder.record_browser_event({"action": "navigate", **navigation})

        if overall_status == "completed":
            for index, action in enumerate(actions, start=1):
                if not isinstance(action, Mapping):
                    raise BrowserTestError("Every browser action must be an object")
                event = {
                    "action": action.get("action"),
                    "index": index,
                    "urlBefore": page.url,
                    "startedAt": _utc_now(),
                }
                action_started = time.monotonic()
                try:
                    details = _perform_action(page, action, timeout_ms, recorder, text_limit)
                    event.update(details)
                    event["status"] = "completed"
                    if "screenshotPath" in details:
                        screenshots.append(details["screenshotPath"])
                except (BrowserTestError, PlaywrightTimeoutError, PlaywrightError) as exc:
                    event.update({"status": "failed", "failure": redact_text(str(exc))})
                    overall_status = "failed"
                event.update(
                    {
                        "urlAfter": page.url,
                        "endedAt": _utc_now(),
                        "durationSeconds": round(time.monotonic() - action_started, 3),
                    }
                )
                recorder.record_browser_event(event)
                action_results.append(event)
                if event["status"] == "failed":
                    break

        final_url = page.url
        try:
            title, _ = _bounded_text(page.title(), text_limit)
        except PlaywrightError:
            title = None
        context.close()
        browser.close()

    result = {
        "schemaVersion": SCHEMA_VERSION,
        "status": overall_status,
        "requestedUrl": target_url,
        "finalUrl": final_url,
        "title": title,
        "navigation": navigation,
        "actions": action_results,
        "consoleErrors": console_errors,
        "pageErrors": page_errors,
        "errorsTruncated": len(console_errors) >= max_messages or len(page_errors) >= max_messages,
        "screenshots": screenshots,
        "timing": {
            "startedAt": started_at,
            "endedAt": _utc_now(),
            "durationSeconds": round(time.monotonic() - started, 3),
        },
    }
    recorder.record_browser_event(
        {
            "action": "browser_test_complete",
            "status": overall_status,
            "finalUrl": final_url,
            "title": title,
            "consoleErrors": console_errors,
            "pageErrors": page_errors,
            "screenshots": screenshots,
            "timing": result["timing"],
        }
    )
    return result
