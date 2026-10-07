# Shell and testing adapter

Purpose: run approved deterministic commands in a known target directory and capture their results.

Expected input: executable and arguments as separate values, working directory, timeout, environment allowlist, output limit, and whether the command may write.

Expected output: exit code, duration, bounded stdout and stderr, timeout state, and artifact references.

The adapter should detect project-defined lint, type-check, unit, integration, and build commands instead of inventing replacements. It must avoid shell string interpolation when an argument array is possible and must never log secret environment values.
