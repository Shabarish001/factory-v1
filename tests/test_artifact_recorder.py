"""Tests for local Factory run artifacts."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.artifacts.recorder import RunArtifactRecorder, sanitize_project_name
from tools.project.inspector import inspect_project
from tools.project.runner import run_command


class RunArtifactRecorderTests(unittest.TestCase):
    def _roots(self, directory: str, target_name: str = "Example Project") -> tuple[Path, Path]:
        root = Path(directory)
        factory = root / "factory"
        target = root / target_name
        factory.mkdir()
        target.mkdir()
        return factory, target

    def _git_target(self, directory: str) -> tuple[Path, Path]:
        factory, target = self._roots(directory)
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
        return factory, target

    @staticmethod
    def _snapshot(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_creates_grouped_runs_with_unique_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._roots(directory, "My Product")
            first = RunArtifactRecorder(factory, target)
            second = RunArtifactRecorder(factory, target)

            self.assertNotEqual(first.run_id, second.run_id)
            self.assertEqual(first.project_name, "my-product")
            self.assertEqual(first.run_directory.parent.name, "my-product")
            self.assertTrue(first.run_path.is_file())
            self.assertTrue(first.commands_path.is_file())
            first.complete()
            second.complete()

    def test_integrates_inspector_and_runner_results_without_target_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._git_target(directory)
            before = self._snapshot(target)
            recorder = RunArtifactRecorder(factory, target)

            inspection = inspect_project(target, factory_root=factory)
            command = run_command(target, ["git", "status", "--short"], factory_root=factory)
            recorder.record_inspector(inspection)
            event = recorder.record_command(command)
            recorder.complete()

            after = self._snapshot(target)
            stored_inspector = json.loads(recorder.inspector_path.read_text(encoding="utf-8"))
            stored_command = json.loads(recorder.commands_path.read_text(encoding="utf-8"))
            summary = json.loads(recorder.summary_path.read_text(encoding="utf-8"))

            self.assertEqual(before, after)
            self.assertEqual(stored_inspector["result"]["git"]["branch"], inspection["git"]["branch"])
            self.assertEqual(stored_command["policy"]["level"], "READ_ONLY")
            self.assertEqual(event["status"], "completed")
            self.assertTrue(summary["inspectorRecorded"])
            self.assertEqual(summary["commandCount"], 1)
            self.assertEqual(summary["status"], "completed")

    def test_records_failed_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._roots(directory)
            recorder = RunArtifactRecorder(factory, target)
            recorder.fail("build failed")

            run = json.loads(recorder.run_path.read_text(encoding="utf-8"))
            summary = json.loads(recorder.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(run["status"], "failed")
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["failure"], "build failed")
            self.assertIsNotNone(run["timing"]["endedAt"])
            self.assertIsInstance(run["timing"]["durationSeconds"], float)

    def test_redacts_secrets_from_all_persisted_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._roots(directory)
            recorder = RunArtifactRecorder(factory, target)
            recorder.record_inspector(
                {
                    "metadata": {
                        "apiKey": "AIza123456789secret",
                        "note": "password=hunter2",
                    }
                }
            )
            recorder.record_command(
                {
                    "status": "process_failed",
                    "command": {"argv": ["tool", "--token", "ghp_123456789secret"]},
                    "policy": {"level": "READ_ONLY", "reason": "authorization=BearerValue"},
                    "stdout": "token=plain-secret",
                    "stderr": "Authorization: Bearer abcdefghijk",
                    "stdoutBytes": 18,
                    "stderrBytes": 38,
                    "environment": {"filtered": True, "values": {"PASSWORD": "never-store"}},
                }
            )
            recorder.fail("secret=final-secret")

            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in recorder.run_directory.iterdir()
                if path.is_file()
            )
            for secret in (
                "AIza123456789secret",
                "hunter2",
                "ghp_123456789secret",
                "BearerValue",
                "plain-secret",
                "abcdefghijk",
                "never-store",
                "final-secret",
            ):
                self.assertNotIn(secret, persisted)
            self.assertIn("[REDACTED]", persisted)

    def test_caps_stored_command_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._roots(directory)
            recorder = RunArtifactRecorder(factory, target, output_limit=16)
            event = recorder.record_command(
                {
                    "status": "completed",
                    "command": {"argv": ["git", "status"]},
                    "policy": {"level": "READ_ONLY"},
                    "stdout": "x" * 100,
                    "stderr": "y" * 100,
                    "stdoutBytes": 100,
                    "stderrBytes": 100,
                }
            )
            recorder.complete()

            self.assertEqual(event["stdout"]["storedBytes"], 16)
            self.assertEqual(event["stderr"]["storedBytes"], 16)
            self.assertTrue(event["stdout"]["truncated"])
            self.assertTrue(event["stderr"]["truncated"])

    def test_sanitizes_invalid_project_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._roots(directory)
            recorder = RunArtifactRecorder(factory, target, project_name="../../Bad Project\\Name")
            self.assertEqual(recorder.project_name, "bad-project-name")
            self.assertEqual(recorder.run_directory.parent.parent, factory / ".factory-artifacts")
            recorder.complete()

        self.assertEqual(sanitize_project_name("../../"), "project")
        self.assertEqual(sanitize_project_name("CON"), "project-con")


if __name__ == "__main__":
    unittest.main()
