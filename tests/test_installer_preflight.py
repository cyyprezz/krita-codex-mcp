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
    compatibility_status = "compatible"
    krita_version = "5.3.2.1"
    compatibility_issues: list[dict[str, object]] = []
    calls: list[tuple[str, dict[str, object]]] = []

    def __init__(self, config) -> None:
        self.config = config

    def _compatibility_payload(self) -> dict[str, object]:
        return {
            "profile_version": 1,
            "krita_version": self.krita_version,
            "status": self.compatibility_status,
            "evaluation_complete": True,
            "validation": (
                "tested_build"
                if self.krita_version == "5.3.2.1"
                else "capability_checked"
            ),
            "capabilities": {"native_paint_line": True},
            "issues": list(self.compatibility_issues),
        }

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
        if operation == "document" and arguments.get("action") == "compatibility":
            return {
                **common,
                "runtime_compatibility": self._compatibility_payload(),
            }
        if operation == "layers" and arguments.get("action") == "create":
            kind = arguments.get("kind")
            layer_id = "probe-group" if kind == "grouplayer" else "probe-paint"
            return {**common, "affected_layer_id": layer_id}
        if operation == "layers" and arguments.get("action") == "reorder":
            return {
                **common,
                "affected_layer": {"parent_id": arguments.get("parent_id")},
            }
        if operation == "draw":
            return {**common, "verified_content_change": True}
        if operation == "state":
            return {
                **common,
                "runtime_compatibility": self._compatibility_payload(),
            }
        if operation == "preview":
            return {**common, "png_base64": base64.b64encode(PNG_SIGNATURE).decode("ascii")}
        if operation == "save_export":
            Path(str(arguments["png_path"])).write_bytes(PNG_SIGNATURE)
            Path(str(arguments["kra_path"])).write_bytes(b"KRA")
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
        _FakeBridgeAPI.compatibility_status = "compatible"
        _FakeBridgeAPI.krita_version = "5.3.2.1"
        _FakeBridgeAPI.compatibility_issues = []

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
            result = run_live_preflight(self.paths, __version__)
        self.assertEqual(result.status, "compatible")
        self.assertEqual(result.krita_version, "5.3.2.1")
        actions = [
            arguments.get("action")
            for operation, arguments in _FakeBridgeAPI.calls
            if operation == "document"
        ]
        self.assertIn("close", actions)
        self.assertIn("activate", actions)
        self.assertEqual(list(self.allowed.glob(".krita-codex-preflight-*.png")), [])
        self.assertEqual(list(self.allowed.glob(".krita-codex-preflight-*.kra")), [])

    def test_unknown_krita_version_passes_when_capabilities_are_complete(self) -> None:
        _FakeBridgeAPI.krita_version = "6.0.0"
        with patch(
            "krita_codex_mcp.installer.preflight.BridgeAPI",
            _FakeBridgeAPI,
        ):
            result = run_live_preflight(self.paths, __version__)
        self.assertEqual(result.status, "compatible")
        self.assertEqual(result.validation, "capability_checked")

    def test_optional_capability_warning_does_not_block_productive_use(self) -> None:
        _FakeBridgeAPI.compatibility_status = "limited"
        _FakeBridgeAPI.compatibility_issues = [
            {
                "severity": "warning",
                "code": "MISSING_NATIVE_PAINT_ELLIPSE",
                "feature": "native_paint_ellipse",
                "affected_tools": ["krita_draw"],
                "message": "Node.paintEllipse is unavailable.",
            }
        ]
        with patch(
            "krita_codex_mcp.installer.preflight.BridgeAPI",
            _FakeBridgeAPI,
        ):
            result = run_live_preflight(self.paths, __version__)
        self.assertEqual(result.status, "limited")
        self.assertEqual(result.issues[0]["affected_tools"], ["krita_draw"])

    def test_critical_capability_failure_blocks_live_check(self) -> None:
        _FakeBridgeAPI.compatibility_status = "incompatible"
        _FakeBridgeAPI.compatibility_issues = [
            {
                "severity": "critical",
                "code": "MISSING_DOCUMENT_PROJECTION",
                "feature": "document_projection",
                "affected_tools": ["krita_preview"],
                "message": "Document.projection is unavailable.",
            }
        ]
        with (
            patch("krita_codex_mcp.installer.preflight.BridgeAPI", _FakeBridgeAPI),
            self.assertRaisesRegex(InstallerError, "is incompatible"),
        ):
            run_live_preflight(self.paths, __version__)

    def test_live_preflight_rejects_loaded_bridge_version_mismatch(self) -> None:
        _FakeBridgeAPI.version = "0.0.0"
        with (
            patch("krita_codex_mcp.installer.preflight.BridgeAPI", _FakeBridgeAPI),
            self.assertRaisesRegex(InstallerError, "loaded in Krita"),
        ):
            run_live_preflight(self.paths, __version__)


if __name__ == "__main__":
    unittest.main()
