from __future__ import annotations

import base64
import time
from pathlib import Path
from uuid import uuid4

from krita_codex_protocol import PROTOCOL_VERSION, BridgeConfig, ConfigError

from .bridge import BridgeAPI
from .configuration import resolve_allowed_roots
from .errors import BridgeRequestError, InstallerError
from .manifest import verify_installed_payload
from .paths import InstallPaths

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _validate_png(encoded: object) -> None:
    if not isinstance(encoded, str):
        raise InstallerError("The Krita bridge preview did not contain PNG data.")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise InstallerError("The Krita bridge preview contained invalid base64 data.") from exc
    if not decoded.startswith(PNG_SIGNATURE):
        raise InstallerError("The Krita bridge preview did not contain a valid PNG.")


def _validate_runtime_versions(result: dict[str, object], distribution_version: str) -> None:
    if result.get("bridge_distribution_version") != distribution_version:
        raise InstallerError(
            "The bridge loaded in Krita does not match the installer and wheel version."
        )
    if result.get("bridge_protocol_version") != PROTOCOL_VERSION:
        raise InstallerError("The bridge loaded in Krita uses a different protocol version.")


def run_offline_preflight(paths: InstallPaths, distribution_version: str) -> BridgeConfig:
    try:
        config = BridgeConfig.load(paths.config_file)
    except ConfigError as exc:
        raise InstallerError("The bridge configuration failed validation.") from exc
    resolve_allowed_roots(config.allowed_roots)
    verify_installed_payload(paths, distribution_version)
    return config


def run_live_preflight(paths: InstallPaths, distribution_version: str) -> None:
    config = run_offline_preflight(paths, distribution_version)
    api = BridgeAPI(config)
    documents = api.execute("document", {"action": "list"})
    _validate_runtime_versions(documents, distribution_version)
    original_document = documents.get("active_document_id")
    temporary_document: str | None = None
    export_path: Path | None = None
    check_id = uuid4().hex
    caught: Exception | None = None
    try:
        created = api.execute(
            "document",
            {
                "action": "create",
                "width": 64,
                "height": 64,
                "name": f"Krita Codex Preflight {check_id}",
                "color_model": "RGBA",
                "color_depth": "U8",
                "profile": "",
                "resolution": 72,
            },
        )
        _validate_runtime_versions(created, distribution_version)
        candidate = created.get("affected_document_id")
        if not isinstance(candidate, str) or not candidate:
            raise InstallerError("The preflight document was not created safely.")
        temporary_document = candidate
        state = api.execute("state", {"document_id": temporary_document})
        _validate_runtime_versions(state, distribution_version)
        preview = api.execute(
            "preview",
            {
                "max_width": 64,
                "max_height": 64,
                "scale": 1,
                "document_id": temporary_document,
            },
        )
        _validate_runtime_versions(preview, distribution_version)
        _validate_png(preview.get("png_base64"))
        export_path = config.allowed_roots[0] / f".krita-codex-preflight-{check_id}.png"
        exported = api.execute(
            "save_export",
            {
                "png_path": str(export_path),
                "overwrite": False,
                "document_id": temporary_document,
            },
        )
        _validate_runtime_versions(exported, distribution_version)
        if not export_path.is_file() or not export_path.read_bytes().startswith(PNG_SIGNATURE):
            raise InstallerError("The preflight export did not create a valid PNG.")
    except Exception as exc:
        caught = exc
    finally:
        cleanup_failure = _cleanup_live_check(
            api,
            temporary_document=temporary_document,
            original_document=(
                original_document if isinstance(original_document, str) else None
            ),
            export_path=export_path,
        )
        if caught is None and cleanup_failure is not None:
            caught = cleanup_failure
    if caught is not None:
        if isinstance(caught, InstallerError):
            raise caught
        raise InstallerError("The live Krita preflight failed.") from caught


def _cleanup_live_check(
    api: BridgeAPI,
    *,
    temporary_document: str | None,
    original_document: str | None,
    export_path: Path | None,
) -> Exception | None:
    failure: Exception | None = None
    if temporary_document:
        deadline = time.monotonic() + api.config.export_timeout_seconds
        while True:
            try:
                api.execute(
                    "document",
                    {
                        "action": "close",
                        "document_id": temporary_document,
                        "discard_changes": True,
                    },
                )
                break
            except BridgeRequestError as exc:
                if exc.code == "BRIDGE_BUSY" and time.monotonic() < deadline:
                    time.sleep(1.0)
                    continue
                failure = exc
                break
            except Exception as exc:
                failure = exc
                break
    if original_document:
        try:
            api.execute(
                "document",
                {"action": "activate", "document_id": original_document},
            )
        except Exception as exc:
            failure = failure or exc
    if export_path and export_path.exists():
        try:
            export_path.unlink()
        except OSError as exc:
            failure = failure or exc
    return failure
