from __future__ import annotations

import json
import os
import secrets
import tempfile
from collections.abc import Iterable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from krita_codex_protocol import BridgeConfig, ConfigError

from .errors import InstallerError

DEFAULT_BRIDGE_PORT = 17641

CONFIG_DEFAULTS: dict[str, int] = {
    "port": DEFAULT_BRIDGE_PORT,
    "request_timeout_seconds": 30,
    "open_save_timeout_seconds": 120,
    "export_timeout_seconds": 180,
    "max_request_bytes": 1_048_576,
    "max_response_bytes": 16_777_216,
    "preview_max_dimension": 2048,
    "preview_max_bytes": 8_388_608,
    "asset_max_bytes": 67_108_864,
    "asset_batch_max_bytes": 268_435_456,
    "asset_max_frames": 256,
    "asset_render_max_pixels": 134_217_728,
    "document_max_dimension": 16_384,
    "document_max_pixels": 67_108_864,
}


def configured_port_candidate(path: Path) -> int | None:
    """Read only the possible listener port, including from a resettable bad config."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_BRIDGE_PORT
    if not isinstance(raw, dict):
        return DEFAULT_BRIDGE_PORT
    port = raw.get("port", DEFAULT_BRIDGE_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        return DEFAULT_BRIDGE_PORT
    return port


def load_raw_config(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise InstallerError("The bridge configuration path is not a file.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallerError(
            "The existing bridge configuration is invalid and was not changed."
        ) from exc
    if not isinstance(raw, dict):
        raise InstallerError("The existing bridge configuration must be a JSON object.")
    try:
        BridgeConfig.from_mapping(raw)
    except ConfigError as exc:
        raise InstallerError(
            "The existing bridge configuration is unsafe and was not changed."
        ) from exc
    return raw


def resolve_allowed_roots(values: Iterable[str | Path]) -> list[str]:
    roots: list[str] = []
    for value in values:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise InstallerError("Every allowed root must be an absolute path.")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise InstallerError("Every allowed root must already exist.") from exc
        if not resolved.is_dir():
            raise InstallerError("Every allowed root must be a directory.")
        probe_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=resolved,
                prefix=".krita-codex-write-test-",
                delete=False,
            ) as probe:
                probe_path = Path(probe.name)
            probe_path.unlink()
        except OSError as exc:
            if probe_path is not None and probe_path.exists():
                with suppress(OSError):
                    probe_path.unlink()
            raise InstallerError("Every allowed root must be writable.") from exc
        roots.append(str(resolved))
    if not roots:
        raise InstallerError("A new installation requires at least one --allowed-root.")
    return roots


def prepare_config(
    existing: Mapping[str, Any] | None,
    *,
    allowed_roots: Iterable[str | Path] | None,
    port: int | None,
    reset: bool,
) -> dict[str, Any]:
    supplied_roots = list(allowed_roots or [])
    use_existing = existing is not None and not reset
    if use_existing:
        config = dict(existing)
        if supplied_roots:
            resolved = resolve_allowed_roots(supplied_roots)
            current = [str(path) for path in BridgeConfig.from_mapping(config).allowed_roots]
            if [os.path.normcase(path) for path in resolved] != [
                os.path.normcase(path) for path in current
            ]:
                raise InstallerError(
                    "Migration and update preserve existing allowed roots; "
                    "configure them separately."
                )
        if port is not None:
            config["port"] = port
    else:
        roots = resolve_allowed_roots(supplied_roots)
        config = {
            "token": secrets.token_urlsafe(32),
            "host": "127.0.0.1",
            "allowed_roots": roots,
        }
        if port is not None:
            config["port"] = port
    config["host"] = "127.0.0.1"
    for key, value in CONFIG_DEFAULTS.items():
        config.setdefault(key, value)
    try:
        BridgeConfig.from_mapping(config)
    except ConfigError as exc:
        raise InstallerError("The staged bridge configuration is invalid.") from exc
    return config


def write_staged_config(path: Path, config: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    if os.name == "nt":
        path.write_text(encoded, encoding="utf-8")
    else:
        # Staging paths are unique. Publish no token bytes before the file is private,
        # and never truncate an unexpected existing file or follow a staged symlink.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
    try:
        BridgeConfig.load(path)
    except ConfigError as exc:
        raise InstallerError("The staged bridge configuration failed validation.") from exc
