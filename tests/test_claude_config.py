from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from krita_codex_mcp import __version__
from krita_codex_mcp.installer.cli import run
from krita_codex_mcp.installer.paths import InstallPaths


class ClaudeConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.uvx = self.root / "uv tools" / "uvx.exe"
        self.uvx.parent.mkdir()
        self.uvx.touch()
        self.environment = {
            "APPDATA": str(self.root / "roaming"),
            "LOCALAPPDATA": str(self.root / "local"),
            "USERPROFILE": str(self.root / "profile"),
            "HOME": str(self.root / "profile"),
            "ANTHROPIC_API_KEY": "must-not-appear",
            "KRITA_CODEX_TOKEN": "must-not-appear-either",
        }

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        output, error = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch("shutil.which", return_value=str(self.uvx)),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            try:
                result = run(["claude-config", *arguments])
            except SystemExit as exc:
                result = int(exc.code)
        return result, output.getvalue(), error.getvalue()

    def test_code_preview_uses_same_pinned_server_and_absolute_paths(self) -> None:
        result, output, error = self.invoke()
        self.assertEqual(result, 0, error)
        server = json.loads(output)["mcpServers"]["krita"]
        self.assertEqual(server["type"], "stdio")
        self.assertEqual(server["command"], str(self.uvx))
        self.assertEqual(
            server["args"], ["--from", f"krita-codex-mcp=={__version__}", "krita-codex-mcp"]
        )
        expected = InstallPaths.from_environment(self.environment).config_file
        self.assertEqual(server["env"]["KRITA_CODEX_CONFIG"], str(expected))
        self.assertNotIn("must-not-appear", output)
        self.assertFalse(expected.parent.exists())

    def test_desktop_preview_and_add_json_entry_have_matching_launchers(self) -> None:
        result, output, error = self.invoke("--client", "desktop")
        self.assertEqual(result, 0, error)
        desktop = json.loads(output)["mcpServers"]["krita"]
        self.assertNotIn("type", desktop)
        result, output, error = self.invoke("--server-only")
        self.assertEqual(result, 0, error)
        code = json.loads(output)
        code.pop("type")
        self.assertEqual(code, desktop)

    def test_preview_preserves_existing_client_and_bridge_files_byte_for_byte(self) -> None:
        paths = InstallPaths.from_environment(self.environment)
        files = [self.root / "profile" / ".claude.json", paths.config_file]
        original = b'{"mcpServers":{"other":{"command":"keep-me"}},"token":"private"}\n'
        for file in files:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(original)
        result, _, error = self.invoke()
        self.assertEqual(result, 0, error)
        for file in files:
            self.assertEqual(file.read_bytes(), original)

    def test_preview_forwards_only_explicit_absolute_xdg_paths_and_config_override(self) -> None:
        self.environment.update(
            XDG_CONFIG_HOME=str(self.root / "config"),
            XDG_DATA_HOME=str(self.root / "data"),
            XDG_STATE_HOME=str(self.root / "state"),
            KRITA_CODEX_CONFIG=str(self.root / "custom bridge.json"),
        )
        result, output, error = self.invoke()
        self.assertEqual(result, 0, error)
        env = json.loads(output)["mcpServers"]["krita"]["env"]
        self.assertEqual(env, {k: self.environment[k] for k in (
            "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "KRITA_CODEX_CONFIG"
        )})

    def test_relative_xdg_values_are_ignored(self) -> None:
        self.environment["XDG_CONFIG_HOME"] = "relative/ignored"
        result, output, error = self.invoke()
        self.assertEqual(result, 0, error)
        self.assertNotIn("XDG_CONFIG_HOME", json.loads(output)["mcpServers"]["krita"]["env"])

    def test_relative_bridge_override_is_rejected(self) -> None:
        self.environment["KRITA_CODEX_CONFIG"] = "relative/config.json"
        result, _, error = self.invoke()
        self.assertEqual(result, 1)
        self.assertIn("absolute", error)

    def test_explicit_uvx_path_with_spaces_is_preserved_as_one_command(self) -> None:
        result, output, error = self.invoke("--uvx", str(self.uvx))
        self.assertEqual(result, 0, error)
        self.assertEqual(json.loads(output)["mcpServers"]["krita"]["command"], str(self.uvx))

    def test_unavailable_or_relative_uvx_fails_without_writing_configuration(self) -> None:
        result, _, error = self.invoke("--uvx", "uvx")
        self.assertEqual(result, 1)
        self.assertIn("absolute", error)
        result, _, error = self.invoke("--uvx", str(self.root / "missing.exe"))
        self.assertEqual(result, 1)
        self.assertIn("uvx", error)


if __name__ == "__main__":
    unittest.main()
