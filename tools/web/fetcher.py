"""SSRF-safe HTTP transport and small HTML/text extractor."""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from tools.artifacts import RunArtifactRecorder
from tools.artifacts.recorder import redact_text

from .base import (
    FetchNetworkError,
    FetchTimeoutError,
    PageFetchError,
    PageFetchRequest,
    PageFetchResponse,
    PageTransport,
    RawPageResponse,
    RedirectError,
    ResponseTooLargeError,
    UnsupportedContentTypeError,
    UrlSecurityError,
)


USER_AGENT = "FactoryV1-PublicPageFetcher/1.0"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
ACCEPTED_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet")
IGNORED_TAGS = {"script", "style", "noscript", "template", "svg", "canvas", "nav", "header", "footer", "aside", "form"}
BLOCK_TAGS = {
    "address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "fieldset",
    "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "main",
    "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul",
}
ResolveHost = Callable[[str, int], Sequence[str]]
RequestOnce = Callable[[str, Sequence[str], PageFetchRequest], RawPageResponse]


@dataclass(frozen=True)
class ValidatedUrl:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


def _default_resolver(hostname: str, port: int) -> Sequence[str]:
    records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(record[4][0] for record in records))


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return address.is_global


def validate_public_url(url: str, resolver: ResolveHost = _default_resolver) -> ValidatedUrl:
    """Validate a public HTTP(S) URL and resolve every address before use."""

    if not isinstance(url, str) or not url or len(url) > 4_096:
        raise UrlSecurityError("URL must be a non-empty string no longer than 4096 characters")
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise UrlSecurityError("URL contains whitespace or control characters")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as exc:
        raise UrlSecurityError("URL is malformed") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise UrlSecurityError("Only http and https URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise UrlSecurityError("URLs containing embedded credentials are not allowed")
    if not hostname:
        raise UrlSecurityError("URL must include a hostname")
    hostname = hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(INTERNAL_SUFFIXES):
        raise UrlSecurityError("Local or internal hostnames are not allowed")

    try:
        literal = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is None and "." not in hostname:
        raise UrlSecurityError("Single-label hostnames are not allowed")

    effective_port = port or (443 if scheme == "https" else 80)
    if literal is not None:
        addresses = (str(literal),)
    else:
        try:
            addresses = tuple(dict.fromkeys(resolver(hostname, effective_port)))
        except (OSError, socket.gaierror) as exc:
            raise FetchNetworkError(f"DNS resolution failed for {hostname}") from exc
    if not addresses:
        raise FetchNetworkError(f"DNS resolution returned no addresses for {hostname}")
    if any(not _is_public_address(address) for address in addresses):
        raise UrlSecurityError("URL resolves to a non-public network address")

    network_url = urlunsplit((scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    return ValidatedUrl(network_url, hostname, effective_port, addresses)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


class HttpPageTransport:
    """Direct HTTP transport that pins validated IPs and checks every redirect."""

    def __init__(
        self,
        *,
        resolver: ResolveHost = _default_resolver,
        request_once: RequestOnce | None = None,
    ) -> None:
        self._resolver = resolver
        self._request_once = request_once or self._network_request_once

    def fetch(self, request: PageFetchRequest) -> RawPageResponse:
        current = request.url
        visited: set[str] = set()
        for redirect_count in range(request.max_redirects + 1):
            validated = validate_public_url(current, self._resolver)
            if validated.url in visited:
                raise RedirectError("Redirect loop detected")
            visited.add(validated.url)
            try:
                response = self._request_once(validated.url, validated.addresses, request)
            except (TimeoutError, socket.timeout) as exc:
                raise FetchTimeoutError("Webpage request timed out") from exc
            except PageFetchError:
                raise
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                raise FetchNetworkError(f"Webpage request failed: {redact_text(str(exc))}") from exc

            declared = _header(response.headers, "Content-Length")
            if declared is not None:
                try:
                    declared_bytes = int(declared)
                except ValueError:
                    declared_bytes = None
                if declared_bytes is not None and declared_bytes > request.max_response_bytes:
                    raise ResponseTooLargeError("Webpage Content-Length exceeds the configured byte limit")
            if len(response.body) > request.max_response_bytes:
                raise ResponseTooLargeError("Webpage response exceeded the configured byte limit")
            if response.http_status not in REDIRECT_STATUSES:
                return RawPageResponse(
                    requested_url=request.url,
                    final_url=validated.url,
                    http_status=response.http_status,
                    headers=response.headers,
                    body=response.body,
                    redirect_count=redirect_count,
                )
            location = _header(response.headers, "Location")
            if not location:
                raise RedirectError("Redirect response did not include a Location header")
            if redirect_count >= request.max_redirects:
                raise RedirectError("Webpage exceeded the configured redirect limit")
            current = urljoin(validated.url, location)
        raise RedirectError("Webpage exceeded the configured redirect limit")

    @staticmethod
    def _network_request_once(
        url: str,
        addresses: Sequence[str],
        request: PageFetchRequest,
    ) -> RawPageResponse:
        parsed = urlsplit(url)
        connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            timeout=request.connect_timeout_seconds,
        )
        pinned_address = addresses[0]
        original_create_connection = connection._create_connection

        def pinned_connection(address: Any, timeout: Any = None, source_address: Any = None, *args: Any, **kwargs: Any):
            return socket.create_connection(
                (pinned_address, address[1]),
                timeout=timeout,
                source_address=source_address,
            )

        connection._create_connection = pinned_connection
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html, application/xhtml+xml, text/plain;q=0.9",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if connection.sock is not None:
                connection.sock.settimeout(request.read_timeout_seconds)
            headers = {key: value for key, value in response.getheaders()}
            content_length = _header(headers, "Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    declared_length = None
                if declared_length is not None and declared_length > request.max_response_bytes:
                    raise ResponseTooLargeError("Webpage Content-Length exceeds the configured byte limit")
            body = response.read(request.max_response_bytes + 1)
            if len(body) > request.max_response_bytes:
                raise ResponseTooLargeError("Webpage response exceeded the configured byte limit")
            return RawPageResponse(url, url, response.status, headers, body)
        finally:
            connection._create_connection = original_create_connection
            connection.close()


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._ignored_depth = 0
        self._head_depth = 0
        self._title_depth = 0
        self._stack: list[tuple[str, bool, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {name.lower(): value for name, value in attrs}
        hidden = (
            "hidden" in attributes
            or str(attributes.get("aria-hidden", "")).lower() == "true"
            or "display:none" in str(attributes.get("style", "")).replace(" ", "").lower()
            or "visibility:hidden" in str(attributes.get("style", "")).replace(" ", "").lower()
        )
        ignored = tag in IGNORED_TAGS or hidden
        is_head = tag == "head"
        self._stack.append((tag, ignored, is_head))
        if ignored:
            self._ignored_depth += 1
        if is_head:
            self._head_depth += 1
        if tag == "title" and self._ignored_depth == 0:
            self._title_depth += 1
        if tag in BLOCK_TAGS and self._ignored_depth == 0 and self._head_depth == 0:
            self.text_parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in BLOCK_TAGS and self._ignored_depth == 0 and self._head_depth == 0:
            self.text_parts.append(" ")
        match = next((index for index in range(len(self._stack) - 1, -1, -1) if self._stack[index][0] == tag), None)
        if match is None:
            return
        removed = self._stack[match:]
        del self._stack[match:]
        self._ignored_depth -= sum(1 for _, ignored, _ in removed if ignored)
        self._head_depth -= sum(1 for _, _, is_head in removed if is_head)
        if tag == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._title_depth and self._ignored_depth == 0:
            self.title_parts.append(data)
        if self._ignored_depth == 0 and self._head_depth == 0:
            self.text_parts.append(data)


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _content_type(headers: Mapping[str, str]) -> tuple[str | None, str]:
    raw = _header(headers, "Content-Type")
    if not raw:
        return None, "utf-8"
    parts = [part.strip() for part in raw.split(";")]
    media_type = parts[0].lower()
    charset = "utf-8"
    for part in parts[1:]:
        if part.lower().startswith("charset="):
            charset = part.split("=", 1)[1].strip(' "\'') or "utf-8"
    return media_type, charset


def _decode(body: bytes, charset: str) -> str:
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _extract(body: bytes, media_type: str, charset: str) -> tuple[str | None, str]:
    source = _decode(body, charset)
    if media_type == "text/plain":
        return None, _normalize_whitespace(source)
    parser = _ReadableHTMLParser()
    try:
        parser.feed(source)
        parser.close()
    except (ValueError, AssertionError) as exc:
        raise PageFetchError("HTML extraction failed") from exc
    normalized_title = _normalize_whitespace(" ".join(parser.title_parts))
    title = normalized_title[:500] or None
    return title, _normalize_whitespace(" ".join(parser.text_parts))


class PublicPageFetcher:
    """Fetch and normalize one explicitly supplied public page."""

    def __init__(self, transport: PageTransport | None = None) -> None:
        self.transport = transport or HttpPageTransport()

    def fetch(
        self,
        request: PageFetchRequest,
        *,
        recorder: RunArtifactRecorder | None = None,
    ) -> PageFetchResponse:
        if not isinstance(request, PageFetchRequest):
            raise TypeError("request must be a PageFetchRequest")
        started = time.monotonic()
        try:
            raw = self.transport.fetch(request)
            media_type, charset = _content_type(raw.headers)
            if not 200 <= raw.http_status < 300:
                result = self._failure(
                    request,
                    started,
                    "http_error",
                    f"Webpage returned HTTP {raw.http_status}",
                    raw=raw,
                    content_type=media_type,
                )
            elif media_type not in ACCEPTED_CONTENT_TYPES:
                result = self._failure(
                    request,
                    started,
                    UnsupportedContentTypeError.code,
                    f"Unsupported content type: {media_type or 'missing'}",
                    raw=raw,
                    content_type=media_type,
                )
            else:
                title, extracted = _extract(raw.body, media_type, charset)
                truncated = len(extracted) > request.max_text_characters
                text = extracted[: request.max_text_characters]
                result = PageFetchResponse(
                    requested_url=request.url,
                    final_url=raw.final_url,
                    http_status=raw.http_status,
                    title=title,
                    content_type=media_type,
                    text=text,
                    text_length=len(text),
                    duration_seconds=round(time.monotonic() - started, 3),
                    status="completed",
                    error=None,
                    response_bytes=len(raw.body),
                    text_truncated=truncated,
                    redirect_count=raw.redirect_count,
                )
        except PageFetchError as exc:
            result = self._failure(request, started, exc.code, redact_text(str(exc)))
        if recorder is not None:
            recorder.record_fetch_event(result.to_dict())
        return result

    @staticmethod
    def _failure(
        request: PageFetchRequest,
        started: float,
        code: str,
        message: str,
        *,
        raw: RawPageResponse | None = None,
        content_type: str | None = None,
    ) -> PageFetchResponse:
        return PageFetchResponse(
            requested_url=request.url,
            final_url=raw.final_url if raw else None,
            http_status=raw.http_status if raw else None,
            title=None,
            content_type=content_type,
            text="",
            text_length=0,
            duration_seconds=round(time.monotonic() - started, 3),
            status="failed",
            error={"code": code, "message": message},
            response_bytes=len(raw.body) if raw else 0,
            text_truncated=False,
            redirect_count=raw.redirect_count if raw else 0,
        )


def fetch_page(
    url: str,
    *,
    recorder: RunArtifactRecorder | None = None,
    transport: PageTransport | None = None,
    max_text_characters: int = 50_000,
) -> PageFetchResponse:
    """Convenience interface for one public page; it never follows page links."""

    request = PageFetchRequest(url=url, max_text_characters=max_text_characters)
    return PublicPageFetcher(transport).fetch(request, recorder=recorder)
