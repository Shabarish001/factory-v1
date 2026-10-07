# Operating on target repositories

Factory and each product are peers on disk. For example:

```text
C:/work/software-factory/
|-- factory-v1/.git/
`-- FindAndBook/.git/
```

`FindAndBook` remains an independent repository. It is never added as a subdirectory, submodule, subtree, or copied Git directory inside Factory unless a future user explicitly chooses a different repository strategy.

## Future run sequence

Given the explicit target `../FindAndBook`, a project runner should:

1. Resolve the Factory root and target path to absolute canonical paths.
2. Reject the target if it equals Factory or sits below Factory.
3. Run `git -C <target> rev-parse --show-toplevel` and confirm the returned root matches the target repository.
4. Read target instruction files, manifest files, branch, status, and recent history.
5. Stop and report according to dirty-worktree policy before any write.
6. Build a plan that names allowed target paths and required checks.
7. Request write approval when the run began in dry-run mode.
8. Execute commands with the target as the working directory while storing run logs in Factory.
9. Test in the target using its own toolchain.
10. Leave commits, branches, and product documentation in the target repository only.

Factory must not search for sibling repositories automatically. The user or calling system supplies one target path, preventing accidental work on unrelated products.

## Credentials and artifacts

The target's `.env` files and secret stores remain in the target and are never copied to Factory. Command runners receive only allowlisted environment variables. Screenshots, recordings, and logs are reviewed for secrets before any external upload.

Factory v1 documents this flow but does not yet execute it. `FindAndBook` is not modified as part of the Factory scaffold.
