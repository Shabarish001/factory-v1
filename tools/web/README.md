# Public webpage fetcher

The public page fetcher reads one explicitly supplied HTTP(S) page and returns bounded, normalized text. It is the provider-independent bridge between URL discovery and future research:

```text
search -> selected URL -> public page fetch -> researcher
```

## Interface

```python
from tools.web import fetch_page

result = fetch_page("https://www.example.com/product")
print(result.to_dict())
```

For custom limits, construct `PageFetchRequest` and call `PublicPageFetcher.fetch()`. `PageTransport` is the transport boundary; callers do not depend on the standard-library HTTP implementation.

The result contains requested/final URLs, HTTP status, title, media type, extracted text and length, byte count, redirect count, timing, truncation, and structured status/error metadata.

## Extraction

The standard-library `HTMLParser` is sufficient for this intentionally small capability, so no dependency was added. Extraction decodes the declared charset, removes scripts, styles, templates, forms, navigation/header/footer/aside regions, SVG/canvas content, and hidden elements, then collapses repeated whitespace. Plain text is normalized directly. Returned text is held in memory and capped at the request limit.

Only `text/html`, `application/xhtml+xml`, and `text/plain` are accepted. Binary, archive, executable, media, and other unsupported responses fail without content extraction.

## Network boundary

- Only explicit `http` and `https` URLs are accepted.
- Embedded credentials, localhost, loopback, private, link-local, reserved/non-global IPs, single-label names, and common internal DNS suffixes are rejected.
- Every DNS answer must be public. The transport connects to the validated address directly while preserving the hostname for TLS verification, reducing DNS-rebinding exposure.
- Every redirect is independently resolved and validated. Redirect loops and excessive redirects fail.
- Connection/read timeouts, redirects, declared `Content-Length`, actual response bytes, and extracted text are bounded.
- Requests use a fixed user-agent and do not send cookies, authorization, Factory environment values, or proxy-derived credentials.
- JavaScript is never executed, forms are never submitted, and links are never followed recursively.

## Artifacts

When an active `RunArtifactRecorder` is supplied, safe metadata is written to `research/fetch-events.jsonl`. URL query strings/fragments and complete fetched text are omitted. The returned text remains available to the caller in memory.

Tests use a scripted transport and resolver. The required suite performs no public network requests.
