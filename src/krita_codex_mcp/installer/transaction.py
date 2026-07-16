from __future__ import annotations

import json
import os
import shutil
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .errors import InstallerError

if TYPE_CHECKING:
    from .paths import InstallPaths

JOURNAL_SCHEMA_VERSION = 1
MUTATING_PHASE = "mutating"
VALIDATED_PHASE = "validated"


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _fsync_directory(path: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        # Windows does not generally allow opening directories this way. Every journal
        # file itself is still flushed before its atomic replace.
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


class TransactionJournal:
    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.data = data

    @classmethod
    def start(
        cls,
        path: Path,
        *,
        operation: str,
        roots: Sequence[Path],
        components: Sequence[SwapComponent | RemoveComponent],
    ) -> TransactionJournal:
        records: list[dict[str, Any]] = []
        for component in components:
            if component.had_target is None:
                raise InstallerError("Transaction component state was not prepared.")
            record: dict[str, Any] = {
                "name": component.name,
                "target": str(component.target),
                "backup": str(component.backup),
                "had_target": component.had_target,
                "state": "pending",
            }
            if isinstance(component, SwapComponent):
                record["staged"] = str(component.staged)
            records.append(record)
        journal = cls(
            path,
            {
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "operation": operation,
                "phase": MUTATING_PHASE,
                "roots": [str(root) for root in roots],
                "components": records,
            },
        )
        journal._write()
        return journal

    @classmethod
    def load(cls, path: Path) -> TransactionJournal:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InstallerError(
                f"The incomplete installer transaction journal is unreadable: {path}"
            ) from exc
        if not isinstance(raw, dict):
            raise InstallerError(
                f"The incomplete installer transaction journal is invalid: {path}"
            )
        return cls(path, raw)

    def mark_component(self, name: str, state: str) -> None:
        matching = self._matching_components(name)
        matching[0]["state"] = state
        self._write()

    def component(self, name: str) -> dict[str, Any]:
        return self._matching_components(name)[0]

    def _matching_components(self, name: str) -> list[dict[str, Any]]:
        components = self.data.get("components")
        if not isinstance(components, list):
            raise InstallerError(f"The transaction journal is invalid: {self.path}")
        matching = [entry for entry in components if entry.get("name") == name]
        if len(matching) != 1:
            raise InstallerError(f"The transaction journal is invalid: {self.path}")
        return matching

    def mark_validated(self) -> None:
        self.data["phase"] = VALIDATED_PHASE
        self._write()

    def remove(self) -> None:
        self.path.unlink()
        _fsync_directory(self.path.parent)

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            encoded = (json.dumps(self.data, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            )
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            _fsync_directory(self.path.parent)
        finally:
            with suppress(OSError):
                temporary.unlink()


@dataclass
class SwapComponent:
    name: str
    staged: Path
    target: Path
    backup: Path
    had_target: bool | None = None
    installed: bool = False

    def prepare(self) -> None:
        self.had_target = self.target.exists() or self.target.is_symlink()

    def apply(self, journal: TransactionJournal | None = None) -> None:
        if self.had_target is None:
            self.prepare()
        self.backup.parent.mkdir(parents=True, exist_ok=True)
        if self.had_target:
            if journal is not None:
                journal.mark_component(self.name, "moving_target_to_backup")
            self.target.replace(self.backup)
            _fsync_directory(self.target.parent)
            _fsync_directory(self.backup.parent)
            if journal is not None:
                journal.mark_component(self.name, "target_backed_up")
        self.target.parent.mkdir(parents=True, exist_ok=True)
        if journal is not None:
            journal.mark_component(self.name, "installing_staged")
        self.staged.replace(self.target)
        _fsync_directory(self.staged.parent)
        _fsync_directory(self.target.parent)
        self.installed = True
        if journal is not None:
            journal.mark_component(self.name, "installed")

    def restore(self, journal: TransactionJournal | None = None) -> None:
        if journal is None:
            if _exists(self.backup):
                if _exists(self.target):
                    remove_path(self.target)
                self.backup.replace(self.target)
            elif not self.had_target and _exists(self.target):
                remove_path(self.target)
            return
        _restore_swap(journal.component(self.name), journal)


@dataclass
class RemoveComponent:
    name: str
    target: Path
    backup: Path
    had_target: bool | None = None
    removed: bool = False

    def prepare(self) -> None:
        self.had_target = self.target.exists() or self.target.is_symlink()

    def apply(self, journal: TransactionJournal | None = None) -> None:
        if self.had_target is None:
            self.prepare()
        if not self.had_target:
            return
        self.backup.parent.mkdir(parents=True, exist_ok=True)
        if journal is not None:
            journal.mark_component(self.name, "moving_target_to_backup")
        self.target.replace(self.backup)
        _fsync_directory(self.target.parent)
        _fsync_directory(self.backup.parent)
        self.removed = True
        if journal is not None:
            journal.mark_component(self.name, "removed")

    def restore(self, journal: TransactionJournal | None = None) -> None:
        if journal is None:
            if _exists(self.backup):
                self.backup.replace(self.target)
            return
        _restore_remove(journal.component(self.name), journal)


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _restore_swap(
    entry: dict[str, Any],
    journal: TransactionJournal | None = None,
) -> None:
    target = Path(entry["target"])
    backup = Path(entry["backup"])
    had_target = entry["had_target"]
    state = entry["state"]
    target_exists = _exists(target)
    backup_exists = _exists(backup)

    if state == "restored":
        if had_target and (not target_exists or backup_exists):
            raise OSError("restored target state is inconsistent")
        if not had_target and (target_exists or backup_exists):
            raise OSError("restored absent target state is inconsistent")
        return
    if state == "restoring_previous":
        if had_target:
            if backup_exists:
                if target_exists:
                    remove_path(target)
                backup.replace(target)
            elif not target_exists:
                raise OSError("previous target and its backup are both missing")
        else:
            if backup_exists:
                raise OSError("unexpected backup exists while restoring absent target")
            if target_exists:
                remove_path(target)
        if journal is not None:
            journal.mark_component(entry["name"], "restored")
        return

    recoverable = False
    if had_target:
        if state == "pending":
            if not target_exists or backup_exists:
                raise OSError("pending target state is inconsistent")
            recoverable = True
        elif state == "moving_target_to_backup":
            if (target_exists and not backup_exists) or (
                backup_exists and not target_exists
            ):
                recoverable = True
            else:
                raise OSError("target-to-backup boundary is inconsistent")
        elif state == "target_backed_up":
            if not backup_exists or target_exists:
                raise OSError("backed-up target state is inconsistent")
            recoverable = True
        elif state in {"installing_staged", "installed"}:
            if not backup_exists:
                raise OSError("previous target backup is missing")
            recoverable = True
    else:
        if backup_exists:
            raise OSError("unexpected backup exists for an originally absent target")
        if state == "pending":
            if target_exists:
                raise OSError("an originally absent target appeared unexpectedly")
            recoverable = True
        elif state in {"installing_staged", "installed"}:
            recoverable = True
    if not recoverable:
        raise OSError("unsupported swap journal state")
    if journal is not None:
        journal.mark_component(entry["name"], "restoring_previous")
    target_exists = _exists(target)
    backup_exists = _exists(backup)
    if had_target and backup_exists:
        if target_exists:
            remove_path(target)
        backup.replace(target)
    elif not had_target and target_exists:
        remove_path(target)
    if journal is not None:
        journal.mark_component(entry["name"], "restored")


def _restore_remove(
    entry: dict[str, Any],
    journal: TransactionJournal | None = None,
) -> None:
    target = Path(entry["target"])
    backup = Path(entry["backup"])
    had_target = entry["had_target"]
    state = entry["state"]
    target_exists = _exists(target)
    backup_exists = _exists(backup)

    if state == "restored":
        if had_target and (not target_exists or backup_exists):
            raise OSError("restored remove target state is inconsistent")
        if not had_target and (target_exists or backup_exists):
            raise OSError("restored absent remove target state is inconsistent")
        return
    if state == "restoring_previous":
        if had_target:
            if backup_exists and not target_exists:
                backup.replace(target)
            elif not target_exists or backup_exists:
                raise OSError("remove rollback boundary is inconsistent")
        elif target_exists or backup_exists:
            raise OSError("absent remove target changed during rollback")
        if journal is not None:
            journal.mark_component(entry["name"], "restored")
        return

    recoverable = False
    if not had_target:
        if target_exists or backup_exists or state != "pending":
            raise OSError("originally absent remove target is inconsistent")
        recoverable = True
    elif state == "pending":
        if not target_exists or backup_exists:
            raise OSError("pending remove target state is inconsistent")
        recoverable = True
    elif state == "moving_target_to_backup":
        if (target_exists and not backup_exists) or (
            backup_exists and not target_exists
        ):
            recoverable = True
        else:
            raise OSError("remove boundary is inconsistent")
    elif state == "removed":
        if not backup_exists or target_exists:
            raise OSError("removed target state is inconsistent")
        recoverable = True
    if not recoverable:
        raise OSError("unsupported remove journal state")
    if journal is not None:
        journal.mark_component(entry["name"], "restoring_previous")
    if had_target and _exists(backup):
        backup.replace(target)
    if journal is not None:
        journal.mark_component(entry["name"], "restored")


def _validated_journal_path(
    value: object,
    *,
    label: str,
    journal: TransactionJournal,
) -> Path:
    if not isinstance(value, str):
        raise InstallerError(f"The transaction {label} path is invalid: {journal.path}")
    raw_segments = value.replace("\\", "/").split("/")
    candidate = Path(value)
    if (
        not candidate.is_absolute()
        or any(segment in {".", ".."} for segment in raw_segments)
    ):
        raise InstallerError(f"The transaction {label} path is unsafe: {journal.path}")
    return candidate


def _validated_records(
    journal: TransactionJournal,
    paths: InstallPaths,
) -> tuple[str, list[Path], list[dict[str, Any]]]:
    data = journal.data
    if data.get("schema_version") != JOURNAL_SCHEMA_VERSION:
        raise InstallerError(f"The transaction journal schema is invalid: {journal.path}")
    operation = data.get("operation")
    phase = data.get("phase")
    roots_raw = data.get("roots")
    components = data.get("components")
    if (
        operation not in {"install", "uninstall"}
        or phase not in {MUTATING_PHASE, VALIDATED_PHASE}
        or not isinstance(roots_raw, list)
        or not isinstance(components, list)
    ):
        raise InstallerError(f"The transaction journal is invalid: {journal.path}")
    if len(roots_raw) != 2:
        raise InstallerError(f"The transaction journal roots are invalid: {journal.path}")
    roots = [
        _validated_journal_path(value, label="root", journal=journal)
        for value in roots_raw
    ]
    expected_root_parents = (paths.pykrita_root, paths.config_dir)
    expected_prefixes = (
        (".kritacodex-install-", ".kritacodex-config-")
        if operation == "install"
        else (".kritacodex-uninstall-", ".kritacodex-uninstall-config-")
    )
    for root, parent, prefix in zip(
        roots, expected_root_parents, expected_prefixes, strict=True
    ):
        if root.parent != parent or not root.name.startswith(prefix):
            raise InstallerError(f"The transaction journal root is unsafe: {journal.path}")

    expected_targets = {
        "plugin": paths.plugin_dir,
        "protocol": paths.protocol_dir,
        "desktop": paths.desktop_file,
        "config": paths.config_file,
    }
    payload_root, config_root = roots
    expected_backups = {
        "plugin": payload_root / "backup" / "kritacodexbridge",
        "protocol": payload_root / "backup" / "krita_codex_protocol",
        "desktop": payload_root / "backup" / "kritacodexbridge.desktop",
        "config": config_root / "backup" / "config.json",
    }
    expected_stages = {
        "plugin": payload_root / "stage" / "kritacodexbridge",
        "protocol": payload_root / "stage" / "krita_codex_protocol",
        "desktop": payload_root / "stage" / "kritacodexbridge.desktop",
        "config": config_root / "stage" / "config.json",
    }
    allowed_states = (
        {
            "pending",
            "moving_target_to_backup",
            "target_backed_up",
            "installing_staged",
            "installed",
            "restoring_previous",
            "restored",
        }
        if operation == "install"
        else {"pending", "moving_target_to_backup", "removed", "restoring_previous", "restored"}
    )
    names: set[str] = set()
    for entry in components:
        if not isinstance(entry, dict):
            raise InstallerError(f"The transaction journal component is invalid: {journal.path}")
        name = entry.get("name")
        state = entry.get("state")
        had_target = entry.get("had_target")
        if (
            not isinstance(name, str)
            or name in names
            or name not in expected_targets
            or state not in allowed_states
            or not isinstance(had_target, bool)
        ):
            raise InstallerError(f"The transaction journal component is unsafe: {journal.path}")
        names.add(name)
        target_path = _validated_journal_path(
            entry.get("target"), label=f"{name} target", journal=journal
        )
        backup_path = _validated_journal_path(
            entry.get("backup"), label=f"{name} backup", journal=journal
        )
        if target_path != expected_targets[name]:
            raise InstallerError(f"The transaction target path is unsafe: {journal.path}")
        if backup_path != expected_backups[name]:
            raise InstallerError(f"The transaction backup path is unsafe: {journal.path}")
        staged = entry.get("staged")
        if operation == "install":
            staged_path = _validated_journal_path(
                staged, label=f"{name} stage", journal=journal
            )
            if staged_path != expected_stages[name]:
                raise InstallerError(f"The transaction stage path is unsafe: {journal.path}")
        elif staged is not None:
            raise InstallerError(f"The uninstall journal contains a stage path: {journal.path}")
    required = {"plugin", "protocol", "desktop"}
    if operation == "install":
        required.add("config")
    if not required.issubset(names):
        raise InstallerError(f"The transaction journal is incomplete: {journal.path}")
    return operation, roots, components


def _cleanup_roots(roots: Sequence[Path]) -> None:
    for root in roots:
        if _exists(root):
            remove_path(root)


def _recovery_details(journal: TransactionJournal, components: Sequence[dict[str, Any]]) -> str:
    backups = ", ".join(str(entry["backup"]) for entry in components)
    return f"Journal: {journal.path}. Backup paths: {backups}."


def recover_incomplete_transaction(paths: InstallPaths) -> None:
    if not paths.transaction_journal_file.exists():
        return
    journal = TransactionJournal.load(paths.transaction_journal_file)
    operation, roots, components = _validated_records(journal, paths)
    details = _recovery_details(journal, components)

    if journal.data["phase"] == VALIDATED_PHASE:
        try:
            _cleanup_roots(roots)
            journal.remove()
        except OSError as exc:
            raise InstallerError(
                "A validated installer transaction could not finish cleanup. " + details
            ) from exc
        return

    failures: list[str] = []
    restore = _restore_swap if operation == "install" else _restore_remove
    for entry in reversed(components):
        try:
            restore(entry, journal)
        except OSError as exc:
            failures.append(f"{entry['name']}: {exc}")
    if failures:
        raise InstallerError(
            "Automatic recovery of the incomplete installer transaction failed "
            f"({'; '.join(failures)}). Backups and journal were retained. {details}"
        )
    try:
        _cleanup_roots(roots)
        journal.remove()
    except OSError as exc:
        raise InstallerError(
            "The previous installation was restored, but transaction cleanup failed; "
            "journal and remaining backups were retained. " + details
        ) from exc


def incomplete_transaction_config_paths(paths: InstallPaths) -> list[Path]:
    """Return only strictly validated config snapshots relevant to crash recovery."""
    if not paths.transaction_journal_file.exists():
        return []
    journal = TransactionJournal.load(paths.transaction_journal_file)
    _operation, _roots, components = _validated_records(journal, paths)
    candidates = [paths.config_file]
    for entry in components:
        if entry["name"] != "config":
            continue
        candidates.append(Path(entry["backup"]))
        staged = entry.get("staged")
        if isinstance(staged, str):
            candidates.append(Path(staged))
    return list(dict.fromkeys(candidates))
