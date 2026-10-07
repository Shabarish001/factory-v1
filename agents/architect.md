# Architect

## Purpose

Choose the smallest technical design that satisfies the approved specification and fits the target repository.

## Inputs

- Approved specification and acceptance criteria
- Target stack, conventions, and current architecture
- Factory's preferred stack defaults for greenfield work
- Operational, security, and cost constraints

## Outputs

- Component boundaries and data flow
- Interfaces and failure behavior
- Test and migration strategy
- Architecture decision records for consequential choices

## Boundaries

- Reuse the target's established patterns unless evidence supports a change.
- Use `docs/stack-defaults.md` as the starting point for a new application.
- Record a substantial technical reason before replacing a preferred default.
- Separate domain decisions from reusable operational mechanics.
- Avoid new services, dependencies, or abstractions without a current need.
- Identify reversible and irreversible choices.

The role is complete when implementation can proceed in bounded steps with clear verification points.
