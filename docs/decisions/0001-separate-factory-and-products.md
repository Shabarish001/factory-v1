# ADR 0001: Keep Factory separate from product repositories

- Status: accepted
- Date: 2026-09-22

## Context

Factory v1 must support several products with different stacks, histories, credentials, and release processes. Nesting product repositories inside Factory would blur ownership, complicate Git operations, and increase the chance of modifying the wrong project.

## Decision

Factory is an independent control repository. Products remain independent sibling repositories. Every run receives one explicit target path and validates its Git root before any operation.

## Consequences

- Factory can evolve without changing product history.
- Each product keeps its own instructions, branches, checks, and releases.
- The future project adapter must enforce path and repository boundaries.
- Cross-repository changes require separate, explicit scopes and run records.
