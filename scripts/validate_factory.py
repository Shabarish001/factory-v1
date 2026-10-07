"""Validate the Factory v1 scaffold without third-party dependencies."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable


REQUIRED_FILES = (
    "AGENTS.md",
    "README.md",
    "skills-lock.json",
    "config/defaults.json",
    "agents/README.md",
    "orchestration/workflow.md",
    "orchestration/run-contract.md",
    "tools/README.md",
    "tools/project/inspector.py",
    "tools/project/command_policy.py",
    "tools/project/runner.py",
    "tools/project/authorization.py",
    "tools/project/writer.py",
    "tools/artifacts/recorder.py",
    "tools/browser/playwright_tool.py",
    "tools/search/base.py",
    "tools/search/service.py",
    "tools/search/tavily.py",
    "tools/web/base.py",
    "tools/web/fetcher.py",
    "tools/web/README.md",
    "tools/research/workflow.py",
    "tools/research/run.py",
    "tools/research/README.md",
    "requirements-browser.txt",
    "docs/architecture.md",
    "docs/target-projects.md",
    "docs/cost-policy.md",
    "docs/stack-defaults.md",
    "docs/roadmap.md",
    "docs/research-workflow.md",
)

REQUIRED_DIRECTORIES = (
    ".agents/skills",
    "agents",
    "orchestration",
    "tools",
    "config",
    "templates",
    "docs",
    "scripts",
    "tests",
)


def _load_json(path: Path, errors: list[str]) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"Invalid JSON in {path.name}: {exc}")
        return None


def _missing(root: Path, paths: Iterable[str], kind: str) -> list[str]:
    errors: list[str] = []
    for relative in paths:
        path = root / relative
        exists = path.is_file() if kind == "file" else path.is_dir()
        if not exists:
            errors.append(f"Missing required {kind}: {relative}")
    return errors


def validate_factory(root: Path) -> list[str]:
    """Return human-readable validation errors for a Factory repository."""

    root = root.resolve()
    errors = _missing(root, REQUIRED_FILES, "file")
    errors.extend(_missing(root, REQUIRED_DIRECTORIES, "directory"))

    defaults = _load_json(root / "config" / "defaults.json", errors)
    if isinstance(defaults, dict):
        if defaults.get("version") != 1:
            errors.append("config/defaults.json must declare version 1")
        paid_enabled = defaults.get("cost", {}).get("paidProvidersEnabled")
        if paid_enabled is not False:
            errors.append("Paid providers must be disabled in the initial scaffold")
        runner = defaults.get("runner")
        if not isinstance(runner, dict):
            errors.append("config/defaults.json must define runner policy")
        else:
            allowed = runner.get("allowedPolicies")
            if allowed != ["READ_ONLY", "BUILD_AND_TEST"]:
                errors.append("Runner defaults must allow only READ_ONLY and BUILD_AND_TEST")
            if runner.get("runAuthorizablePolicies") != ["DEPENDENCY_CHANGE"]:
                errors.append("Runner may authorize only DEPENDENCY_CHANGE per run")
        authorization = defaults.get("targetAuthorization")
        if not isinstance(authorization, dict):
            errors.append("config/defaults.json must define target authorization policy")
        elif (
            authorization.get("authorizableCapabilities")
            != ["READ_ONLY", "BUILD_AND_TEST", "DEPENDENCY_CHANGE", "PROJECT_WRITE"]
            or authorization.get("alwaysBlockedCapabilities")
            != ["EXTERNAL_ACTION", "DESTRUCTIVE"]
        ):
            errors.append("Target authorization capabilities do not match Factory safety policy")
        browser = (defaults.get("tools") or {}).get("browser")
        if not isinstance(browser, dict):
            errors.append("config/defaults.json must define browser policy")
        elif browser.get("enabled") is not True or browser.get("localOnly") is not True:
            errors.append("Browser policy must be enabled and restricted to local URLs")
        search = (defaults.get("tools") or {}).get("search")
        if not isinstance(search, dict):
            errors.append("config/defaults.json must define search policy")
        elif (
            search.get("enabled") is not True
            or search.get("provider") != "tavily"
            or search.get("defaultDepth") != "basic"
        ):
            errors.append("Search policy must enable Tavily with basic depth by default")
        public_fetch = (defaults.get("tools") or {}).get("publicFetch")
        if not isinstance(public_fetch, dict):
            errors.append("config/defaults.json must define public fetch policy")
        elif (
            public_fetch.get("enabled") is not True
            or public_fetch.get("allowedSchemes") != ["http", "https"]
            or public_fetch.get("persistFetchedText") is not False
        ):
            errors.append("Public fetch must allow only HTTP(S) and must not persist page text")
        research = defaults.get("research")
        if not isinstance(research, dict):
            errors.append("config/defaults.json must define research bounds")
        elif (
            research.get("enabled") is not True
            or research.get("maxSearches") != 6
            or research.get("maxResultsPerSearch") != 5
            or research.get("maxPages") != 8
            or research.get("defaultSearchDepth") != "basic"
            or research.get("requireAdvancedJustification") is not True
        ):
            errors.append("Research defaults must use conservative bounded basic search")
        stack_defaults = defaults.get("stackDefaults")
        if not isinstance(stack_defaults, dict):
            errors.append("config/defaults.json must define stackDefaults")
        else:
            expected_defaults = {
                ("frontend", "framework"): "nextjs",
                ("frontend", "language"): "typescript",
                ("frontend", "styling"): "tailwind-css",
                ("backend", "platform"): "firebase",
                ("backend", "database"): "firestore",
                ("deployment", "webFrontend"): "vercel",
                ("testing", "browser"): "playwright",
                ("testing", "unitAndIntegration"): "vitest",
            }
            for (section, key), expected in expected_defaults.items():
                section_defaults = stack_defaults.get(section)
                actual = (
                    section_defaults.get(key)
                    if isinstance(section_defaults, dict)
                    else None
                )
                if actual != expected:
                    errors.append(
                        f"stackDefaults.{section}.{key} must be {expected!r}"
                    )

    lock = _load_json(root / "skills-lock.json", errors)
    if isinstance(lock, dict):
        skills = lock.get("skills")
        if not isinstance(skills, dict) or not skills:
            errors.append("skills-lock.json must contain at least one installed skill")
        else:
            skills_root = (root / ".agents" / "skills").resolve()
            for name, metadata in sorted(skills.items()):
                if not isinstance(metadata, dict):
                    errors.append(f"Skill {name!r} has invalid lock metadata")
                    continue
                skill_path = metadata.get("skillPath")
                if not isinstance(skill_path, str):
                    errors.append(f"Skill {name!r} has no skillPath")
                    continue
                candidate = (skills_root / skill_path).resolve()
                try:
                    candidate.relative_to(skills_root)
                except ValueError:
                    errors.append(f"Skill {name!r} escapes .agents/skills")
                    continue
                if not candidate.is_file():
                    errors.append(f"Installed skill is missing: {name} ({skill_path})")

    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate_factory(root)
    if errors:
        print("Factory scaffold validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Factory scaffold validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
