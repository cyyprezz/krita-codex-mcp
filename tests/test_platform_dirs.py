from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from krita_codex_mcp.installer.paths import InstallPaths
from krita_codex_protocol import default_config_path
from krita_codex_protocol.platform_dirs import (
    uses_windows_layout,
    xdg_config_dir,
    xdg_pykrita_root,
    xdg_state_dir,
)


class XdgLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def test_defaults_under_home_when_xdg_variables_are_absent(self) -> None:
        env = {"HOME": str(self.home)}
        self.assertEqual(xdg_pykrita_root(env), self.home / ".local/share/krita/pykrita")
        self.assertEqual(xdg_config_dir(env), self.home / ".config/krita-codex-mcp")
        self.assertEqual(xdg_state_dir(env), self.home / ".local/state/krita-codex-mcp")

    def test_xdg_variables_take_priority(self) -> None:
        env = {
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.home / "data"),
            "XDG_CONFIG_HOME": str(self.home / "conf"),
            "XDG_STATE_HOME": str(self.home / "state"),
        }
        self.assertEqual(xdg_pykrita_root(env), self.home / "data/krita/pykrita")
        self.assertEqual(xdg_config_dir(env), self.home / "conf/krita-codex-mcp")
        self.assertEqual(xdg_state_dir(env), self.home / "state/krita-codex-mcp")

    def test_relative_xdg_variables_are_ignored(self) -> None:
        env = {
            "HOME": str(self.home),
            "XDG_DATA_HOME": "relative-data",
            "XDG_CONFIG_HOME": "relative-conf",
            "XDG_STATE_HOME": "relative-state",
        }
        self.assertEqual(xdg_pykrita_root(env), self.home / ".local/share/krita/pykrita")
        self.assertEqual(xdg_config_dir(env), self.home / ".config/krita-codex-mcp")
        self.assertEqual(xdg_state_dir(env), self.home / ".local/state/krita-codex-mcp")

    def test_windows_layout_needs_both_variables_outside_windows(self) -> None:
        fake_os = SimpleNamespace(name="posix", environ={})
        with patch("krita_codex_protocol.platform_dirs.os", fake_os):
            self.assertFalse(uses_windows_layout({"HOME": str(self.home)}))
            self.assertFalse(uses_windows_layout({"APPDATA": str(self.home / "a")}))
            self.assertTrue(uses_windows_layout({"APPDATA": "a", "LOCALAPPDATA": "l"}))

    def test_native_windows_always_keeps_windows_layout(self) -> None:
        fake_os = SimpleNamespace(name="nt", environ={})
        with patch("krita_codex_protocol.platform_dirs.os", fake_os):
            self.assertTrue(uses_windows_layout({}))

    def test_missing_home_uses_the_platform_home_directory(self) -> None:
        with patch.object(Path, "home", return_value=self.home):
            self.assertEqual(xdg_config_dir({}), self.home / ".config/krita-codex-mcp")


class InstallPathsXdgTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def test_install_paths_follow_xdg(self) -> None:
        with patch("krita_codex_mcp.installer.paths.uses_windows_layout", return_value=False):
            paths = InstallPaths.from_environment({"HOME": str(self.home)})
        self.assertEqual(paths.pykrita_root, self.home / ".local/share/krita/pykrita")
        self.assertEqual(paths.plugin_dir, paths.pykrita_root / "kritacodexbridge")
        self.assertEqual(paths.config_file, self.home / ".config/krita-codex-mcp/config.json")
        self.assertEqual(paths.codex_config_file, self.home / ".codex/config.toml")

    def test_windows_environment_keeps_windows_layout(self) -> None:
        env = {
            "APPDATA": str(self.home / "roaming"),
            "LOCALAPPDATA": str(self.home / "local"),
            "USERPROFILE": str(self.home / "user"),
        }
        paths = InstallPaths.from_environment(env)
        self.assertEqual(paths.pykrita_root, self.home / "roaming/krita/pykrita")
        self.assertEqual(paths.config_file, self.home / "local/krita-codex-mcp/config.json")


class DefaultConfigPathXdgTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def test_default_config_path_matches_installer_with_custom_xdg_config_home(self) -> None:
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "conf")}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("krita_codex_protocol.config.uses_windows_layout", return_value=False),
            patch("krita_codex_mcp.installer.paths.uses_windows_layout", return_value=False),
        ):
            expected = self.home / "conf/krita-codex-mcp/config.json"
            self.assertEqual(default_config_path(), expected)
            self.assertEqual(InstallPaths.from_environment().config_file, expected)

    def test_runtime_override_wins_over_xdg(self) -> None:
        override = self.home / "custom/config.json"
        env = {"HOME": str(self.home), "KRITA_CODEX_CONFIG": str(override)}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(default_config_path(), override)

    def test_plugin_log_uses_xdg_state_home(self) -> None:
        plugin_path = (
            Path(__file__).resolve().parents[1]
            / "krita-plugin/kritacodexbridge/logging_utils.py"
        )
        spec = importlib.util.spec_from_file_location("_platform_dirs_log_test", plugin_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        env = {"HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")}
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(module, "uses_windows_layout", return_value=False),
        ):
            self.assertEqual(
                module.bridge_log_path(), self.home / "state/krita-codex-mcp/bridge.log"
            )


if __name__ == "__main__":
    unittest.main()
