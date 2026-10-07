"""Read-only target project inspector for Factory v1.

The inspector deliberately has a narrow scope. It resolves one explicit target,
reads bounded metadata, and uses read-only Git commands. It never runs project
scripts, installs dependencies, writes files, or visits another repository.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any


MAX_PACKAGE_JSON_BYTES = 1_000_000
MAX_REPORTED_STATUS_FILES = 100
MAX_TOP_LEVEL_ENTRIES = 100
COMMAND_TIMEOUT_SECONDS = 10

IGNORED_STRUCTURE_DIRECTORIES = {
    ".git",
    ".next",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}

KNOWN_PROJECT_FILES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "requirements.txt",
    "AGENTS.md",
    "firebase.json",
    ".firebaserc",
    "firestore.rules",
    "firestore.indexes.json",
    "storage.rules",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "tailwind.config.js",
    "tailwind.config.cjs",
    "tailwind.config.ts",
    "tsconfig.json",
    "jsconfig.json",
}

IMPORTANT_DEPENDENCIES = {
    "@playwright/test",
    "@radix-ui/react-dialog",
    "firebase",
    "firebase-admin",
    "firebase-functions",
    "next",
    "playwright",
    "react",
    "react-dom",
    "tailwindcss",
    "typescript",
    "vitest",
}


class InspectorError(ValueError):
    """An expected, safe-to-report inspection error."""


def _factory_root() -> Path:
    return Path(__file__).resolve().parents[2]


def validate_target_path(target_path: str | os.PathLike[str], factory_root: Path | None = None) -> Path:
    """Resolve one target and reject unsafe Factory-local paths."""

    factory = (factory_root or _factory_root()).resolve()
    candidate = Path(target_path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    target = candidate.resolve()

    if not target.exists():
        raise InspectorError(f"Target does not exist: {target}")
    if not target.is_dir():
        raise InspectorError(f"Target is not a directory: {target}")
    if target == factory:
        raise InspectorError("Refusing to inspect Factory v1 itself")
    try:
        target.relative_to(factory)
    except ValueError:
        return target
    raise InspectorError(f"Target must be outside Factory v1: {target}")


def _run_git(target: Path, args: Sequence[str]) -> tuple[int, str, str]:
    git_environment = os.environ.copy()
    git_environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={target}",
                "-C",
                str(target),
                *args,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=git_environment,
            timeout=COMMAND_TIMEOUT_SECONDS,
            shell=False,
        )
    except FileNotFoundError:
        return 127, "", "git executable was not found"
    except subprocess.TimeoutExpired:
        return 124, "", "git command timed out"
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def _git_info(target: Path) -> dict[str, Any]:
    root_code, root_output, root_error = _run_git(target, ["rev-parse", "--show-toplevel"])
    if root_code != 0:
        return {
            "isRepository": False,
            "repositoryRoot": None,
            "branch": None,
            "workingTreeClean": None,
            "status": {"modified": [], "untracked": [], "other": [], "reportedCount": 0},
            "error": root_error or "Target is not a Git repository",
        }

    repository_root = Path(root_output).resolve()
    branch_code, branch, branch_error = _run_git(target, ["branch", "--show-current"])
    if branch_code != 0:
        branch = None
    if not branch:
        revision_code, revision, _ = _run_git(target, ["rev-parse", "--short", "HEAD"])
        branch = f"HEAD detached at {revision}" if revision_code == 0 else "HEAD detached"

    status_code, status_output, status_error = _run_git(
        target, ["status", "--porcelain=v1", "--untracked-files=all"]
    )
    modified: list[str] = []
    untracked: list[str] = []
    other: list[str] = []
    status_lines = status_output.splitlines() if status_output else []
    for line in status_lines:
        if len(line) < 4:
            other.append(line)
            continue
        code = line[:2]
        path = line[3:]
        if code == "??":
            untracked.append(path)
        elif code[0] != " " or code[1] != " ":
            modified.append(path)
        else:
            other.append(path)

    return {
        "isRepository": True,
        "repositoryRoot": str(repository_root),
        "branch": branch,
        "workingTreeClean": status_code == 0 and not status_lines,
        "status": {
            "modified": modified[:MAX_REPORTED_STATUS_FILES],
            "untracked": untracked[:MAX_REPORTED_STATUS_FILES],
            "other": other[:MAX_REPORTED_STATUS_FILES],
            "reportedCount": min(len(status_lines), MAX_REPORTED_STATUS_FILES),
            "totalCount": len(status_lines),
        },
        "error": status_error if status_code != 0 else (branch_error or None),
    }


def _read_package_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if path.stat().st_size > MAX_PACKAGE_JSON_BYTES:
            return None, f"package.json exceeds {MAX_PACKAGE_JSON_BYTES} bytes"
        with path.open("r", encoding="utf-8-sig") as stream:
            value = json.load(stream)
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"could not parse package.json: {exc}"
    if not isinstance(value, dict):
        return None, "package.json must contain a JSON object"
    return value, None


def _package_manager(package: dict[str, Any], files: dict[str, str]) -> str | None:
    declared = package.get("packageManager")
    if isinstance(declared, str) and declared:
        return declared.split("@", 1)[0]
    for filename, manager in (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("package-lock.json", "npm"),
    ):
        if filename in files:
            return manager
    return None


def _dependencies(package: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        values = package.get(section)
        if not isinstance(values, dict):
            continue
        for name, version in values.items():
            if name in IMPORTANT_DEPENDENCIES and isinstance(version, str):
                found[name] = version
    return dict(sorted(found.items()))


def _node_info(root: Path, files: dict[str, str]) -> dict[str, Any] | None:
    package_path = root / "package.json"
    package, error = _read_package_json(package_path)
    if package is None and error is None:
        return None

    package = package or {}
    dependencies = _dependencies(package)
    names = set(dependencies)
    framework: list[str] = []
    if "next" in names or any(name.startswith("next.config.") for name in files):
        framework.append("Next.js")
    if "react" in names or "react-dom" in names:
        framework.append("React")
    if "typescript" in names or "tsconfig.json" in files:
        framework.append("TypeScript")
    if "node" in str(package.get("engines", {})).lower() or package:
        framework.append("Node.js")

    return {
        "packageJson": {"present": True, "parseError": error},
        "packageManager": _package_manager(package, files),
        "scripts": package.get("scripts") if isinstance(package.get("scripts"), dict) else {},
        "importantDependencies": dependencies,
        "frameworkRuntime": list(dict.fromkeys(framework)),
    }


def _preferred_defaults(root: Path, files: dict[str, str], node: dict[str, Any] | None) -> dict[str, Any]:
    dependencies = set((node or {}).get("importantDependencies", {}))
    checks = {
        "nextjs": "next" in dependencies or any(name.startswith("next.config.") for name in files),
        "react": "react" in dependencies or "react-dom" in dependencies,
        "typescript": "typescript" in dependencies or "tsconfig.json" in files,
        "tailwind": "tailwindcss" in dependencies or any(name.startswith("tailwind.config.") for name in files),
        "firebase": (
            bool({"firebase", "firebase-admin", "firebase-functions"} & dependencies)
            or any(name in files for name in {"firebase.json", ".firebaserc", "firestore.rules", "storage.rules"})
        ),
        "vitest": "vitest" in dependencies,
        "playwright": bool({"playwright", "@playwright/test"} & dependencies),
    }
    return {"detected": checks, "presentCount": sum(checks.values()), "totalCount": len(checks)}


def _project_files(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    try:
        entries = sorted(root.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        return files
    for entry in entries:
        if not entry.is_file():
            continue
        name = entry.name
        lower = name.lower()
        if name in KNOWN_PROJECT_FILES or lower.startswith("readme") or lower.startswith("agents"):
            files[name] = "file"
        elif lower.startswith("tsconfig") and lower.endswith(".json"):
            files[name] = "file"
    return files


def _structure(root: Path) -> dict[str, Any]:
    entries: list[dict[str, str]] = []
    ignored: list[str] = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name.lower())
    except OSError as exc:
        return {"topLevel": [], "ignoredDirectories": [], "truncated": False, "error": str(exc)}
    for child in children:
        if child.is_dir() and child.name in IGNORED_STRUCTURE_DIRECTORIES:
            ignored.append(child.name)
            continue
        if len(entries) >= MAX_TOP_LEVEL_ENTRIES:
            continue
        entries.append({"name": child.name, "type": "directory" if child.is_dir() else "file"})
    return {
        "topLevel": entries,
        "ignoredDirectories": ignored,
        "truncated": len(children) - len(ignored) > MAX_TOP_LEVEL_ENTRIES,
    }


def inspect_project(target_path: str | os.PathLike[str], factory_root: Path | None = None) -> dict[str, Any]:
    """Inspect one target without writing to it or running project code."""

    target = validate_target_path(target_path, factory_root=factory_root)
    files = _project_files(target)
    node = _node_info(target, files)
    git = _git_info(target)
    summary = _summary(target, git, files, node)
    return {
        "schemaVersion": 1,
        "target": {"path": str(target)},
        "git": git,
        "structure": _structure(target),
        "projectFiles": files,
        "node": node,
        "preferredDefaults": _preferred_defaults(target, files, node),
        "summary": summary,
    }


def _summary(target: Path, git: dict[str, Any], files: dict[str, str], node: dict[str, Any] | None) -> str:
    if git["isRepository"]:
        state = "clean" if git["workingTreeClean"] else "not clean"
        git_text = f"Git repository on {git['branch']} ({state})"
    else:
        git_text = "not a Git repository"
    detected = _preferred_defaults(target, files, node)["detected"]
    defaults = ", ".join(name for name, present in detected.items() if present) or "none"
    return f"{target.name}: {git_text}; detected defaults: {defaults}."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect one external target repository without modifying it.")
    parser.add_argument("target", help="Target project path, for example ../FindAndBook")
    parser.add_argument(
        "--factory-root",
        type=Path,
        default=None,
        help="Override Factory root for tests or embedding; normally auto-detected",
    )
    parser.add_argument("--no-summary", action="store_true", help="Do not print the human summary to stderr")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = inspect_project(args.target, factory_root=args.factory_root)
    except InspectorError as exc:
        print(f"Factory inspector refused the target: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # Keep CLI failures concise while preserving a non-zero exit.
        print(f"Factory inspector failed: {exc}", file=sys.stderr)
        return 1

    if not args.no_summary:
        print(result["summary"], file=sys.stderr)
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
