# Factory v1 operating rules

These rules apply to every session working in this repository or using it to operate on another repository.

## Repository boundary

Factory v1 is a reusable engineering system, not a product application. Each target application remains an independent Git repository. Never nest, move, or absorb a target repository into Factory.

Before touching a target:

1. Resolve its absolute path and Git root.
2. Confirm it is not Factory v1 and is not inside Factory v1.
3. Inspect its instructions, files, current branch, Git status, and recent history.
4. Treat existing changes as user-owned. Do not overwrite or clean them.
5. Confirm the requested scope authorizes writes to that repository.

Never modify unrelated projects. A request concerning one target does not authorize changes to its siblings.

## Working method

1. Inspect the target project before changing it.
2. Research when external knowledge is required. Record sources and access dates.
3. Plan substantial work before implementation.
4. Read and use installed skills when their trigger matches the task.
5. Prefer deterministic tools and inexpensive methods over model calls.
6. Record important architecture choices in `docs/decisions/` or the target's decision directory.
7. Run real checks and tests. Never claim functionality based on inspection alone.
8. Keep changes limited to the requested outcome.
9. Never expose credentials in prompts, logs, artifacts, commits, screenshots, or output.
10. Never modify an unrelated project.
11. Treat every target application as an independent Git repository.

## Cost-aware execution

Use the lowest-cost method that can produce a reliable result:

1. Existing files, deterministic scripts, and local CLI tools
2. Linters, compilers, test runners, and browser automation
3. Local or inexpensive models for extraction, summarization, and simple classification
4. Strong models for architecture, difficult reasoning, debugging, and code changes

Do not use a model to simulate a command or test that can be run. Paid providers are disabled until configuration and explicit authorization exist. Explain any escalation to a stronger or paid model in the run record.

## Safety and evidence

- Use dry-run or read-only behavior for the first contact with a target.
- Validate command paths and working directories before executing writes.
- Do not upload source, screenshots, logs, or artifacts to an external service without explicit approval.
- Keep generated evidence in `.factory-artifacts/`; do not commit it by default.
- Report skipped or blocked checks. Do not silently treat them as passing.
- Deployment and external side effects always require explicit authorization.

## Architecture discipline

Roles own decisions and phase-specific reasoning. Tools own reusable operational mechanics and return structured results. Orchestration connects them and enforces gates. Keep provider details behind tool adapters and avoid a single service that controls the whole workflow.

Do not add an agent, provider, framework, or abstraction until a concrete workflow needs it.

## Preferred application stack

For a new product, use the defaults defined in `docs/stack-defaults.md`: Next.js with React, TypeScript, Tailwind CSS, shadcn/ui or Radix where useful, Firebase with Firestore, Firebase Authentication, Firebase Storage, and Firebase Cloud Functions when server-side logic is required, Vercel for web applications, Vitest for unit and integration tests, and Playwright for browser and end-to-end tests.

Inspect an existing target before applying these defaults. Do not migrate or replace its working stack merely to match Factory preferences. Recommend an alternative only when a substantial technical reason exists, and record that reason and its tradeoffs in an architecture decision.
