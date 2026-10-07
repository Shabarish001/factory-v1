"""Provider-neutral web search for Factory v1."""

from .base import (
    MalformedResponseError,
    MissingCredentialError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    SearchDepth,
    SearchError,
    SearchProvider,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchTimeRange,
)
from .service import SearchService, create_default_search_service
from .tavily import TavilySearchProvider

__all__ = [
    "MalformedResponseError",
    "MissingCredentialError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "SearchDepth",
    "SearchError",
    "SearchProvider",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
    "SearchService",
    "SearchTimeRange",
    "TavilySearchProvider",
    "create_default_search_service",
]
