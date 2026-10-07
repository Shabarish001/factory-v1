# Factory workflow

The full lifecycle is:

```text
IDEA
  -> research
  -> product understanding
  -> specification
  -> architecture
  -> implementation
  -> automated testing
  -> browser/UI verification
  -> code review
  -> fixes
  -> deployment
```

A run selects the smallest useful subset. A bug fix may begin with reproduction and move directly through implementation, tests, review, and fixes. A documentation change may need no browser phase.

## Phase gates

| Phase | Required entry | Exit artifact or evidence |
| --- | --- | --- |
| Inspect | Explicit target path | Target inventory, instructions, Git state |
| Research | A validated bounded plan for a question local evidence cannot answer | Source bundle, cited findings, and uncertainty |
| Product understanding | Idea or requested outcome | Problem, users, constraints |
| Specification | Agreed problem | Scope and acceptance criteria |
| Architecture | Stable requirements | Design and recorded decisions |
| Implementation | Approved scope and target write authority | Scoped target diff |
| Automated testing | Runnable change | Actual command results |
| Browser verification | User-visible behavior and runnable app | Screenshots, recording, or scripted assertions |
| Review | Diff plus available evidence | Findings and recommendation |
| Fixes | Actionable findings | Updated diff and repeated checks |
| Deployment | Passing gates and explicit approval | Deployment result and rollback reference |

## Failure behavior

A phase returns `completed`, `blocked`, `failed`, or `skipped`. It must include a reason for every status except `completed`. Orchestration stops before writes when the target is invalid, scope is unclear, the dirty-worktree policy blocks execution, credentials are missing, or required approval is absent.

Retries must be bounded and recorded. A retry may not silently switch provider, spend tier, branch, target, or deployment environment.
