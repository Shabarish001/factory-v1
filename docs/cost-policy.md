# Cost policy

Factory routes work by reliability and cost, not by assigning every phase to a model.

## Routing order

1. Read existing repository evidence.
2. Run deterministic local tools such as Git, search in files, compilers, linters, test runners, and package-manager commands.
3. Use browser automation for observable UI behavior.
4. Use cheap search providers for external discovery when local evidence is insufficient.
5. Use local or low-cost models for extraction, summarization, and simple classification.
6. Use a strong model for difficult architecture, debugging, ambiguous synthesis, and code modification.

The first method that can produce a reliable answer wins. Cheap but unreliable output is not a saving because it creates review and repair work.

## Run accounting

Run records count search calls and provider-reported credits. Future records may add model class, input and output units, retries, and the reason for strong-model escalation. They must not store API keys or raw authorization headers.

## Current policy

Model providers remain disabled. Web search is configured for Tavily but only operates when `TAVILY_API_KEY` is supplied by the environment. Basic depth is the default; advanced depth must be requested explicitly. Human approval remains required before broadening paid-provider use or changing a run's cost limit.
