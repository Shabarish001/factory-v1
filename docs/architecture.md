# Architecture

Factory v1 is a control repository around independent product repositories. Its design has four layers:

```text
User objective
    |
Orchestration: phases, gates, budget, run record
    |
Roles: research, product, architecture, review decisions
    |
Tool adapters: project, Git, shell, browser, search, model routing
    |
Independent target repository and external providers
```

The first executable project flow is intentionally small:

```text
project inspector
      |
command policy
      |
controlled runner
      |
structured result
      |
local run-artifact recorder
      |
deterministic Playwright verification
      |
provider-neutral search -> Tavily adapter
      |
selected public URL -> bounded page fetch/extract
      |
normalized source bundle -> Codex researcher synthesis
```

The inspector validates one target and identifies its available checks. The policy classifies an argument vector into a capability level. The runner applies configured limits and executes only enabled local levels. The local recorder stores redacted inspector, command, browser, search, fetch, and research evidence under Factory. Search callers use normalized contracts while Tavily-specific HTTP details stay in its adapter. The page fetcher reads only selected public URLs. The bounded research workflow validates budgets, deduplicates and ranks candidates, fetches selected pages, and emits stable citation metadata. Codex synthesis remains outside the deterministic tool layer.

## Dependency direction

Orchestration may use role contracts and tool interfaces. Roles may request tool capabilities but do not contain provider commands. Tool adapters do not decide product scope. A target repository does not depend on Factory code.

This direction keeps product repositories usable without Factory and allows a provider to change without rewriting role instructions.

## Data and artifacts

The local recorder creates `.factory-artifacts/<project-name>/<run-id>/` inside Factory. It stores run metadata, authorization capabilities, changed-path/hash metadata, the inspector result, bounded command events, browser evidence, and safe search/fetch metadata. Secret-bearing file contents, fetched page text, and authorization credentials are excluded. The directory is gitignored.

Product documents belong in the target only when they are part of the approved change. Temporary research and execution logs stay in Factory artifacts.

## Safety boundary

The target path is untrusted input. A project adapter canonicalizes it, requires the exact Git root and a clean initial working tree, and rejects Factory itself or any repository nested inside Factory. Read-only inspection comes before write access. A `TargetAuthorization` binds an immutable capability set to one active run and root. `PROJECT_WRITE` is exposed only through a traversal- and symlink-resistant text API; `DEPENDENCY_CHANGE` is exposed only through classified project-local package-manager operations. Shell, Git, network, upload, deployment, external, and destructive operations remain separate or blocked so authorization cannot silently expand.

## Deliberately absent

There is no agent runtime, queue, database, provider SDK, plugin framework, or deployment engine. Direct standard-library HTTP keeps Tavily behind the search contract and avoids coupling callers to a provider package.
