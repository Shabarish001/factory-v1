# Model router

Purpose: choose the least expensive model class that can reliably complete a reasoning task.

Expected input: task kind, sensitivity, context size, required capabilities, latency limit, quality requirement, and budget.

Expected output: selected route, reason, provider-neutral usage estimate, fallback order, and final measured usage.

Planned routes are deterministic or no-model, local model, low-cost API model, and strong model. Routing rules must be inspectable. They may not send private source to an external provider unless policy permits it. No model provider or API key is configured yet.
