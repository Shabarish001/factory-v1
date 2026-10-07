# Web search adapter

Factory exposes web search through `SearchService`, `SearchRequest`, and normalized `SearchResponse` / `SearchResult` objects. Callers select a provider by name but never handle provider response formats. Tavily is the first adapter and uses Python's standard-library HTTP client, so it adds no package dependency.

## Interface

```python
from tools.search import SearchRequest, create_default_search_service

request = SearchRequest(
    "Playwright locator official documentation",
    max_results=5,
    include_domains=["playwright.dev"],
    time_range="month",
)
response = create_default_search_service().search(request)
print(response.to_dict())
```

`SearchRequest` accepts `query`, `max_results` (1-20), `depth` (`basic` or `advanced`), domain allow/block lists, and an optional `day`, `week`, `month`, or `year` time range. `basic` is always the default. A caller must explicitly request `advanced`.

The normalized response contains the query, provider, depth, result count, duration, provider request ID, credit/cost metadata, and results. Every result has `title`, `url`, bounded `content`, `score`, `provider`, and `query`.

## Tavily credential

Set the key only in the process environment before calling the adapter:

```powershell
$env:TAVILY_API_KEY = Read-Host -MaskInput "Tavily API key"
```

Factory reads only `TAVILY_API_KEY`. It does not accept credentials as search arguments, put them in request bodies, print them, or record authorization headers. Requests use HTTPS, a bounded timeout, capped responses, and explicit response validation.

## Cost behavior

Basic search is the cheapest supported default. Tavily currently reports one credit for basic and two for advanced; actual returned usage is normalized into `usage.credits`, while monetary cost remains `null` until a pricing policy exists. The tool never calls an LLM.

## Artifacts

Pass an active `RunArtifactRecorder` as `recorder=` to write `search/search-events.jsonl`. Events contain a redacted query, provider, depth, count, duration, usage, and result titles/URLs. Result snippets, credentials, request headers, and environment values are not persisted.

## Adding providers

Implement the `SearchProvider` protocol, normalize into `SearchResponse`, then register the adapter in `SearchService`. Research callers remain unchanged. Selected result URLs can be passed individually to `tools.web.fetch_page`; a future researcher can inspect that bounded text and cite sources without importing Tavily code.

Normal tests use injected fake transports and make no network requests. A live smoke check is optional: set `TAVILY_API_KEY` and run the interface example manually; it is intentionally absent from the required suite.
