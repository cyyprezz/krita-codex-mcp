from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from krita_codex_mcp.installer.configuration import write_staged_config
from krita_codex_mcp.installer.errors import InstallerError
from krita_codex_mcp.installer.manager import InstallationManager
from krita_codex_mcp.installer.paths import InstallPaths
from krita_codex_mcp.installer.transaction import SwapComponent


@unittest.skipIf(os.name == "nt", "POSIX file modes do not describe Windows ACLs")
class InstallerPermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o755)
        self.allowed = self.root / "work"
        self.allowed.mkdir()
        self.paths = InstallPaths.from_environment({"HOME": str(self.root)})
        self.manager = InstallationManager(self.paths)
        reservation = patch(
            "krita_codex_mcp.installer.manager.BridgePortReservation",
            side_effect=lambda _ports: nullcontext(),
        )
        reservation.start()
        self.addCleanup(reservation.stop)
        previous_umask = os.umask(0)
        self.addCleanup(os.umask, previous_umask)

    def assert_private(self, path: Path, mode: int) -> None:
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode, str(path))

    def test_fresh_install_protects_config_directory_and_token(self) -> None:
        self.manager.install(allowed_roots=[self.allowed])
        self.assert_private(self.paths.config_dir, 0o700)
        self.assert_private(self.paths.config_file, 0o600)

    def test_staged_token_is_private_when_created(self) -> None:
        staged = self.root / "stage" / "config.json"
        config = {
            "token": "synthetic-permission-test-token-only",
            "allowed_roots": [str(self.allowed)],
        }
        real_open = os.open
        observed_modes: list[int] = []

        def observe_open(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if Path(path) == staged:
                observed_modes.append(stat.S_IMODE(os.fstat(descriptor).st_mode))
            return descriptor

        with patch("os.open", side_effect=observe_open):
            write_staged_config(staged, config)
        self.assertEqual(observed_modes, [0o600])
        self.assert_private(staged, 0o600)

    def test_update_hardens_existing_config_before_creating_a_backup(self) -> None:
        self.manager.install(allowed_roots=[self.allowed])
        previous = self.paths.config_file.read_bytes()
        self.paths.config_dir.chmod(0o755)
        self.paths.config_file.chmod(0o644)

        def inspect_stage(step: str) -> None:
            if step == "after_stage":
                self.assert_private(self.paths.config_dir, 0o700)
                self.assert_private(self.paths.config_file, 0o600)
            if step == "after_swap_config":
                copies = list(self.paths.config_dir.rglob("config.json"))
                self.assertGreaterEqual(len(copies), 2)
                for copy in copies:
                    self.assert_private(copy, 0o600)

        InstallationManager(self.paths, fault_injector=inspect_stage).update()
        self.assertEqual(self.paths.config_file.read_bytes(), previous)

    def test_failed_update_retains_only_private_token_snapshots(self) -> None:
        self.manager.install(allowed_roots=[self.allowed])
        self.paths.config_file.chmod(0o644)

        def fail(step: str) -> None:
            if step == "after_swap_config":
                raise RuntimeError("injected failure")

        with (
            patch.object(SwapComponent, "restore", side_effect=OSError("restore failed")),
            self.assertRaisesRegex(InstallerError, "rollback was incomplete"),
        ):
            InstallationManager(self.paths, fault_injector=fail).update()
        copies = list(self.paths.config_dir.rglob("config.json"))
        self.assertGreaterEqual(len(copies), 2)
        for copy in copies:
            self.assert_private(copy, 0o600)
        self.assert_private(self.paths.config_dir, 0o700)

    def test_recovery_hardens_validated_config_snapshots_before_reading_them(self) -> None:
        self.manager.install(allowed_roots=[self.allowed])

        def fail(step: str) -> None:
            if step == "after_swap_config":
                raise RuntimeError("injected failure")

        with (
            patch.object(SwapComponent, "restore", side_effect=OSError("restore failed")),
            self.assertRaises(InstallerError),
        ):
            InstallationManager(self.paths, fault_injector=fail).update()
        for copy in self.paths.config_dir.rglob("config.json"):
            copy.chmod(0o644)
        self.paths.config_dir.chmod(0o755)
        original_read = Path.read_text
        inspected: list[Path] = []

        def inspect_read(path: Path, *args, **kwargs):
            if path.name == "config.json" and path.exists():
                self.assert_private(path, 0o600)
                self.assert_private(self.paths.config_dir, 0o700)
                inspected.append(path)
            return original_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", inspect_read):
            self.manager.update()
        self.assertGreaterEqual(len(set(inspected)), 2)
        self.assertFalse(self.paths.transaction_journal_file.exists())

    def test_uninstall_keeps_existing_config_private(self) -> None:
        self.manager.install(allowed_roots=[self.allowed])
        previous = json.loads(self.paths.config_file.read_text())
        self.paths.config_dir.chmod(0o755)
        self.paths.config_file.chmod(0o644)
        self.manager.uninstall()
        self.assert_private(self.paths.config_dir, 0o700)
        self.assert_private(self.paths.config_file, 0o600)
        self.assertEqual(json.loads(self.paths.config_file.read_text()), previous)

    def test_uninstall_rollback_restores_a_private_config(self) -> None:
        self.manager.install(allowed_roots=[self.allowed])
        previous = self.paths.config_file.read_bytes()
        self.paths.config_file.chmod(0o644)

        def fail(step: str) -> None:
            if step == "after_remove_config":
                copies = list(self.paths.config_dir.rglob("config.json"))
                self.assertEqual(len(copies), 1)
                self.assert_private(copies[0], 0o600)
                raise RuntimeError("injected uninstall failure")

        with self.assertRaisesRegex(InstallerError, "previous installation was restored"):
            InstallationManager(self.paths, fault_injector=fail).uninstall(remove_config=True)
        self.assertEqual(self.paths.config_file.read_bytes(), previous)
        self.assert_private(self.paths.config_file, 0o600)

    def test_recovery_restores_a_private_old_backup_when_target_is_missing(self) -> None:
        self.manager.install(allowed_roots=[self.allowed])
        previous = self.paths.config_file.read_bytes()

        class SimulatedCrash(BaseException):
            pass

        def crash(step: str) -> None:
            if step == "after_swap_config":
                raise SimulatedCrash()

        with self.assertRaises(SimulatedCrash):
            InstallationManager(self.paths, fault_injector=crash).update()
        self.paths.config_file.unlink()
        backups = list(self.paths.config_dir.glob(".kritacodex-config-*/backup/config.json"))
        self.assertEqual(len(backups), 1)
        backups[0].chmod(0o644)
        self.paths.config_dir.chmod(0o755)

        with (
            patch(
                "krita_codex_mcp.installer.manager.stage_distribution_payload",
                side_effect=RuntimeError("stop after recovery"),
            ),
            self.assertRaisesRegex(InstallerError, "before any files were replaced"),
        ):
            self.manager.update()
        self.assertEqual(self.paths.config_file.read_bytes(), previous)
        self.assert_private(self.paths.config_file, 0o600)
        self.assertFalse(self.paths.transaction_journal_file.exists())

    def test_configuration_symlink_is_rejected_without_changing_its_target(self) -> None:
        self.manager.install(allowed_roots=[self.allowed])
        outside = self.root / "separate-config.json"
        self.paths.config_file.replace(outside)
        outside.chmod(0o644)
        previous = outside.read_bytes()
        self.paths.config_file.symlink_to(outside)
        with self.assertRaisesRegex(InstallerError, "must not be symlinks"):
            self.manager.update()
        self.assert_private(outside, 0o644)
        self.assertEqual(outside.read_bytes(), previous)

    def test_permission_failure_aborts_before_staging(self) -> None:
        self.manager.install(allowed_roots=[self.allowed])
        previous = self.paths.config_file.read_bytes()
        original_chmod = Path.chmod

        def refuse_config(path: Path, mode: int, **kwargs):
            if path == self.paths.config_file:
                raise PermissionError("synthetic permission failure")
            return original_chmod(path, mode, **kwargs)

        with (
            patch.object(Path, "chmod", refuse_config),
            self.assertRaisesRegex(
                InstallerError, "Cannot secure an existing bridge configuration"
            ),
        ):
            self.manager.update()
        self.assertEqual(self.paths.config_file.read_bytes(), previous)
        self.assertEqual(list(self.paths.config_dir.glob(".kritacodex-config-*")), [])


if __name__ == "__main__":
    unittest.main()
