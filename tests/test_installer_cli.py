from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from krita_codex_mcp.installer.cli import run


class InstallerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.environment = {
            "APPDATA": str(root / "roaming"),
            "LOCALAPPDATA": str(root / "local"),
            "USERPROFILE": str(root / "profile"),
        }
        self.codex_config = root / "profile" / ".codex" / "config.toml"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_codex_config_defaults_to_preview_only(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, self.environment, clear=False), redirect_stdout(output):
            result = run(["codex-config"])
        self.assertEqual(result, 0)
        self.assertIn("Preview only", output.getvalue())
        self.assertFalse(self.codex_config.exists())

    def test_codex_config_apply_requires_confirmation(self) -> None:
        output = io.StringIO()
        error = io.StringIO()
        with (
            patch.dict(os.environ, self.environment, clear=False),
            patch("builtins.input", return_value="no"),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            result = run(["codex-config", "--apply"])
        self.assertEqual(result, 1)
        self.assertIn("Proposed", output.getvalue())
        self.assertFalse(self.codex_config.exists())

    def test_codex_config_explicit_yes_applies_managed_block(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, self.environment, clear=False), redirect_stdout(output):
            result = run(["codex-config", "--apply", "--yes"])
        self.assertEqual(result, 0)
        self.assertTrue(self.codex_config.is_file())
        self.assertIn("krita-codex-mcp==", self.codex_config.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
