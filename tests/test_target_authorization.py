"""Tests for run-bound target authorization and controlled project writes."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.artifacts import RunArtifactRecorder
from tools.project import (
    AuthorizationError,
    PolicyLevel,
    ProjectWriteError,
    ProjectWriter,
    TargetAuthorization,
)
from tools.project.runner import run_command


class TargetAuthorizationTests(unittest.TestCase):
    def _git_repository(self, path: Path) -> None:
        path.mkdir()
        (path / "README.md").write_text("original\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
        subprocess.run(
            [
                "git", "-C", str(path), "-c", "user.name=Factory Test",
                "-c", "user.email=factory@example.invalid", "commit", "-qm", "fixture",
            ],
            check=True,
        )

    def _roots(self, directory: str) -> tuple[Path, Path, Path]:
        root = Path(directory)
        factory = root / "factory"
        target = root / "target"
        sibling = root / "sibling"
        factory.mkdir()
        self._git_repository(target)
        self._git_repository(sibling)
        return factory, target, sibling

    @staticmethod
    def _process_success(*args, **kwargs):
        del args, kwargs
        return {
            "status": "completed",
            "exitCode": 0,
            "timedOut": False,
            "stdout": "",
            "stderr": "",
            "stdoutBytes": 0,
            "stderrBytes": 0,
            "stdoutTruncated": False,
            "stderrTruncated": False,
            "startedAt": "2026-01-01T00:00:00+00:00",
            "endedAt": "2026-01-01T00:00:01+00:00",
            "durationSeconds": 1.0,
        }

    def test_authorized_create_update_read_and_nested_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, _ = self._roots(directory)
            recorder = RunArtifactRecorder(factory, target)
            authorization = TargetAuthorization(
                target,
                [PolicyLevel.READ_ONLY, PolicyLevel.PROJECT_WRITE],
                recorder=recorder,
            )
            writer = ProjectWriter(authorization)

            created = writer.create_text("src/nested/example.txt", "first\n")
            self.assertEqual(writer.read_text("src/nested/example.txt"), "first\n")
            replaced = writer.replace_text(
                "src/nested/example.txt",
                "second\n",
                expected_text="first\n",
            )
            recorder.complete()

            self.assertEqual(created["operation"], "create")
            self.assertEqual(replaced["operation"], "replace")
            self.assertEqual((target / "src" / "nested" / "example.txt").read_text(), "second\n")

    def test_rejects_project_write_without_permission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, _ = self._roots(directory)
            recorder = RunArtifactRecorder(factory, target)
            authorization = TargetAuthorization(
                target,
                [PolicyLevel.READ_ONLY],
                recorder=recorder,
            )
            with self.assertRaisesRegex(AuthorizationError, "PROJECT_WRITE"):
                ProjectWriter(authorization).create_text("blocked.txt", "no")
            self.assertFalse((target / "blocked.txt").exists())
            recorder.complete()

    def test_dependency_change_requires_same_run_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, _ = self._roots(directory)
            without_authorization = run_command(
                target,
                ["npm", "install", "lodash"],
                requested_policy=PolicyLevel.DEPENDENCY_CHANGE,
                factory_root=factory,
            )
            self.assertEqual(without_authorization["status"], "policy_rejected")
            self.assertIn("explicit", without_authorization["policy"]["reason"])

            recorder = RunArtifactRecorder(factory, target)
            authorization = TargetAuthorization(
                target,
                [PolicyLevel.READ_ONLY, PolicyLevel.BUILD_AND_TEST],
                recorder=recorder,
            )
            denied = run_command(
                target,
                ["npm", "install", "lodash"],
                authorization=authorization,
            )
            self.assertEqual(denied["status"], "policy_rejected")
            recorder.complete()

    def test_authorized_dependency_change_is_classified_and_executed_in_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, _ = self._roots(directory)
            recorder = RunArtifactRecorder(factory, target)
            authorization = TargetAuthorization(
                target,
                [PolicyLevel.DEPENDENCY_CHANGE],
                recorder=recorder,
            )
            with patch(
                "tools.project.runner._execute_process",
                side_effect=self._process_success,
            ) as execute:
                result = run_command(
                    target,
                    ["npm", "install", "lodash"],
                    authorization=authorization,
                )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["policy"]["level"], "DEPENDENCY_CHANGE")
            self.assertEqual(result["authorization"]["runId"], recorder.run_id)
            self.assertEqual(execute.call_args.args[1], target.resolve())
            recorder.complete()

    def test_blocks_global_local_external_and_destructive_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, _ = self._roots(directory)
            recorder = RunArtifactRecorder(factory, target)
            authorization = TargetAuthorization(
                target,
                [PolicyLevel.DEPENDENCY_CHANGE, PolicyLevel.PROJECT_WRITE],
                recorder=recorder,
            )
            commands = (
                ["npm", "install", "--global", "typescript"],
                ["npm", "install", "--location", "global", "typescript"],
                ["npm", "install", "file:../sibling"],
                ["git", "push"],
                ["git", "reset", "--hard"],
            )
            with patch("tools.project.runner._execute_process") as execute:
                results = [
                    run_command(target, command, authorization=authorization)
                    for command in commands
                ]
            self.assertTrue(all(item["status"] == "policy_rejected" for item in results))
            execute.assert_not_called()
            recorder.complete()

    def test_rejects_path_traversal_absolute_escape_and_git_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, _ = self._roots(directory)
            recorder = RunArtifactRecorder(factory, target)
            authorization = TargetAuthorization(
                target,
                [PolicyLevel.PROJECT_WRITE],
                recorder=recorder,
            )
            writer = ProjectWriter(authorization)

            for path in ("../escape.txt", target.parent / "absolute.txt", ".git/config"):
                with self.subTest(path=path):
                    with self.assertRaises(ProjectWriteError):
                        writer.create_text(path, "blocked")
            self.assertFalse((target.parent / "escape.txt").exists())
            self.assertFalse((target.parent / "absolute.txt").exists())
            recorder.complete()

    def test_rejects_nested_repository_and_colon_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, _ = self._roots(directory)
            recorder = RunArtifactRecorder(factory, target)
            authorization = TargetAuthorization(
                target,
                [PolicyLevel.PROJECT_WRITE],
                recorder=recorder,
            )
            nested = target / "vendor"
            self._git_repository(nested)
            writer = ProjectWriter(authorization)
            with self.assertRaisesRegex(ProjectWriteError, "nested Git"):
                writer.create_text("vendor/changed.txt", "blocked")
            with self.assertRaisesRegex(ProjectWriteError, "Colon"):
                writer.create_text("README.md:stream", "blocked")
            recorder.complete()

    def test_rejects_dirty_target_at_authorization_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, _ = self._roots(directory)
            (target / "README.md").write_text("user change\n", encoding="utf-8")
            recorder = RunArtifactRecorder(factory, target)
            with self.assertRaisesRegex(AuthorizationError, "clean"):
                TargetAuthorization(
                    target,
                    [PolicyLevel.PROJECT_WRITE],
                    recorder=recorder,
                )
            recorder.complete()

    def test_rejects_factory_and_sibling_authorization_and_isolates_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, sibling = self._roots(directory)
            sibling_before = (sibling / "README.md").read_bytes()
            recorder = RunArtifactRecorder(factory, target)

            with self.assertRaises(AuthorizationError):
                TargetAuthorization(
                    factory,
                    [PolicyLevel.PROJECT_WRITE],
                    recorder=recorder,
                )
            with self.assertRaises(AuthorizationError):
                TargetAuthorization(
                    sibling,
                    [PolicyLevel.PROJECT_WRITE],
                    recorder=recorder,
                )

            authorization = TargetAuthorization(
                target,
                [PolicyLevel.PROJECT_WRITE],
                recorder=recorder,
            )
            writer = ProjectWriter(authorization)
            with self.assertRaises(ProjectWriteError):
                writer.create_text("../sibling/changed.txt", "blocked")
            writer.create_text("target-only.txt", "allowed")
            recorder.complete()

            self.assertEqual((sibling / "README.md").read_bytes(), sibling_before)
            self.assertFalse((sibling / "changed.txt").exists())

    def test_rejects_symlink_escape_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, _ = self._roots(directory)
            outside = Path(directory) / "outside"
            outside.mkdir()
            link = target / "linked"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Directory symlinks are not available")
            recorder = RunArtifactRecorder(factory, target)
            authorization = TargetAuthorization(
                target,
                [PolicyLevel.PROJECT_WRITE],
                recorder=recorder,
            )
            with self.assertRaisesRegex(ProjectWriteError, "Symlink"):
                ProjectWriter(authorization).create_text("linked/escape.txt", "blocked")
            self.assertFalse((outside / "escape.txt").exists())
            recorder.complete()

    def test_delete_requires_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, _ = self._roots(directory)
            recorder = RunArtifactRecorder(factory, target)
            authorization = TargetAuthorization(
                target,
                [PolicyLevel.PROJECT_WRITE],
                recorder=recorder,
            )
            writer = ProjectWriter(authorization)
            writer.create_text("remove-me.txt", "temporary")
            with self.assertRaisesRegex(AuthorizationError, "confirm=True"):
                writer.delete_file("remove-me.txt")
            self.assertTrue((target / "remove-me.txt").exists())
            deleted = writer.delete_file("remove-me.txt", confirm=True)
            self.assertEqual(deleted["operation"], "delete")
            self.assertFalse((target / "remove-me.txt").exists())
            recorder.complete()

    def test_records_authorization_and_change_metadata_without_contents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, _ = self._roots(directory)
            recorder = RunArtifactRecorder(factory, target)
            authorization = TargetAuthorization(
                target,
                [
                    PolicyLevel.READ_ONLY,
                    PolicyLevel.BUILD_AND_TEST,
                    PolicyLevel.DEPENDENCY_CHANGE,
                    PolicyLevel.PROJECT_WRITE,
                ],
                recorder=recorder,
            )
            ProjectWriter(authorization).create_text("secret.txt", "password=do-not-store")
            recorder.complete()

            authorization_data = json.loads(
                (recorder.run_directory / "authorization.json").read_text(encoding="utf-8")
            )
            changes_text = (recorder.run_directory / "project-changes.jsonl").read_text(
                encoding="utf-8"
            )
            summary = json.loads(recorder.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(authorization_data["runId"], recorder.run_id)
            self.assertEqual(authorization_data["targetRepositoryRoot"], str(target.resolve()))
            self.assertEqual(
                authorization_data["alwaysBlocked"],
                ["EXTERNAL_ACTION", "DESTRUCTIVE"],
            )
            self.assertIn('"path": "secret.txt"', changes_text)
            self.assertNotIn("do-not-store", changes_text)
            self.assertEqual(summary["projectChangeCount"], 1)
            self.assertTrue(summary["authorizationRecorded"])

    def test_refuses_blocked_capability_grants_and_closed_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target, _ = self._roots(directory)
            blocked_recorder = RunArtifactRecorder(factory, target)
            with self.assertRaisesRegex(AuthorizationError, "cannot be authorized"):
                TargetAuthorization(
                    target,
                    [PolicyLevel.EXTERNAL_ACTION],
                    recorder=blocked_recorder,
                )
            blocked_recorder.complete()

            recorder = RunArtifactRecorder(factory, target)
            authorization = TargetAuthorization(
                target,
                [PolicyLevel.PROJECT_WRITE],
                recorder=recorder,
            )
            with self.assertRaisesRegex(AttributeError, "immutable"):
                authorization.capabilities = frozenset({PolicyLevel.DESTRUCTIVE})
            with self.assertRaisesRegex(ValueError, "already has"):
                TargetAuthorization(
                    target,
                    [PolicyLevel.READ_ONLY],
                    recorder=recorder,
                )
            recorder.complete()
            with self.assertRaisesRegex(ValueError, "finalized"):
                ProjectWriter(authorization).create_text("late.txt", "blocked")
            self.assertFalse((target / "late.txt").exists())


if __name__ == "__main__":
    unittest.main()
