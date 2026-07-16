from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from krita_codex_mcp import __version__
from krita_codex_mcp.installer.errors import InstallerError
from krita_codex_mcp.installer.manager import InstallationManager
from krita_codex_mcp.installer.manifest import MANIFEST_NAME, verify_installed_payload
from krita_codex_mcp.installer.paths import InstallPaths
from krita_codex_mcp.installer.transaction import SwapComponent


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        # Real socket semantics are covered by test_installer_ports; transaction tests must not
        # contend with a productive local Krita bridge.
        self.reservation_patch = patch(
            "krita_codex_mcp.installer.manager.BridgePortReservation",
            side_effect=lambda _ports: nullcontext(),
        )
        self.reservation_patch.start()
        self.addCleanup(self.reservation_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        environment = {
            "APPDATA": str(self.root / "roaming"),
            "LOCALAPPDATA": str(self.root / "local"),
            "USERPROFILE": str(self.root / "profile"),
        }
        self.paths = InstallPaths.from_environment(environment)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_fresh_install_requires_explicit_allowed_root(self) -> None:
        with self.assertRaisesRegex(InstallerError, "--allowed-root"):
            InstallationManager(self.paths).install(allowed_roots=[])
        self.assertFalse(self.paths.plugin_dir.exists())
        self.assertFalse(self.paths.config_file.exists())

    def test_install_stages_complete_versioned_payload_without_sensitive_data(self) -> None:
        InstallationManager(self.paths).install(allowed_roots=[self.allowed])
        manifest = verify_installed_payload(self.paths, __version__)
        self.assertEqual(manifest["distribution_version"], __version__)
        self.assertTrue((self.paths.plugin_dir / "extension.py").is_file())
        self.assertTrue((self.paths.protocol_dir / "messages.py").is_file())
        config = json.loads(self.paths.config_file.read_text(encoding="utf-8"))
        manifest_text = (self.paths.plugin_dir / MANIFEST_NAME).read_text(encoding="utf-8")
        self.assertNotIn(config["token"], manifest_text)
        self.assertNotIn(str(self.root), manifest_text)
        self.assertEqual(config["host"], "127.0.0.1")
        self.assertEqual(config["allowed_roots"], [str(self.allowed.resolve())])

    def test_repeat_install_and_update_preserve_configuration_and_remove_stale_files(self) -> None:
        manager = InstallationManager(self.paths)
        manager.install(allowed_roots=[self.allowed])
        previous = json.loads(self.paths.config_file.read_text(encoding="utf-8"))
        previous["custom_setting"] = "preserved"
        self.paths.config_file.write_text(json.dumps(previous), encoding="utf-8")
        stale = self.paths.plugin_dir / "obsolete.py"
        stale.write_text("obsolete", encoding="utf-8")

        manager.install(allowed_roots=[self.allowed])
        repeated = json.loads(self.paths.config_file.read_text(encoding="utf-8"))
        self.assertEqual(repeated["token"], previous["token"])
        self.assertEqual(repeated["allowed_roots"], previous["allowed_roots"])
        self.assertEqual(repeated["custom_setting"], "preserved")
        self.assertFalse(stale.exists())

        manager.update()
        updated = json.loads(self.paths.config_file.read_text(encoding="utf-8"))
        self.assertEqual(updated["token"], previous["token"])
        self.assertEqual(updated["allowed_roots"], previous["allowed_roots"])

    def test_migration_preserves_existing_allowed_roots_without_cli_value(self) -> None:
        self.paths.config_file.parent.mkdir(parents=True)
        existing = {
            "token": "synthetic-credential-value-for-tests-only",
            "host": "127.0.0.1",
            "port": 17641,
            "allowed_roots": [str(self.allowed.resolve())],
        }
        self.paths.config_file.write_text(json.dumps(existing), encoding="utf-8")
        self.paths.plugin_dir.mkdir(parents=True)
        (self.paths.plugin_dir / "legacy.py").write_text("legacy", encoding="utf-8")

        InstallationManager(self.paths).install(allowed_roots=[])

        migrated = json.loads(self.paths.config_file.read_text(encoding="utf-8"))
        self.assertEqual(migrated["token"], existing["token"])
        self.assertEqual(migrated["allowed_roots"], existing["allowed_roots"])
        self.assertFalse((self.paths.plugin_dir / "legacy.py").exists())
        verify_installed_payload(self.paths, __version__)

    def test_migration_rejects_changed_allowed_roots(self) -> None:
        second = self.root / "second"
        second.mkdir()
        manager = InstallationManager(self.paths)
        manager.install(allowed_roots=[self.allowed])
        with self.assertRaisesRegex(InstallerError, "preserve existing allowed roots"):
            manager.install(allowed_roots=[second])

    def _snapshot_installation(self) -> dict[str, bytes]:
        snapshot: dict[str, bytes] = {"config": self.paths.config_file.read_bytes()}
        for prefix, root in (
            ("plugin", self.paths.plugin_dir),
            ("protocol", self.paths.protocol_dir),
        ):
            for path in root.rglob("*"):
                if path.is_file():
                    snapshot[f"{prefix}/{path.relative_to(root).as_posix()}"] = path.read_bytes()
        snapshot["desktop"] = self.paths.desktop_file.read_bytes()
        return snapshot

    def test_failure_injection_at_every_swap_point_restores_previous_state(self) -> None:
        manager = InstallationManager(self.paths)
        manager.install(allowed_roots=[self.allowed])
        failure_points = (
            "after_swap_plugin",
            "after_swap_protocol",
            "after_swap_desktop",
            "after_swap_config",
            "after_validate",
        )
        for failure_point in failure_points:
            with self.subTest(failure_point=failure_point):
                manager.update()
                marker = self.paths.plugin_dir / "previous-only.txt"
                marker.write_text(failure_point, encoding="utf-8")
                previous = self._snapshot_installation()

                def fail(step: str, expected: str = failure_point) -> None:
                    if step == expected:
                        raise RuntimeError("injected failure")

                failing = InstallationManager(self.paths, fault_injector=fail)
                with self.assertRaisesRegex(
                    InstallerError, "previous installation was restored"
                ):
                    failing.update()
                self.assertEqual(self._snapshot_installation(), previous)

    def test_staging_failure_does_not_touch_existing_installation(self) -> None:
        manager = InstallationManager(self.paths)
        manager.install(allowed_roots=[self.allowed])
        previous = self._snapshot_installation()

        def fail(step: str) -> None:
            if step == "after_stage":
                raise RuntimeError("injected staging failure")

        with self.assertRaisesRegex(InstallerError, "before any files were replaced"):
            InstallationManager(self.paths, fault_injector=fail).update()
        self.assertEqual(self._snapshot_installation(), previous)

    def test_incomplete_rollback_retains_transaction_backup(self) -> None:
        manager = InstallationManager(self.paths)
        manager.install(allowed_roots=[self.allowed])

        def fail(step: str) -> None:
            if step == "after_swap_plugin":
                raise RuntimeError("injected install failure")

        with (
            patch.object(SwapComponent, "restore", side_effect=OSError("restore failed")),
            self.assertRaisesRegex(InstallerError, "rollback was incomplete"),
        ):
            InstallationManager(self.paths, fault_injector=fail).update()
        retained = list(self.paths.pykrita_root.glob(".kritacodex-install-*/backup"))
        self.assertTrue(retained)

    def test_uninstall_failure_restores_removed_components(self) -> None:
        manager = InstallationManager(self.paths)
        manager.install(allowed_roots=[self.allowed])
        previous = self._snapshot_installation()

        def fail(step: str) -> None:
            if step == "after_remove_protocol":
                raise RuntimeError("injected uninstall failure")

        with self.assertRaisesRegex(InstallerError, "previous installation was restored"):
            InstallationManager(self.paths, fault_injector=fail).uninstall()
        self.assertEqual(self._snapshot_installation(), previous)

    def test_uninstall_is_repeatable_and_keeps_config_by_default(self) -> None:
        manager = InstallationManager(self.paths)
        manager.install(allowed_roots=[self.allowed])
        previous_config = self.paths.config_file.read_bytes()

        manager.uninstall()
        manager.uninstall()

        self.assertFalse(self.paths.plugin_dir.exists())
        self.assertFalse(self.paths.protocol_dir.exists())
        self.assertFalse(self.paths.desktop_file.exists())
        self.assertEqual(self.paths.config_file.read_bytes(), previous_config)

    def test_explicit_uninstall_removes_config(self) -> None:
        manager = InstallationManager(self.paths)
        manager.install(allowed_roots=[self.allowed])
        manager.uninstall(remove_config=True)
        self.assertFalse(self.paths.config_file.exists())


if __name__ == "__main__":
    unittest.main()
