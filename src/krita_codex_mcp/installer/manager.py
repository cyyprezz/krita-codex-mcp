from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from krita_codex_mcp import __version__
from krita_codex_protocol import BridgeConfig

from .configuration import (
    DEFAULT_BRIDGE_PORT,
    configured_port_candidate,
    load_raw_config,
    prepare_config,
    write_staged_config,
)
from .errors import InstallerError
from .locking import InstallerLock
from .manifest import verify_installed_payload, verify_payload_root
from .paths import InstallPaths
from .port_reservation import BridgePortReservation
from .resources import stage_distribution_payload
from .transaction import (
    RemoveComponent,
    SwapComponent,
    TransactionJournal,
    incomplete_transaction_config_paths,
    recover_incomplete_transaction,
    remove_path,
)

FaultInjector = Callable[[str], None]


def _no_fault(_step: str) -> None:
    return


class InstallationManager:
    def __init__(
        self,
        paths: InstallPaths,
        *,
        distribution_version: str = __version__,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self.paths = paths
        self.distribution_version = distribution_version
        self._fault = fault_injector or _no_fault

    def install(
        self,
        *,
        allowed_roots: Iterable[str | Path] | None,
        port: int | None = None,
        reset_config: bool = False,
    ) -> None:
        with InstallerLock(self.paths.installer_lock_file):
            self._recover_incomplete_transaction()
            old_port = configured_port_candidate(self.paths.config_file)
            existing = self._load_existing_config(reset_config=reset_config)
            config = prepare_config(
                existing,
                allowed_roots=allowed_roots,
                port=port,
                reset=reset_config,
            )
            ports = {BridgeConfig.from_mapping(config).port}
            if old_port is not None:
                ports.add(old_port)
            with BridgePortReservation(ports):
                self._install_payload(config)

    def update(self) -> None:
        with InstallerLock(self.paths.installer_lock_file):
            self._recover_incomplete_transaction()
            existing = load_raw_config(self.paths.config_file)
            if existing is None:
                raise InstallerError("Update requires an existing bridge configuration.")
            config = prepare_config(
                existing,
                allowed_roots=None,
                port=None,
                reset=False,
            )
            with BridgePortReservation([BridgeConfig.from_mapping(config).port]):
                self._install_payload(config)

    def uninstall(self, *, remove_config: bool = False) -> None:
        with InstallerLock(self.paths.installer_lock_file):
            self._recover_incomplete_transaction()
            existing = load_raw_config(self.paths.config_file)
            port = (
                BridgeConfig.from_mapping(existing).port
                if existing is not None
                else DEFAULT_BRIDGE_PORT
            )
            with BridgePortReservation([port]):
                self._uninstall_locked(remove_config=remove_config)

    def _uninstall_locked(self, *, remove_config: bool) -> None:
        self.paths.pykrita_root.mkdir(parents=True, exist_ok=True)
        self.paths.config_dir.mkdir(parents=True, exist_ok=True)
        transaction_root = self.paths.pykrita_root / (
            ".kritacodex-uninstall-" + uuid4().hex
        )
        config_transaction_root = self.paths.config_dir / (
            ".kritacodex-uninstall-config-" + uuid4().hex
        )
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
        if remove_config:
            components.append(
                RemoveComponent(
                    "config",
                    self.paths.config_file,
                    config_transaction_root / "backup" / "config.json",
                )
            )
        for component in components:
            component.prepare()
        journal = TransactionJournal.start(
            self.paths.transaction_journal_file,
            operation="uninstall",
            roots=[transaction_root, config_transaction_root],
            components=components,
        )
        applied: list[RemoveComponent] = []
        try:
            for component in components:
                applied.append(component)
                component.apply(journal)
                self._fault(f"after_remove_{component.name}")
            for component in components:
                if component.had_target and (
                    component.target.exists() or component.target.is_symlink()
                ):
                    raise InstallerError("Uninstall post-validation found a managed target.")
            journal.mark_validated()
        except Exception as exc:
            rollback_errors = self._restore_removed(applied, journal)
            if rollback_errors:
                raise InstallerError(
                    "Uninstall failed and rollback was incomplete; backups and journal "
                    f"were retained. {self._transaction_details(journal, components)}"
                ) from exc
            self._finish_rolled_back_transaction(
                journal,
                [transaction_root, config_transaction_root],
                components,
            )
            raise InstallerError(
                "Uninstall failed; the previous installation was restored."
            ) from exc
        self._finish_validated_transaction(
            journal,
            [transaction_root, config_transaction_root],
            components,
        )

    def _load_existing_config(self, *, reset_config: bool) -> dict[str, Any] | None:
        if not reset_config:
            return load_raw_config(self.paths.config_file)
        try:
            return load_raw_config(self.paths.config_file)
        except InstallerError:
            return None

    def _recover_incomplete_transaction(self) -> None:
        config_paths = incomplete_transaction_config_paths(self.paths)
        if not config_paths:
            return
        ports = {
            candidate
            for path in config_paths
            if (candidate := configured_port_candidate(path)) is not None
        }
        if not ports:
            ports.add(DEFAULT_BRIDGE_PORT)
        with BridgePortReservation(ports):
            recover_incomplete_transaction(self.paths)

    def _install_payload(self, config: Mapping[str, Any]) -> None:
        self.paths.pykrita_root.mkdir(parents=True, exist_ok=True)
        self.paths.config_dir.mkdir(parents=True, exist_ok=True)
        transaction_root = self.paths.pykrita_root / (
            ".kritacodex-install-" + uuid4().hex
        )
        config_transaction_root = self.paths.config_dir / (
            ".kritacodex-config-" + uuid4().hex
        )
        stage_root = transaction_root / "stage"
        staged_config = config_transaction_root / "stage" / "config.json"
        try:
            stage_distribution_payload(stage_root, self.distribution_version)
            write_staged_config(staged_config, config)
            verify_payload_root(stage_root, self.distribution_version)
            self._fault("after_stage")
            components = self._swap_components(
                stage_root,
                staged_config,
                transaction_root,
                config_transaction_root,
            )
            for component in components:
                component.prepare()
            journal = TransactionJournal.start(
                self.paths.transaction_journal_file,
                operation="install",
                roots=[transaction_root, config_transaction_root],
                components=components,
            )
            applied: list[SwapComponent] = []
            try:
                for component in components:
                    applied.append(component)
                    component.apply(journal)
                    self._fault(f"after_swap_{component.name}")
                verify_installed_payload(self.paths, self.distribution_version)
                BridgeConfig.load(self.paths.config_file)
                self._fault("after_validate")
                journal.mark_validated()
            except Exception as exc:
                rollback_errors = self._restore_swapped(applied, journal)
                if rollback_errors:
                    raise InstallerError(
                        "Installation failed and rollback was incomplete; backups and journal "
                        f"were retained. {self._transaction_details(journal, components)}"
                    ) from exc
                self._finish_rolled_back_transaction(
                    journal,
                    [transaction_root, config_transaction_root],
                    components,
                )
                raise InstallerError(
                    "Installation failed; the previous installation was restored."
                ) from exc
        except InstallerError:
            if (
                transaction_root.exists() or config_transaction_root.exists()
            ) and not self._contains_backup(transaction_root, config_transaction_root):
                self._cleanup(transaction_root, config_transaction_root)
            raise
        except Exception as exc:
            self._cleanup(transaction_root, config_transaction_root)
            raise InstallerError(
                "Installation staging failed before any files were replaced."
            ) from exc
        self._finish_validated_transaction(
            journal,
            [transaction_root, config_transaction_root],
            components,
        )

    def _swap_components(
        self,
        stage_root: Path,
        staged_config: Path,
        transaction_root: Path,
        config_transaction_root: Path,
    ) -> list[SwapComponent]:
        return [
            SwapComponent(
                "plugin",
                stage_root / "kritacodexbridge",
                self.paths.plugin_dir,
                transaction_root / "backup" / "kritacodexbridge",
            ),
            SwapComponent(
                "protocol",
                stage_root / "krita_codex_protocol",
                self.paths.protocol_dir,
                transaction_root / "backup" / "krita_codex_protocol",
            ),
            SwapComponent(
                "desktop",
                stage_root / "kritacodexbridge.desktop",
                self.paths.desktop_file,
                transaction_root / "backup" / "kritacodexbridge.desktop",
            ),
            SwapComponent(
                "config",
                staged_config,
                self.paths.config_file,
                config_transaction_root / "backup" / "config.json",
            ),
        ]

    @staticmethod
    def _restore_swapped(
        components: list[SwapComponent],
        journal: TransactionJournal,
    ) -> list[str]:
        errors: list[str] = []
        for component in reversed(components):
            try:
                component.restore(journal)
            except Exception as exc:
                errors.append(f"{component.name}: {type(exc).__name__}")
        return errors

    @staticmethod
    def _restore_removed(
        components: list[RemoveComponent],
        journal: TransactionJournal,
    ) -> list[str]:
        errors: list[str] = []
        for component in reversed(components):
            try:
                component.restore(journal)
            except Exception as exc:
                errors.append(f"{component.name}: {type(exc).__name__}")
        return errors

    @staticmethod
    def _contains_backup(*roots: Path) -> bool:
        return any((root / "backup").exists() for root in roots)

    @staticmethod
    def _transaction_details(
        journal: TransactionJournal,
        components: Iterable[SwapComponent | RemoveComponent],
    ) -> str:
        backups = ", ".join(str(component.backup) for component in components)
        return f"Journal: {journal.path}. Backup paths: {backups}."

    def _finish_rolled_back_transaction(
        self,
        journal: TransactionJournal,
        roots: list[Path],
        components: Iterable[SwapComponent | RemoveComponent],
    ) -> None:
        try:
            self._cleanup(*roots)
            journal.remove()
        except OSError as exc:
            raise InstallerError(
                "The previous installation was restored, but transaction cleanup failed; "
                "the journal and remaining backups were retained. "
                + self._transaction_details(journal, components)
            ) from exc

    def _finish_validated_transaction(
        self,
        journal: TransactionJournal,
        roots: list[Path],
        components: Iterable[SwapComponent | RemoveComponent],
    ) -> None:
        try:
            self._cleanup(*roots)
            journal.remove()
        except OSError as exc:
            raise InstallerError(
                "The installation was validated, but transaction cleanup failed; "
                "the next mutating installer run will resume cleanup. "
                + self._transaction_details(journal, components)
            ) from exc

    @staticmethod
    def _cleanup(*roots: Path) -> None:
        for root in roots:
            if root.exists() or root.is_symlink():
                remove_path(root)
            parent = root.parent
            if parent.exists() and not any(parent.iterdir()):
                with suppress(OSError):
                    parent.rmdir()
