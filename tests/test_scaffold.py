"""Tests for the dependency-free Factory scaffold validator."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.validate_factory import validate_factory


class ScaffoldValidationTests(unittest.TestCase):
    def test_repository_scaffold_is_valid(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(validate_factory(root), [])

    def test_empty_directory_reports_required_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            errors = validate_factory(Path(directory))

        self.assertIn("Missing required file: AGENTS.md", errors)
        self.assertIn("Missing required directory: tools", errors)


if __name__ == "__main__":
    unittest.main()
