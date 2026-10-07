# Research workflow

## Plan format

```json
{
  "objective": "Understand official booking product and API capabilities",
  "queries": [
    "official booking product features",
    "official booking API documentation"
  ],
  "allowedDomains": ["example.com"],
  "blockedDomains": [],
  "maxSearches": 6,
  "maxResultsPerSearch": 5,
  "maxPages": 8,
  "searchDepth": "basic",
  "perPageContentLimit": 12000,
  "advancedJustification": null
}
```

Unknown fields, duplicate queries, secret-like query content, contradictory domains, missing advanced-search justification, and limits above `config/defaults.json` are rejected. Bounds are rejected rather than silently increased or clamped.

## Source bundle

The machine-readable bundle contains the objective and queries; completed sources with stable IDs, provenance, scores, fetched text, and fetch metadata; failed sources preserving their assigned IDs; citation mappings; search failures; and aggregate counts, credits, and duration.

```json
{
  "objective": "...",
  "queries": ["..."],
  "sources": [
    {
      "sourceId": "S1",
      "title": "Official product",
      "url": "https://example.com/product",
      "domain": "example.com",
      "searchQuery": "...",
      "searchScore": 0.92,
      "content": "Bounded fetched text...",
      "fetchStatus": "completed"
    }
  ],
  "failedSources": [],
  "citations": [
    {"sourceId": "S1", "title": "Official product", "url": "https://example.com/product"}
  ],
  "metadata": {
    "searchCount": 1,
    "pagesFetched": 1,
    "creditsUsed": 1.0,
    "durationSeconds": 0.5
  }
}
```

The default policy permits at most six queries, five results per query, eight fetched pages, 12,000 characters per page, 100,000 content characters per bundle, and a 500,000-byte stored bundle. Basic search is the default. Advanced search is accepted only with a plan justification.

## Researcher synthesis

The workflow stops at evidence preparation. Codex then follows `agents/researcher.md` to produce observed findings with `[S#]` citations, separate inferences and recommendations, uncertainties, and a source list. That Markdown may be stored as `research-result.md`; no external model API is involved.
