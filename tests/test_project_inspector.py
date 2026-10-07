"""Tests for the read-only target project inspector."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.project.inspector import InspectorError, inspect_project, validate_target_path


class ProjectInspectorTests(unittest.TestCase):
    def test_rejects_factory_and_nested_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory = Path(directory) / "factory"
            factory.mkdir()
            target = factory / "nested"
            target.mkdir()

            with self.assertRaises(InspectorError):
                validate_target_path(factory, factory_root=factory)
            with self.assertRaises(InspectorError):
                validate_target_path(target, factory_root=factory)

    def test_rejects_missing_and_file_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory = Path(directory) / "factory"
            factory.mkdir()
            file_target = Path(directory) / "project.txt"
            file_target.write_text("not a project", encoding="utf-8")

            with self.assertRaises(InspectorError):
                validate_target_path(Path(directory) / "missing", factory_root=factory)
            with self.assertRaises(InspectorError):
                validate_target_path(file_target, factory_root=factory)

    def test_detects_node_project_and_preferred_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory = Path(directory) / "factory"
            target = Path(directory) / "project"
            factory.mkdir()
            target.mkdir()
            (target / "package.json").write_text(
                json.dumps(
                    {
                        "name": "fixture",
                        "packageManager": "pnpm@9.0.0",
                        "scripts": {"test": "vitest", "build": "next build"},
                        "dependencies": {
                            "next": "^15.0.0",
                            "react": "^19.0.0",
                            "firebase": "^11.0.0",
                        },
                        "devDependencies": {
                            "@playwright/test": "^1.0.0",
                            "tailwindcss": "^4.0.0",
                            "typescript": "^5.0.0",
                            "vitest": "^2.0.0",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (target / "tsconfig.json").write_text("{}", encoding="utf-8")
            (target / "README.md").write_text("fixture", encoding="utf-8")

            before = sorted(path.relative_to(target).as_posix() for path in target.rglob("*"))
            result = inspect_project(target, factory_root=factory)
            after = sorted(path.relative_to(target).as_posix() for path in target.rglob("*"))

            self.assertEqual(before, after)
            self.assertEqual(result["node"]["packageManager"], "pnpm")
            self.assertEqual(sorted(result["node"]["scripts"]), ["build", "test"])
            self.assertEqual(result["preferredDefaults"]["presentCount"], 7)
            self.assertTrue(all(result["preferredDefaults"]["detected"].values()))
            self.assertIn("package.json", result["projectFiles"])
            self.assertFalse(result["git"]["isRepository"])

    def test_reports_git_branch_and_dirty_summary_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory = Path(directory) / "factory"
            target = Path(directory) / "project"
            factory.mkdir()
            target.mkdir()
            (target / "README.md").write_text("fixture", encoding="utf-8")
            subprocess.run(["git", "-C", str(target), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(target),
                    "-c",
                    "user.name=Factory Test",
                    "-c",
                    "user.email=factory@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            (target / "untracked.txt").write_text("fixture", encoding="utf-8")

            result = inspect_project(target, factory_root=factory)

            self.assertTrue(result["git"]["isRepository"])
            self.assertFalse(result["git"]["workingTreeClean"])
            self.assertEqual(result["git"]["status"]["untracked"], ["untracked.txt"])
            self.assertIn("not clean", result["summary"])


if __name__ == "__main__":
    unittest.main()
