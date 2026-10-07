# Factory v1

Factory v1 is a reusable control repository for software-development work. It is not an application and does not contain product code. It will coordinate research, planning, architecture, implementation, testing, review, and deployment for independent repositories such as `../FindAndBook`.

The current release is a documentation-first scaffold. It defines boundaries and interfaces without committing the project to an orchestration framework or paid provider.

## What exists now

- Permanent operating rules in [AGENTS.md](AGENTS.md)
- Installed workflows under `.agents/skills`, tracked by `skills-lock.json`
- Role contracts for research, product analysis, architecture, and review
- A phased workflow and run contract for future orchestration
- Tool adapter contracts for search, browser, project, Git, model routing, and shell operations
- Product documentation templates
- Preferred defaults for application stack, deployment, and testing
- A cost policy and phased roadmap
- A dependency-free scaffold validator and a standard-library test suite with optional Playwright browser tests
- A read-only target project inspector under `tools/project`
- A controlled command runner and local run-artifact recorder
- Deterministic local browser verification with Playwright Chromium
- Provider-neutral web search with a dependency-free Tavily adapter
- SSRF-protected public HTML/text fetching and extraction
- Bounded source-backed research plans and citation bundles

## Repository map

| Path | Responsibility |
| --- | --- |
| `agents/` | Reusable role contracts and their boundaries |
| `orchestration/` | Workflow phases, handoffs, gates, and run records |
| `tools/` | Provider-neutral interfaces for external capabilities |
| `config/` | Safe defaults and future configuration rules |
| `templates/` | Files that may be copied into a target project with approval |
| `docs/` | Architecture, cost policy, decisions, target operation, and roadmap |
| `scripts/` | Small deterministic utilities for Factory itself |
| `tests/` | Checks for Factory contracts and utilities |

## Validate the scaffold

Factory v1 has no application dependencies. Run its checks with Python 3:

```text
python scripts/validate_factory.py
python -m unittest discover -s tests -v
```

## Target repositories

A product stays beside Factory as its own Git repository:

```text
software-factory/
|-- factory-v1/       # reusable engineering system
`-- FindAndBook/      # independent product repository
```

Factory resolves and validates a target path before operating on it. It never copies or nests the target's `.git` directory. See [Operating on target repositories](docs/target-projects.md).

For greenfield products, Factory defaults to Next.js, React, TypeScript, Tailwind CSS, Firebase, Vercel, Playwright, and Vitest. Existing projects keep their established stack unless a substantial technical reason supports a change. See [Preferred stack defaults](docs/stack-defaults.md).

## Current limits

Factory v1 does not yet have external-model synthesis, model routing, GitHub automation, deployment integration, dev-server lifecycle management, or a persistent orchestrator. Research evidence preparation is implemented, but Codex still performs source-backed synthesis using the researcher role contract. The workflow is intentionally not a crawler.

The inspector, controlled command runner, local artifact recorder, deterministic Playwright adapter, Tavily-backed search, public page fetcher, and bounded research workflow are implemented. External search requires `TAVILY_API_KEY`; normal tests remain fully mocked. Before the first product build, Factory should use these capabilities to establish the product brief, acceptance criteria, architecture, and explicit target write authority.
