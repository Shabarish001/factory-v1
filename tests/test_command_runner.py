"""Tests for command policy and controlled execution."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.project.command_policy import CommandPolicy, PolicyLevel
from tools.project.inspector import InspectorError
from tools.project.runner import RunnerError, run_command


class CommandRunnerTests(unittest.TestCase):
    def _git_target(self, directory: str) -> tuple[Path, Path]:
        root = Path(directory)
        factory = root / "factory"
        target = root / "target"
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
        return factory, target

    def _test_module(self, target: Path, body: str) -> None:
        (target / "test_runner_fixture.py").write_text(body, encoding="utf-8")

    def test_classifies_capability_levels(self) -> None:
        policy = CommandPolicy()
        self.assertEqual(policy.classify(["git", "status"]).level, PolicyLevel.READ_ONLY)
        self.assertEqual(policy.classify(["npm", "run", "test"], ["test"]).level, PolicyLevel.BUILD_AND_TEST)
        self.assertEqual(policy.classify(["npm", "install", "lodash"]).level, PolicyLevel.DEPENDENCY_CHANGE)
        self.assertEqual(policy.classify(["git", "push"]).level, PolicyLevel.EXTERNAL_ACTION)
        self.assertEqual(policy.classify(["git", "reset", "--hard"]).level, PolicyLevel.DESTRUCTIVE)

    def test_runs_read_only_command_in_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._git_target(directory)
            before = sorted(path.relative_to(target).as_posix() for path in target.rglob("*"))

            result = run_command(target, ["git", "status", "--short"], factory_root=factory)

            after = sorted(path.relative_to(target).as_posix() for path in target.rglob("*"))
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["policy"]["level"], "READ_ONLY")
            self.assertEqual(result["exitCode"], 0)
            self.assertEqual(before, after)

    def test_rejects_non_git_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._git_target(directory)
            with self.assertRaises((InspectorError, RunnerError)):
                run_command(factory.parent / "not-a-repo", ["git", "status"], factory_root=factory)

            result = run_command(target, ["git", "-C", "..", "status"], factory_root=factory)
            self.assertEqual(result["status"], "policy_rejected")
            self.assertEqual(result["policy"]["level"], "DESTRUCTIVE")
            self.assertIn("outside", result["policy"]["reason"])

    def test_blocks_destructive_and_external_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._git_target(directory)
            destructive = run_command(target, ["git", "reset", "--hard"], factory_root=factory)
            external = run_command(target, ["git", "push"], factory_root=factory)

            self.assertEqual(destructive["status"], "policy_rejected")
            self.assertEqual(destructive["policy"]["level"], "DESTRUCTIVE")
            self.assertEqual(external["status"], "policy_rejected")
            self.assertEqual(external["policy"]["level"], "EXTERNAL_ACTION")

    def test_captures_output_filters_environment_and_reports_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._git_target(directory)
            self._test_module(
                target,
                """
import os
import sys
import unittest

class RunnerFixture(unittest.TestCase):
    def test_output(self):
        print(os.environ.get('FACTORY_VISIBLE', 'missing'))
        print(os.environ.get('FACTORY_SECRET', 'missing'), file=sys.stderr)

if __name__ == '__main__':
    unittest.main()
""",
            )
            os.environ["FACTORY_VISIBLE"] = "visible-value"
            os.environ["FACTORY_SECRET"] = "secret-value"
            try:
                result = run_command(
                    target,
                    [sys.executable, "-m", "unittest", "test_runner_fixture.py"],
                    requested_policy=PolicyLevel.BUILD_AND_TEST,
                    extra_environment_keys=["FACTORY_VISIBLE", "FACTORY_SECRET"],
                    factory_root=factory,
                )
            finally:
                os.environ.pop("FACTORY_VISIBLE", None)
                os.environ.pop("FACTORY_SECRET", None)

            self.assertEqual(result["status"], "completed")
            self.assertIn("visible-value", result["stdout"])
            self.assertIn("missing", result["stderr"])
            self.assertIn("FACTORY_SECRET", result["environment"]["omittedKeys"])
            self.assertNotIn("secret-value", result["stderr"])

    def test_reports_nonzero_process_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._git_target(directory)
            self._test_module(
                target,
                "import unittest\nclass F(unittest.TestCase):\n    def test_fail(self): self.fail('expected')\n",
            )
            result = run_command(
                target,
                [sys.executable, "-m", "unittest", "test_runner_fixture.py"],
                requested_policy=PolicyLevel.BUILD_AND_TEST,
                factory_root=factory,
            )
            self.assertEqual(result["status"], "process_failed")
            self.assertNotEqual(result["exitCode"], 0)

    def test_handles_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory, target = self._git_target(directory)
            self._test_module(
                target,
                "import time\nimport unittest\nclass F(unittest.TestCase):\n    def test_slow(self): time.sleep(1)\n",
            )
            result = run_command(
                target,
                [sys.executable, "-m", "unittest", "test_runner_fixture.py"],
                requested_policy=PolicyLevel.BUILD_AND_TEST,
                timeout_seconds=0.05,
                factory_root=factory,
            )
            self.assertEqual(result["status"], "timed_out")
            self.assertTrue(result["timedOut"])


if __name__ == "__main__":
    unittest.main()
