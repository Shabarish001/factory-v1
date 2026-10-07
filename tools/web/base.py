"""Provider-independent contracts for fetching one public webpage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_READ_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_MAX_TEXT_CHARACTERS = 50_000


class PageFetchError(RuntimeError):
    """Base class for safe-to-report fetch failures."""

    code = "fetch_error"


class UrlSecurityError(PageFetchError):
    code = "url_rejected"


class FetchTimeoutError(PageFetchError):
    code = "timeout"


class FetchNetworkError(PageFetchError):
    code = "network_error"


class RedirectError(PageFetchError):
    code = "redirect_error"


class ResponseTooLargeError(PageFetchError):
    code = "response_too_large"


class UnsupportedContentTypeError(PageFetchError):
    code = "unsupported_content_type"


@dataclass(frozen=True)
class PageFetchRequest:
    """One explicit, bounded public webpage request."""

    url: str
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS
    max_redirects: int = DEFAULT_MAX_REDIRECTS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_text_characters: int = DEFAULT_MAX_TEXT_CHARACTERS

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("url must be a non-empty string")
        if len(self.url) > 4_096:
            raise ValueError("url must not exceed 4096 characters")
        for name, value in (
            ("connect_timeout_seconds", self.connect_timeout_seconds),
            ("read_timeout_seconds", self.read_timeout_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 30:
                raise ValueError(f"{name} must be greater than 0 and no more than 30")
        if (
            isinstance(self.max_redirects, bool)
            or not isinstance(self.max_redirects, int)
            or not 0 <= self.max_redirects <= 10
        ):
            raise ValueError("max_redirects must be between 0 and 10")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or not 1 <= self.max_response_bytes <= 10_000_000
        ):
            raise ValueError("max_response_bytes must be between 1 and 10000000")
        if (
            isinstance(self.max_text_characters, bool)
            or not isinstance(self.max_text_characters, int)
            or not 1 <= self.max_text_characters <= 200_000
        ):
            raise ValueError("max_text_characters must be between 1 and 200000")
        object.__setattr__(self, "url", self.url.strip())


@dataclass(frozen=True)
class RawPageResponse:
    requested_url: str
    final_url: str
    http_status: int
    headers: Mapping[str, str]
    body: bytes
    redirect_count: int = 0


@dataclass(frozen=True)
class PageFetchResponse:
    requested_url: str
    final_url: str | None
    http_status: int | None
    title: str | None
    content_type: str | None
    text: str
    text_length: int
    duration_seconds: float
    status: str
    error: Mapping[str, str] | None
    response_bytes: int
    text_truncated: bool
    redirect_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "requestedUrl": self.requested_url,
            "finalUrl": self.final_url,
            "httpStatus": self.http_status,
            "title": self.title,
            "contentType": self.content_type,
            "text": self.text,
            "textLength": self.text_length,
            "durationSeconds": self.duration_seconds,
            "responseBytes": self.response_bytes,
            "textTruncated": self.text_truncated,
            "redirectCount": self.redirect_count,
            "error": dict(self.error) if self.error else None,
        }


class PageTransport(Protocol):
    """Transport boundary used by the public fetch capability."""

    def fetch(self, request: PageFetchRequest) -> RawPageResponse:
        """Fetch one URL, validating every redirect hop."""
