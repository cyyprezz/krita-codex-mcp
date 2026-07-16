from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from krita_codex_mcp import __version__
from krita_codex_mcp.installer.configuration import write_staged_config
from krita_codex_mcp.installer.errors import InstallerError
from krita_codex_mcp.installer.manager import InstallationManager
from krita_codex_mcp.installer.paths import InstallPaths
from krita_codex_mcp.installer.resources import stage_distribution_payload
from krita_codex_mcp.installer.transaction import (
    RemoveComponent,
    SwapComponent,
    TransactionJournal,
)


class InstallerCrashRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        # Crash fixtures exercise journal recovery independently of the dedicated socket tests.
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
        self.paths = InstallPaths.from_environment(
            {
                "APPDATA": str(self.root / "roaming"),
                "LOCALAPPDATA": str(self.root / "local"),
                "USERPROFILE": str(self.root / "profile"),
            }
        )
        InstallationManager(self.paths).install(allowed_roots=[self.allowed])
        (self.paths.plugin_dir / "previous-only.txt").write_text(
            "previous", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _snapshot(self) -> dict[str, bytes]:
        snapshot = {
            "config": self.paths.config_file.read_bytes(),
            "desktop": self.paths.desktop_file.read_bytes(),
        }
        for prefix, root in (
            ("plugin", self.paths.plugin_dir),
            ("protocol", self.paths.protocol_dir),
        ):
            for path in root.rglob("*"):
                if path.is_file():
                    snapshot[f"{prefix}/{path.relative_to(root).as_posix()}"] = (
                        path.read_bytes()
                    )
        return snapshot

    def _install_crash_fixture(
        self,
    ) -> tuple[TransactionJournal, list[SwapComponent], list[Path]]:
        transaction_root = self.paths.pykrita_root / ".kritacodex-install-crash"
        config_root = self.paths.config_dir / ".kritacodex-config-crash"
        stage_root = transaction_root / "stage"
        staged_config = config_root / "stage" / "config.json"
        stage_distribution_payload(stage_root, __version__)
        existing = json.loads(self.paths.config_file.read_text(encoding="utf-8"))
        write_staged_config(staged_config, existing)
        components = InstallationManager(self.paths)._swap_components(
            stage_root,
            staged_config,
            transaction_root,
            config_root,
        )
        for component in components:
            component.prepare()
        journal = TransactionJournal.start(
            self.paths.transaction_journal_file,
            operation="install",
            roots=[transaction_root, config_root],
            components=components,
        )
        return journal, components, [transaction_root, config_root]

    def _uninstall_crash_fixture(
        self,
    ) -> tuple[TransactionJournal, list[RemoveComponent], list[Path]]:
        transaction_root = self.paths.pykrita_root / ".kritacodex-uninstall-crash"
        config_root = self.paths.config_dir / ".kritacodex-uninstall-config-crash"
        components = [
            RemoveComponent(
                "plugin",
                self.paths.plugin_dir,
                transaction_root / "backup" / "kritacodexbridge",
            ),
            RemoveComponent(
                "protocol",
                self.paths.protocol_dir,
                transaction_root / "backup" / "krita_codex_protocol",
            ),
            RemoveComponent(
                "desktop",
                self.paths.desktop_file,
                transaction_root / "backup" / "kritacodexbridge.desktop",
            ),
        ]
        for component in components:
            component.prepare()
        journal = TransactionJournal.start(
            self.paths.transaction_journal_file,
            operation="uninstall",
            roots=[transaction_root, config_root],
            components=components,
        )
        return journal, components, [transaction_root, config_root]

    def _next_update_recovers_before_staging(
        self,
        previous: dict[str, bytes],
        roots: list[Path],
    ) -> None:
        with (
            patch(
                "krita_codex_mcp.installer.manager.stage_distribution_payload",
                side_effect=RuntimeError("stop after recovery"),
            ),
            self.assertRaisesRegex(InstallerError, "before any files were replaced"),
        ):
            InstallationManager(self.paths).update()
        self.assertEqual(self._snapshot(), previous)
        self.assertFalse(self.paths.transaction_journal_file.exists())
        self.assertTrue(all(not root.exists() for root in roots))

    @staticmethod
    def _persist_tampered_journal(journal: TransactionJournal) -> None:
        journal.path.write_text(
            json.dumps(journal.data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _partial_plugin_backup(
        self,
    ) -> tuple[TransactionJournal, list[SwapComponent], SwapComponent]:
        journal, components, _roots = self._install_crash_fixture()
        plugin = components[0]
        plugin.backup.parent.mkdir(parents=True)
        journal.mark_component(plugin.name, "moving_target_to_backup")
        plugin.target.replace(plugin.backup)
        return journal, components, plugin

    def test_recovers_install_crash_after_target_was_moved_to_backup(self) -> None:
        previous = self._snapshot()
        journal, components, roots = self._install_crash_fixture()
        plugin = components[0]
        plugin.backup.parent.mkdir(parents=True)
        journal.mark_component(plugin.name, "moving_target_to_backup")
        plugin.target.replace(plugin.backup)

        self._next_update_recovers_before_staging(previous, roots)

    def test_recovers_install_crash_after_staged_target_was_installed(self) -> None:
        previous = self._snapshot()
        journal, components, roots = self._install_crash_fixture()
        plugin = components[0]
        plugin.backup.parent.mkdir(parents=True)
        journal.mark_component(plugin.name, "moving_target_to_backup")
        plugin.target.replace(plugin.backup)
        journal.mark_component(plugin.name, "target_backed_up")
        journal.mark_component(plugin.name, "installing_staged")
        plugin.staged.replace(plugin.target)

        self._next_update_recovers_before_staging(previous, roots)

    def test_recovers_install_crash_during_config_swap(self) -> None:
        previous = self._snapshot()
        journal, components, roots = self._install_crash_fixture()
        for component in components[:3]:
            component.apply(journal)
        config = components[3]
        config.backup.parent.mkdir(parents=True)
        journal.mark_component(config.name, "moving_target_to_backup")
        config.target.replace(config.backup)

        self._next_update_recovers_before_staging(previous, roots)

    def test_recovers_uninstall_crash_after_managed_targets_were_removed(self) -> None:
        previous = self._snapshot()
        journal, components, roots = self._uninstall_crash_fixture()
        components[0].apply(journal)
        components[1].apply(journal)

        self._next_update_recovers_before_staging(previous, roots)

    def test_unrecoverable_crash_retains_journal_and_reports_concrete_paths(self) -> None:
        journal, components, _roots = self._install_crash_fixture()
        plugin = components[0]
        plugin.backup.parent.mkdir(parents=True)
        journal.mark_component(plugin.name, "moving_target_to_backup")
        plugin.target.replace(plugin.backup)
        shutil.rmtree(plugin.backup)

        with self.assertRaises(InstallerError) as captured:
            InstallationManager(self.paths).update()

        message = str(captured.exception)
        self.assertIn(str(self.paths.transaction_journal_file), message)
        self.assertIn(str(plugin.backup), message)
        self.assertIn("retained", message)
        self.assertTrue(self.paths.transaction_journal_file.exists())

    def test_traversal_backup_path_is_rejected_before_recovery_mutates_files(self) -> None:
        journal, _components, plugin = self._partial_plugin_backup()
        traversal = plugin.backup.parent / ".." / ".." / "outside"
        journal.component(plugin.name)["backup"] = str(traversal)
        self._persist_tampered_journal(journal)

        with self.assertRaisesRegex(InstallerError, "backup path is unsafe"):
            InstallationManager(self.paths).update()

        self.assertFalse(plugin.target.exists())
        self.assertTrue(plugin.backup.is_dir())
        self.assertTrue(journal.path.exists())

    def test_wrong_in_root_stage_path_is_rejected_before_recovery_mutates_files(
        self,
    ) -> None:
        journal, components, plugin = self._partial_plugin_backup()
        journal.component(plugin.name)["staged"] = str(components[1].staged)
        self._persist_tampered_journal(journal)

        with self.assertRaisesRegex(InstallerError, "stage path is unsafe"):
            InstallationManager(self.paths).update()

        self.assertFalse(plugin.target.exists())
        self.assertTrue(plugin.backup.is_dir())
        self.assertTrue(journal.path.exists())


if __name__ == "__main__":
    unittest.main()
