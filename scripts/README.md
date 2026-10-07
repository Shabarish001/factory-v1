# Scripts

Scripts contain small deterministic Factory utilities. They should use the standard library when practical, accept explicit paths, and return non-zero on failure.

`validate_factory.py` checks the scaffold, JSON configuration, and installed skill references. It performs no network calls and does not inspect or modify sibling projects.
