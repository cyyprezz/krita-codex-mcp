from __future__ import annotations

import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

from krita_codex_mcp import __version__
from krita_codex_mcp.installer.manifest import verify_payload_root
from krita_codex_mcp.installer.resources import stage_distribution_payload
from scripts.verify_distribution import compare_wheel_payloads

REPO_ROOT = Path(__file__).resolve().parents[1]


class DistributionContractTests(unittest.TestCase):
    def test_wheel_declares_server_installer_protocol_and_plugin_resources(self) -> None:
        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = config["project"]
        self.assertEqual(project["dynamic"], ["version"])
        self.assertEqual(
            project["scripts"],
            {
                "krita-codex-mcp": "krita_codex_mcp.server:main",
                "krita-codex-install": "krita_codex_mcp.installer.cli:main",
            },
        )
        wheel = config["tool"]["hatch"]["build"]["targets"]["wheel"]
        self.assertEqual(
            wheel["packages"],
            ["src/krita_codex_mcp", "src/krita_codex_protocol"],
        )
        self.assertEqual(
            wheel["force-include"]["krita-plugin"],
            "krita_codex_mcp/_resources/krita_plugin",
        )

    def test_staged_payload_is_complete_and_manifested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage_distribution_payload(root, __version__)
            manifest = verify_payload_root(root, __version__)
            files = manifest["files"]
            self.assertIn("kritacodexbridge/extension.py", files)
            self.assertIn("kritacodexbridge/installation_info.py", files)
            self.assertIn("krita_codex_protocol/messages.py", files)
            self.assertIn("kritacodexbridge.desktop", files)
            self.assertFalse(any("scripts/" in path for path in files))

    def test_wheel_payload_comparison_ignores_distribution_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "direct.whl"
            second = root / "from-sdist.whl"
            with zipfile.ZipFile(first, "w") as archive:
                archive.writestr("krita_codex_mcp/server.py", b"payload")
                archive.writestr("example-1.dist-info/WHEEL", b"direct metadata")
            with zipfile.ZipFile(second, "w") as archive:
                archive.writestr("krita_codex_mcp/server.py", b"payload")
                archive.writestr("example-1.dist-info/WHEEL", b"rebuilt metadata")
            compare_wheel_payloads(first, second)

    def test_wheel_payload_comparison_rejects_changed_plugin_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "direct.whl"
            second = root / "from-sdist.whl"
            name = "krita_codex_mcp/_resources/krita_plugin/extension.py"
            with zipfile.ZipFile(first, "w") as archive:
                archive.writestr(name, b"direct")
            with zipfile.ZipFile(second, "w") as archive:
                archive.writestr(name, b"changed")
            with self.assertRaisesRegex(RuntimeError, "payload bytes differ"):
                compare_wheel_payloads(first, second)


if __name__ == "__main__":
    unittest.main()
