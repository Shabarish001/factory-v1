# Configuration

Configuration controls Factory behavior, never product behavior. `defaults.json` contains safe, provider-free defaults. A future runner may merge it with a gitignored `config/local.json` and explicit command-line values, in that order.

The `stackDefaults` section gives planning and architecture roles a consistent starting point for greenfield applications. It does not authorize dependencies to be installed and does not override the established stack of an existing target.

Rules for future configuration:

- Validate configuration before opening a target repository.
- Keep target paths explicit. Do not scan sibling directories automatically.
- Store provider names and feature flags here, but load credentials only from the process environment or an approved secret store.
- Never write secrets, tokens, or copied environment values to configuration files.
- Record the effective non-secret configuration in each run artifact.

No paid provider is enabled in the initial scaffold.
