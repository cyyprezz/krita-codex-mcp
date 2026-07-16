from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .errors import InstallerError

BEGIN_MARKER = "# >>> krita-codex-mcp managed block >>>"
END_MARKER = "# <<< krita-codex-mcp managed block <<<"


@dataclass(frozen=True)
class CodexConfigChange:
    previous_exists: bool
    previous: str
    updated: str
    preview: str


def managed_block(distribution_version: str) -> str:
    return "\n".join(
        [
            BEGIN_MARKER,
            "[mcp_servers.krita]",
            'command = "uvx"',
            (
                'args = ["--from", "krita-codex-mcp=='
                + distribution_version
                + '", "krita-codex-mcp"]'
            ),
            "startup_timeout_sec = 60",
            "tool_timeout_sec = 210",
            END_MARKER,
        ]
    )


def _parse_toml(content: str) -> dict[str, object]:
    if not content.strip():
        return {}
    try:
        return tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise InstallerError("The user Codex configuration is not valid TOML.") from exc


def _managed_span(content: str) -> tuple[int, int] | None:
    begin_count = content.count(BEGIN_MARKER)
    end_count = content.count(END_MARKER)
    if begin_count == 0 and end_count == 0:
        return None
    if begin_count != 1 or end_count != 1:
        raise InstallerError("The managed Codex configuration markers are ambiguous.")
    begin = content.index(BEGIN_MARKER)
    end = content.index(END_MARKER, begin) + len(END_MARKER)
    if "[mcp_servers.krita]" not in content[begin:end]:
        raise InstallerError("The managed Codex configuration block is malformed.")
    return begin, end


def plan_codex_config_change(
    path: Path,
    distribution_version: str,
    *,
    remove: bool = False,
) -> CodexConfigChange:
    previous_exists = path.exists()
    try:
        previous = path.read_text(encoding="utf-8") if previous_exists else ""
    except OSError as exc:
        raise InstallerError("The user Codex configuration could not be read.") from exc
    parsed = _parse_toml(previous)
    span = _managed_span(previous)
    if span is None:
        existing = parsed.get("mcp_servers")
        if isinstance(existing, dict) and "krita" in existing:
            raise InstallerError(
                "An unmarked [mcp_servers.krita] entry already exists and was not changed."
            )
        if remove:
            raise InstallerError("No installer-managed Codex configuration block exists.")
        block = managed_block(distribution_version)
        separator = "" if not previous else ("\n" if previous.endswith("\n") else "\n\n")
        updated = previous + separator + block + "\n"
        preview = block
    else:
        begin, end = span
        if remove:
            updated = (previous[:begin] + previous[end:]).strip("\n")
            if updated:
                updated += "\n"
            preview = "Remove the installer-managed [mcp_servers.krita] block."
        else:
            block = managed_block(distribution_version)
            updated = previous[:begin] + block + previous[end:]
            preview = block
    _parse_toml(updated)
    return CodexConfigChange(
        previous_exists=previous_exists,
        previous=previous,
        updated=updated,
        preview=preview,
    )


def _write_fsynced(path: Path, data: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _assert_preview_is_current(path: Path, change: CodexConfigChange) -> None:
    exists = path.exists()
    if exists != change.previous_exists:
        raise InstallerError(
            "The user Codex configuration changed after the preview. The configuration "
            "was not replaced; generate a new preview and confirm it again."
        )
    if not exists:
        return
    try:
        current = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallerError(
            "The user Codex configuration could not be re-read before applying the preview."
        ) from exc
    if current != change.previous:
        raise InstallerError(
            "The user Codex configuration changed after the preview. The configuration "
            "was not replaced; generate a new preview and confirm it again."
        )


def apply_codex_config_change(path: Path, change: CodexConfigChange) -> None:
    _assert_preview_is_current(path, change)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InstallerError("The user Codex configuration directory is not writable.") from exc
    backup = path.with_name(path.name + ".krita-codex-mcp.backup")
    had_previous = path.exists()
    backup_stage = path.with_name("." + path.name + ".backup-" + uuid4().hex)
    stage = path.with_name("." + path.name + ".stage-" + uuid4().hex)
    replaced = False
    try:
        if had_previous:
            _write_fsynced(backup_stage, change.previous.encode("utf-8"))
            os.replace(backup_stage, backup)
        _write_fsynced(stage, change.updated.encode("utf-8"))
        _parse_toml(stage.read_text(encoding="utf-8"))
        _assert_preview_is_current(path, change)
        os.replace(stage, path)
        replaced = True
        _parse_toml(path.read_text(encoding="utf-8"))
    except Exception as exc:
        for temporary in (backup_stage, stage):
            with suppress(OSError):
                temporary.unlink()
        try:
            if replaced and had_previous and backup.exists():
                restore_stage = path.with_name("." + path.name + ".restore-" + uuid4().hex)
                _write_fsynced(restore_stage, backup.read_bytes())
                os.replace(restore_stage, path)
            elif replaced and not had_previous and path.exists():
                path.unlink()
        except OSError as rollback_exc:
            raise InstallerError(
                "The user Codex configuration update failed and rollback was incomplete; "
                "the atomic backup was retained."
            ) from rollback_exc
        raise InstallerError(
            "The user Codex configuration update failed and was rolled back."
        ) from exc


def configure_codex(
    path: Path,
    distribution_version: str,
    *,
    apply: bool,
    remove: bool,
    confirmed: bool,
    confirm: Callable[[str], str] = input,
) -> CodexConfigChange:
    change = plan_codex_config_change(path, distribution_version, remove=remove)
    if not apply:
        return change
    accepted = confirmed
    if not accepted:
        answer = confirm("Apply this user-wide Codex configuration change? [y/N] ")
        accepted = answer.strip().casefold() in {"y", "yes"}
    if not accepted:
        raise InstallerError("The user-wide Codex configuration was not changed.")
    apply_codex_config_change(path, change)
    return change
