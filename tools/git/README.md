# Git and GitHub adapter

Purpose: expose local Git operations and optional GitHub collaboration through structured results.

Initial local capabilities should include status, revision, branch, diff summary, recent history, and changed files. Future write capabilities may create branches, commits, and worktrees only after target write authorization.

GitHub support should use `gh` when available for pull requests, checks, issues, and reviews. Network writes such as pushing, editing a pull request, or resolving a thread require explicit scope. The adapter never rewrites history or deletes work by default.
