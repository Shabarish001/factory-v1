"""Tavily implementation of the provider-neutral search contract."""

from __future__ import annotations

import json
import os
import socket
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from tools.artifacts.recorder import redact_text

from .base import (
    MalformedResponseError,
    MissingCredentialError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    SearchRequest,
    SearchResponse,
    SearchResult,
)


TAVILY_ENDPOINT = "https://api.tavily.com/search"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 30.0
DEFAULT_CONTENT_LIMIT = 4_000
MAX_RESPONSE_BYTES = 2_000_000
Transport = Callable[[str, Mapping[str, Any], Mapping[str, str], float], Mapping[str, Any]]


def _safe_text(value: Any, api_key: str) -> str:
    text = value if isinstance(value, str) else str(value)
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    return redact_text(text)


class TavilySearchProvider:
    """Minimal direct Tavily API adapter with no third-party dependency."""

    name = "tavily"

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        content_limit: int = DEFAULT_CONTENT_LIMIT,
        transport: Transport | None = None,
        endpoint: str = TAVILY_ENDPOINT,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Tavily endpoint must be an HTTPS URL")
        if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be between 0 and {MAX_TIMEOUT_SECONDS:g}")
        if not 1 <= content_limit <= 32_768:
            raise ValueError("content_limit must be between 1 and 32768 characters")
        self.endpoint = endpoint
        self.timeout_seconds = float(timeout_seconds)
        self.content_limit = int(content_limit)
        self._transport = transport or self._http_transport

    @staticmethod
    def _http_transport(
        endpoint: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            body = exc.read(4096).decode("utf-8", errors="replace")
            if exc.code in {429, 432, 433}:
                raise ProviderRateLimitError(f"Tavily usage limit error ({exc.code}): {body}") from exc
            raise ProviderError(f"Tavily HTTP error ({exc.code}): {body}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutError("Tavily request timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeoutError("Tavily request timed out") from exc
            raise ProviderError(f"Tavily network error: {exc.reason}") from exc

        if len(raw) > MAX_RESPONSE_BYTES:
            raise MalformedResponseError("Tavily response exceeded the configured size limit")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MalformedResponseError("Tavily returned invalid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise MalformedResponseError("Tavily response must be a JSON object")
        return decoded

    def search(self, request: SearchRequest) -> SearchResponse:
        if not isinstance(request, SearchRequest):
            raise TypeError("request must be a SearchRequest")
        api_key = os.environ.get("TAVILY_API_KEY", "").strip()
        if not api_key:
            raise MissingCredentialError("TAVILY_API_KEY is not set")

        payload: dict[str, Any] = {
            "query": request.query,
            "search_depth": request.depth.value,
            "max_results": request.max_results,
            "topic": "general",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_usage": True,
        }
        if request.include_domains:
            payload["include_domains"] = list(request.include_domains)
        if request.exclude_domains:
            payload["exclude_domains"] = list(request.exclude_domains)
        if request.time_range is not None:
            payload["time_range"] = request.time_range.value

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        started = time.monotonic()
        try:
            raw = self._transport(self.endpoint, payload, headers, self.timeout_seconds)
        except ProviderError as exc:
            message = _safe_text(exc, api_key)
            raise type(exc)(message) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutError("Tavily request timed out") from exc
        except Exception as exc:
            raise ProviderError(f"Tavily request failed: {_safe_text(exc, api_key)}") from exc
        duration = round(time.monotonic() - started, 3)

        return self._normalize(raw, request, duration, api_key)

    def _normalize(
        self,
        raw: Mapping[str, Any],
        request: SearchRequest,
        duration: float,
        api_key: str,
    ) -> SearchResponse:
        if not isinstance(raw, Mapping):
            raise MalformedResponseError("Tavily response must be an object")
        raw_results = raw.get("results")
        if not isinstance(raw_results, list):
            raise MalformedResponseError("Tavily response is missing a results list")

        normalized: list[SearchResult] = []
        for item in raw_results[: request.max_results]:
            if not isinstance(item, Mapping):
                raise MalformedResponseError("Tavily returned a malformed result")
            title = item.get("title")
            url = item.get("url")
            content = item.get("content")
            score = item.get("score")
            if not isinstance(title, str) or not isinstance(url, str) or not isinstance(content, str):
                raise MalformedResponseError("Tavily result fields title, url, and content must be strings")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise MalformedResponseError("Tavily result URL must use HTTP or HTTPS")
            if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float))):
                raise MalformedResponseError("Tavily result score must be numeric or null")
            normalized.append(
                SearchResult(
                    title=_safe_text(title, api_key)[:500],
                    url=_safe_text(url, api_key)[:2_048],
                    content=_safe_text(content, api_key)[: self.content_limit],
                    score=float(score) if score is not None else None,
                    provider=self.name,
                    query=request.query,
                )
            )

        usage = raw.get("usage")
        credits: int | float | None = None
        if usage is not None:
            if not isinstance(usage, Mapping):
                raise MalformedResponseError("Tavily usage metadata must be an object")
            raw_credits = usage.get("credits")
            if raw_credits is not None:
                if isinstance(raw_credits, bool) or not isinstance(raw_credits, (int, float)):
                    raise MalformedResponseError("Tavily usage credits must be numeric")
                credits = raw_credits

        request_id = raw.get("request_id")
        if request_id is not None and not isinstance(request_id, str):
            raise MalformedResponseError("Tavily request_id must be a string or null")
        return SearchResponse(
            query=request.query,
            provider=self.name,
            depth=request.depth,
            results=tuple(normalized),
            duration_seconds=duration,
            credits=credits,
            provider_request_id=_safe_text(request_id, api_key) if request_id else None,
        )
