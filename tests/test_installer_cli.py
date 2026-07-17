from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from krita_codex_mcp.installer.cli import run
from krita_codex_mcp.installer.preflight import LivePreflightResult


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

    def test_live_check_reports_capability_checked_unknown_version(self) -> None:
        output = io.StringIO()
        result_value = LivePreflightResult(
            krita_version="6.0.0",
            status="compatible",
            validation="capability_checked",
            profile_version=1,
            issues=(),
        )
        with (
            patch.dict(os.environ, self.environment, clear=False),
            patch(
                "krita_codex_mcp.installer.cli.run_live_preflight",
                return_value=result_value,
            ),
            redirect_stdout(output),
        ):
            result = run(["check"])
        self.assertEqual(result, 0)
        self.assertIn("Krita 6.0.0: compatible", output.getvalue())
        self.assertIn("Ready for productive use.", output.getvalue())

    def test_live_check_warns_without_blocking_optional_limitations(self) -> None:
        output = io.StringIO()
        error = io.StringIO()
        result_value = LivePreflightResult(
            krita_version="6.0.0",
            status="limited",
            validation="capability_checked",
            profile_version=1,
            issues=(
                {
                    "severity": "warning",
                    "code": "MISSING_NATIVE_PAINT_ELLIPSE",
                    "feature": "native_paint_ellipse",
                    "affected_tools": ["krita_draw"],
                    "message": "Node.paintEllipse is unavailable.",
                },
            ),
        )
        with (
            patch.dict(os.environ, self.environment, clear=False),
            patch(
                "krita_codex_mcp.installer.cli.run_live_preflight",
                return_value=result_value,
            ),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            result = run(["check"])
        self.assertEqual(result, 0)
        self.assertIn("with the reported limitations", output.getvalue())
        self.assertIn("MISSING_NATIVE_PAINT_ELLIPSE", error.getvalue())


if __name__ == "__main__":
    unittest.main()
