# Orchestration

This directory defines how Factory phases connect. It does not yet implement a scheduler or multi-agent runtime.

Orchestration is responsible for:

- validating the target and requested scope
- selecting only the phases required for a task
- passing explicit artifacts between phases
- enforcing approval, test, cost, and deployment gates
- recording outcomes and failures in a run record

It is not responsible for provider-specific commands, product business rules, or hidden retries. Those belong in tool adapters, the target repository, and explicit run policy.

See `workflow.md` for phases and `run-contract.md` for the future runner boundary.
