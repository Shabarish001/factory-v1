# Project adapter

Purpose: inspect and safely operate on one explicit target repository.

Run it from the Factory root:

```text
python tools/project/inspector.py ../FindAndBook
```

The machine-readable inspection result is written to stdout as JSON. A concise summary is written to stderr. Use `--no-summary` when stdout and stderr are handled separately.

The inspector is read-only:

- It resolves one explicit target and rejects Factory itself or paths inside Factory.
- It uses only bounded directory reads and read-only Git commands.
- It does not install dependencies, run package scripts, start servers, or access another repository.
- It reports path safety errors without attempting a fallback target.

The interface is:

```text
inspect(targetPath) -> gitRoot, revision, branch, dirtyState,
                       instructionFiles, stack, packageManager,
                       availableChecks, warnings
```

The current inspector additionally reports bounded top-level structure, recognized project files, Node scripts and important dependencies, preferred-stack detections, and a concise summary.

## Controlled command runner

Run one command from the Factory root:

```text
python tools/project/runner.py ../FindAndBook --policy READ_ONLY -- git status --short
python tools/project/runner.py ../FindAndBook --policy BUILD_AND_TEST -- npm run test
```

The runner first inspects and validates the target, classifies the argument vector, and starts a process only when the classification is enabled by configuration. It never uses `shell=True`. The result is JSON on stdout and a concise summary on stderr.

The policy levels are:

- `READ_ONLY`: repository state, metadata, and version checks
- `BUILD_AND_TEST`: approved test, lint, type-check, and build commands
- `DEPENDENCY_CHANGE`: recognized dependency metadata changes, disabled by default
- `PROJECT_WRITE`: source or repository changes, disabled by default
- `EXTERNAL_ACTION`: pushes, deployment, cloud, or collaboration actions, disabled
- `DESTRUCTIVE`: reset, clean, deletion, path escape, and shell syntax, blocked

The runner passes only configured non-secret environment keys, bounds timeout and output size, captures stdout and stderr, and reports `policy_rejected`, `timed_out`, `process_failed`, or `completed`. The inspector supplies the target repository and detected package scripts; no project script is run during inspection.

## Target mutation authorization

Writes require an active run and one immutable authorization bound to that run's exact Git root:

```python
from tools.artifacts import RunArtifactRecorder
from tools.project import PolicyLevel, ProjectWriter, TargetAuthorization

with RunArtifactRecorder(factory_root, target_path) as run:
    authorization = TargetAuthorization(
        target_path,
        [
            PolicyLevel.READ_ONLY,
            PolicyLevel.BUILD_AND_TEST,
            PolicyLevel.DEPENDENCY_CHANGE,
            PolicyLevel.PROJECT_WRITE,
        ],
        recorder=run,
    )
    writer = ProjectWriter(authorization)
    writer.create_text("src/example.ts", "export const example = true;\n")
    previous = writer.read_text("src/example.ts")
    writer.replace_text("src/example.ts", "export const example = false;\n", expected_text=previous)
```

Authorization requires a clean target working tree at run start. `PROJECT_WRITE` permits only this repository-confined UTF-8 text interface; it does not authorize arbitrary write commands. Creates make parent directories as needed, and replacements read the current file first and can enforce an expected prior value. A file deletion additionally requires `confirm=True`. Absolute paths, traversal, `.git`, symlinks, nested Git repositories, Factory, and sibling repositories are rejected.

Pass the same authorization to `run_command(..., authorization=authorization)` for recognized `npm`, `pnpm`, or `yarn` dependency operations. `DEPENDENCY_CHANGE` permits project-local package metadata and lock-file changes; global installs, local path dependencies, working-directory overrides, shell syntax, and target escape remain blocked.

Neither capability authorizes deployment, Git push/pull, cloud access, collaboration actions, arbitrary shell execution, or destructive commands. `EXTERNAL_ACTION` and `DESTRUCTIVE` cannot be included in an authorization.
