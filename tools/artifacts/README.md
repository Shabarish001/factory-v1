# Local run artifacts

`RunArtifactRecorder` stores one deterministic run record under Factory v1. It never writes to the target repository.

Programmatic use:

```python
from tools.artifacts import RunArtifactRecorder
from tools.project.inspector import inspect_project
from tools.project.runner import run_command

with RunArtifactRecorder(factory_root, target_path) as run:
    run.record_inspector(inspect_project(target_path))
    result = run_command(target_path, ["git", "status", "--short"])
    run.record_command(result)
```

The context manager completes a successful run and records an exception as a failed run before re-raising it. Call `complete()` or `fail(reason)` directly when explicit lifecycle control is preferable.

Artifacts use this layout:

```text
.factory-artifacts/
`-- <sanitized-project-name>/
    `-- <run-id>/
        |-- run.json
        |-- authorization.json
        |-- project-changes.jsonl
        |-- inspector.json
        |-- commands.jsonl
        |-- browser/
        |   |-- browser-events.jsonl
        |   `-- screenshots/
        |-- search/
        |   `-- search-events.jsonl
        |-- research/
        |   |-- fetch-events.jsonl
        |   |-- research-plan.json
        |   |-- source-bundle.json
        |   `-- research-result.md  # only after Codex synthesis
        `-- summary.json
```

`commands.jsonl` contains one bounded event per line. `authorization.json` records the exact run, Git root, granted capabilities, and always-blocked capabilities. `project-changes.jsonl` records operation, repository-relative path, byte counts, and hashes—never complete source contents. Search events contain safe request/result metadata but no snippets or headers. Environment values are not stored.
