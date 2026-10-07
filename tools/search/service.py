"""Provider registry and artifact-aware search facade."""

from __future__ import annotations

import time
from collections.abc import Mapping

from tools.artifacts import RunArtifactRecorder
from tools.artifacts.recorder import redact_text

from .base import SearchError, SearchProvider, SearchRequest, SearchResponse
from .tavily import TavilySearchProvider


class SearchService:
    """Dispatch provider-neutral requests without exposing adapter formats."""

    def __init__(self, providers: Mapping[str, SearchProvider], *, default_provider: str) -> None:
        self._providers = dict(providers)
        if default_provider not in self._providers:
            raise ValueError(f"Unknown default search provider: {default_provider}")
        self.default_provider = default_provider

    def search(
        self,
        request: SearchRequest,
        *,
        provider: str | None = None,
        recorder: RunArtifactRecorder | None = None,
    ) -> SearchResponse:
        provider_name = provider or self.default_provider
        adapter = self._providers.get(provider_name)
        if adapter is None:
            raise ValueError(f"Unknown search provider: {provider_name}")
        started = time.monotonic()
        try:
            response = adapter.search(request)
        except SearchError as exc:
            if recorder is not None:
                recorder.record_search_event(
                    {
                        "status": "failed",
                        "query": request.query,
                        "provider": provider_name,
                        "searchDepth": request.depth.value,
                        "resultCount": 0,
                        "durationSeconds": round(time.monotonic() - started, 3),
                        "usage": {"credits": None, "estimatedCost": None, "currency": None},
                        "results": [],
                        "error": redact_text(str(exc)),
                    }
                )
            raise
        if recorder is not None:
            recorder.record_search_event(response.to_dict())
        return response


def create_default_search_service() -> SearchService:
    """Build the current provider registry; callers depend only on SearchService."""

    tavily = TavilySearchProvider()
    return SearchService({tavily.name: tavily}, default_provider=tavily.name)
