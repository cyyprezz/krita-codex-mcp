from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from krita_codex_mcp import __version__
from krita_codex_mcp.installer.codex_config import (
    BEGIN_MARKER,
    END_MARKER,
    apply_codex_config_change,
    managed_block,
    plan_codex_config_change,
)
from krita_codex_mcp.installer.errors import InstallerError


class CodexConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / ".codex" / "config.toml"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_preview_is_checkout_free_and_exactly_version_pinned(self) -> None:
        change = plan_codex_config_change(self.path, "1.2.3")
        self.assertIn('command = "uvx"', change.preview)
        self.assertIn('"krita-codex-mcp==1.2.3"', change.preview)
        self.assertNotIn("cwd =", change.preview)
        self.assertFalse(self.path.exists())

    def test_generated_launcher_forwards_custom_xdg_paths(self) -> None:
        change = plan_codex_config_change(self.path, "1.2.3")
        server = tomllib.loads(change.updated)["mcp_servers"]["krita"]
        self.assertEqual(
            server.get("env_vars"),
            ["XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"],
        )

    def test_shipped_example_matches_generated_launcher(self) -> None:
        example = Path(__file__).resolve().parents[1] / ".codex" / "config.toml.example"
        self.assertEqual(
            tomllib.loads(example.read_text(encoding="utf-8")),
            tomllib.loads(managed_block(__version__)),
        )

    def test_apply_creates_managed_block_and_updates_it_atomically(self) -> None:
        self.path.parent.mkdir(parents=True)
        previous = '[features]\nexample = true\n'
        self.path.write_text(previous, encoding="utf-8")

        first = plan_codex_config_change(self.path, "1.2.3")
        apply_codex_config_change(self.path, first)
        installed = self.path.read_text(encoding="utf-8")
        self.assertEqual(installed.count(BEGIN_MARKER), 1)
        self.assertEqual(installed.count(END_MARKER), 1)
        self.assertIn("krita-codex-mcp==1.2.3", installed)
        self.assertEqual(
            self.path.with_name("config.toml.krita-codex-mcp.backup").read_text(
                encoding="utf-8"
            ),
            previous,
        )
        tomllib.loads(installed)

        second = plan_codex_config_change(self.path, "1.2.4")
        apply_codex_config_change(self.path, second)
        updated = self.path.read_text(encoding="utf-8")
        self.assertIn("krita-codex-mcp==1.2.4", updated)
        self.assertNotIn("krita-codex-mcp==1.2.3", updated)
        self.assertEqual(updated.count(BEGIN_MARKER), 1)

    def test_unmarked_conflict_is_never_replaced(self) -> None:
        self.path.parent.mkdir(parents=True)
        previous = '[mcp_servers.krita]\ncommand = "existing"\n'
        self.path.write_text(previous, encoding="utf-8")
        with self.assertRaisesRegex(InstallerError, "unmarked"):
            plan_codex_config_change(self.path, "1.2.3")
        self.assertEqual(self.path.read_text(encoding="utf-8"), previous)

    def test_external_change_between_preview_and_apply_is_never_overwritten(self) -> None:
        self.path.parent.mkdir(parents=True)
        previous = '[features]\nexample = true\n'
        external = '[features]\nexample = false\n'
        self.path.write_text(previous, encoding="utf-8")
        change = plan_codex_config_change(self.path, "1.2.3")

        self.path.write_text(external, encoding="utf-8")

        with self.assertRaisesRegex(InstallerError, "changed after the preview"):
            apply_codex_config_change(self.path, change)
        self.assertEqual(self.path.read_text(encoding="utf-8"), external)
        self.assertFalse(
            self.path.with_name("config.toml.krita-codex-mcp.backup").exists()
        )
        self.assertEqual(list(self.path.parent.glob(".config.toml.*")), [])

    def test_external_file_creation_after_preview_is_never_overwritten(self) -> None:
        change = plan_codex_config_change(self.path, "1.2.3")
        self.path.parent.mkdir(parents=True)
        external = '[features]\nexternal = true\n'
        self.path.write_text(external, encoding="utf-8")

        with self.assertRaisesRegex(InstallerError, "changed after the preview"):
            apply_codex_config_change(self.path, change)
        self.assertEqual(self.path.read_text(encoding="utf-8"), external)
        self.assertFalse(
            self.path.with_name("config.toml.krita-codex-mcp.backup").exists()
        )

    def test_only_managed_block_can_be_removed(self) -> None:
        first = plan_codex_config_change(self.path, "1.2.3")
        apply_codex_config_change(self.path, first)
        removal = plan_codex_config_change(self.path, "1.2.3", remove=True)
        apply_codex_config_change(self.path, removal)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "")
        with self.assertRaisesRegex(InstallerError, "No installer-managed"):
            plan_codex_config_change(self.path, "1.2.3", remove=True)

    def test_ambiguous_markers_fail_closed(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(BEGIN_MARKER + "\n" + BEGIN_MARKER + "\n", encoding="utf-8")
        with self.assertRaisesRegex(InstallerError, "ambiguous"):
            plan_codex_config_change(self.path, "1.2.3")

    def test_failed_post_write_validation_restores_atomic_backup(self) -> None:
        self.path.parent.mkdir(parents=True)
        previous = '[features]\nexample = true\n'
        self.path.write_text(previous, encoding="utf-8")
        change = plan_codex_config_change(self.path, "1.2.3")
        with (
            patch(
                "krita_codex_mcp.installer.codex_config._parse_toml",
                side_effect=[{}, InstallerError("injected validation failure")],
            ),
            self.assertRaisesRegex(InstallerError, "rolled back"),
        ):
            apply_codex_config_change(self.path, change)
        self.assertEqual(self.path.read_text(encoding="utf-8"), previous)


if __name__ == "__main__":
    unittest.main()
