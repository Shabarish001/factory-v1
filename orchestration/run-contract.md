# Run contract

A future project runner should accept one explicit request and produce one inspectable run directory. The interface should remain provider-neutral.

## Input

```json
{
  "objective": "Describe the requested outcome",
  "targetPath": "../ExampleProject",
  "mode": "dry-run",
  "phases": ["inspect", "specification", "architecture"],
  "capabilities": ["READ_ONLY", "BUILD_AND_TEST"],
  "limits": {
    "paidProviders": false,
    "maxStrongModelCalls": 0
  }
}
```

`targetPath`, `objective`, and `mode` are mandatory. Write mode and every capability above the read/build defaults must be explicit. A mutation authorization requires a clean working tree at run start, is immutable, is bound to this run ID and resolved target Git root, and cannot be reused for another target or after the run is finalized.

`PROJECT_WRITE` means controlled source modification through the project writer inside that one target. `DEPENDENCY_CHANGE` means controlled project-local package metadata and lock-file modification through classified package-manager commands. Neither capability implies deployment, Git push, cloud access, external actions, or destructive operations. `EXTERNAL_ACTION` and `DESTRUCTIVE` remain blocked.

## Result

```json
{
  "runId": "timestamp-random-suffix",
  "status": "completed",
  "target": {
    "path": "absolute-path",
    "gitRoot": "absolute-path",
    "revision": "commit-sha",
    "dirty": false
  },
  "phases": [],
  "artifacts": [],
  "cost": {
    "paidCalls": 0,
    "strongModelCalls": 0
  }
}
```

Each phase record contains its status, timestamps, inputs, outputs, commands, checks, and non-secret cost metadata. Command output may be stored separately when large. Secrets and raw environment dumps are never part of a run record.

The inspector, controlled runner, local artifact recorder, and target authorization implement the deterministic execution boundary. Phase selection and broader orchestration remain descriptive.
