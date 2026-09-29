from __future__ import annotations

import unittest
from pathlib import Path
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
    def test_defaults_under_home_when_xdg_variables_are_absent(self) -> None:
        env = {"HOME": "/home/ada"}
        self.assertEqual(xdg_pykrita_root(env), Path("/home/ada/.local/share/krita/pykrita"))
        self.assertEqual(xdg_config_dir(env), Path("/home/ada/.config/krita-codex-mcp"))
        self.assertEqual(xdg_state_dir(env), Path("/home/ada/.local/state/krita-codex-mcp"))

    def test_xdg_variables_take_priority(self) -> None:
        env = {
            "HOME": "/home/ada",
            "XDG_DATA_HOME": "/data",
            "XDG_CONFIG_HOME": "/conf",
            "XDG_STATE_HOME": "/state",
        }
        self.assertEqual(xdg_pykrita_root(env), Path("/data/krita/pykrita"))
        self.assertEqual(xdg_config_dir(env), Path("/conf/krita-codex-mcp"))
        self.assertEqual(xdg_state_dir(env), Path("/state/krita-codex-mcp"))

    def test_relative_xdg_variable_is_ignored(self) -> None:
        env = {"HOME": "/home/ada", "XDG_CONFIG_HOME": "relative"}
        self.assertEqual(xdg_config_dir(env), Path("/home/ada/.config/krita-codex-mcp"))

    def test_windows_layout_needs_both_variables(self) -> None:
        with patch("os.name", "posix"):
            self.assertFalse(uses_windows_layout({"HOME": "/home/ada"}))
            self.assertFalse(uses_windows_layout({"APPDATA": "/a"}))
            self.assertTrue(uses_windows_layout({"APPDATA": "/a", "LOCALAPPDATA": "/l"}))


class InstallPathsXdgTests(unittest.TestCase):
    def test_install_paths_follow_xdg(self) -> None:
        with patch("os.name", "posix"):
            paths = InstallPaths.from_environment({"HOME": "/home/ada"})
        self.assertEqual(paths.pykrita_root, Path("/home/ada/.local/share/krita/pykrita"))
        self.assertEqual(
            paths.plugin_dir, Path("/home/ada/.local/share/krita/pykrita/kritacodexbridge")
        )
        self.assertEqual(paths.config_file, Path("/home/ada/.config/krita-codex-mcp/config.json"))
        self.assertEqual(paths.codex_config_file, Path("/home/ada/.codex/config.toml"))

    def test_windows_environment_keeps_windows_layout(self) -> None:
        env = {"APPDATA": "/w/roaming", "LOCALAPPDATA": "/w/local", "USERPROFILE": "/w/user"}
        paths = InstallPaths.from_environment(env)
        self.assertEqual(paths.pykrita_root, Path("/w/roaming/krita/pykrita"))
        self.assertEqual(paths.config_file, Path("/w/local/krita-codex-mcp/config.json"))


class DefaultConfigPathXdgTests(unittest.TestCase):
    def test_default_config_path_uses_xdg_config_home(self) -> None:
        env = {"HOME": "/home/ada", "XDG_CONFIG_HOME": "/conf"}
        with patch("os.name", "posix"), patch.dict("os.environ", env, clear=True):
            self.assertEqual(default_config_path(), Path("/conf/krita-codex-mcp/config.json"))

    def test_override_wins_over_xdg(self) -> None:
        env = {"HOME": "/home/ada", "KRITA_CODEX_CONFIG": "/custom/config.json"}
        with patch("os.name", "posix"), patch.dict("os.environ", env, clear=True):
            self.assertEqual(default_config_path(), Path("/custom/config.json"))


if __name__ == "__main__":
    unittest.main()
