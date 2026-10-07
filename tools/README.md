# Tool adapters

Tools perform reusable operations. Roles decide why and when to use them. Orchestration applies scope, budget, and approval policy.

Every future adapter should accept explicit inputs and return a structured result with:

- `status`: `completed`, `blocked`, or `failed`
- `data`: provider-neutral output
- `evidence`: command, source, or artifact references
- `cost`: zero or measured usage without secret billing data
- `warnings`: partial results and safety concerns

Adapters must not mutate product state unless their interface names the mutation and the run has write authority. Provider credentials come from the environment or a secret store, never function arguments that may be logged.

Project inspection, controlled commands, artifacts, browser checks, provider-neutral search, safe public page fetching, and bounded source research are working integrations. The remaining category directories describe future contracts.
