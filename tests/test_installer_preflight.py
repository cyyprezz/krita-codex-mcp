from __future__ import annotations

import base64
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from krita_codex_mcp import __version__
from krita_codex_mcp.installer.errors import InstallerError
from krita_codex_mcp.installer.manager import InstallationManager
from krita_codex_mcp.installer.paths import InstallPaths
from krita_codex_mcp.installer.preflight import (
    PNG_SIGNATURE,
    run_live_preflight,
    run_offline_preflight,
)
from krita_codex_protocol import PROTOCOL_VERSION


class _FakeBridgeAPI:
    version = __version__
    calls: list[tuple[str, dict[str, object]]] = []

    def __init__(self, config) -> None:
        self.config = config

    def execute(self, operation: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((operation, arguments))
        common = {
            "bridge_distribution_version": self.version,
            "bridge_protocol_version": PROTOCOL_VERSION,
        }
        if operation == "document" and arguments.get("action") == "list":
            return {**common, "active_document_id": "original"}
        if operation == "document" and arguments.get("action") == "create":
            return {**common, "affected_document_id": "temporary"}
        if operation == "preview":
            return {**common, "png_base64": base64.b64encode(PNG_SIGNATURE).decode("ascii")}
        if operation == "save_export":
            Path(str(arguments["png_path"])).write_bytes(PNG_SIGNATURE)
        return common


class InstallerPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        # Preflight payload tests do not own the machine's productive bridge port.
        self.reservation_patch = patch(
            "krita_codex_mcp.installer.manager.BridgePortReservation",
            side_effect=lambda _ports: nullcontext(),
        )
        self.reservation_patch.start()
        self.addCleanup(self.reservation_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.allowed = root / "allowed"
        self.allowed.mkdir()
        self.paths = InstallPaths.from_environment(
            {
                "APPDATA": str(root / "roaming"),
                "LOCALAPPDATA": str(root / "local"),
                "USERPROFILE": str(root / "profile"),
            }
        )
        InstallationManager(self.paths).install(allowed_roots=[self.allowed])
        _FakeBridgeAPI.calls = []
        _FakeBridgeAPI.version = __version__

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_offline_preflight_verifies_payload_hashes(self) -> None:
        config = run_offline_preflight(self.paths, __version__)
        self.assertEqual(config.allowed_roots, (self.allowed.resolve(),))
        (self.paths.plugin_dir / "extension.py").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(InstallerError, "does not match"):
            run_offline_preflight(self.paths, __version__)

    def test_live_preflight_compares_versions_and_cleans_up(self) -> None:
        with patch(
            "krita_codex_mcp.installer.preflight.BridgeAPI",
            _FakeBridgeAPI,
        ):
            run_live_preflight(self.paths, __version__)
        actions = [
            arguments.get("action")
            for operation, arguments in _FakeBridgeAPI.calls
            if operation == "document"
        ]
        self.assertIn("close", actions)
        self.assertIn("activate", actions)
        self.assertEqual(list(self.allowed.glob(".krita-codex-preflight-*.png")), [])

    def test_live_preflight_rejects_loaded_bridge_version_mismatch(self) -> None:
        _FakeBridgeAPI.version = "0.0.0"
        with (
            patch("krita_codex_mcp.installer.preflight.BridgeAPI", _FakeBridgeAPI),
            self.assertRaisesRegex(InstallerError, "loaded in Krita"),
        ):
            run_live_preflight(self.paths, __version__)


if __name__ == "__main__":
    unittest.main()
