from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from krita_codex_protocol import PROTOCOL_VERSION, BridgeConfig, ConfigError

from .bridge import BridgeAPI
from .configuration import resolve_allowed_roots
from .errors import BridgeRequestError, InstallerError
from .manifest import verify_installed_payload
from .paths import InstallPaths

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
COMPATIBILITY_PROFILE_VERSION = 1


@dataclass(frozen=True)
class LivePreflightResult:
    krita_version: str
    status: str
    validation: str
    profile_version: int
    issues: tuple[dict[str, Any], ...]


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


def _validate_compatibility_report(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise InstallerError("The running Krita plugin returned no compatibility report.")
    if payload.get("profile_version") != COMPATIBILITY_PROFILE_VERSION:
        raise InstallerError("The running Krita plugin uses an unsupported compatibility profile.")
    status = payload.get("status")
    if status not in {"compatible", "limited", "incompatible", "unknown"}:
        raise InstallerError("The running Krita plugin returned an invalid compatibility status.")
    version = payload.get("krita_version")
    if not isinstance(version, str) or not version.strip():
        raise InstallerError("The running Krita plugin did not report its Krita version.")
    if payload.get("validation") not in {"tested_build", "capability_checked"}:
        raise InstallerError("The running Krita plugin returned an invalid validation mode.")
    issues = payload.get("issues")
    if not isinstance(issues, list) or not all(isinstance(issue, dict) for issue in issues):
        raise InstallerError("The running Krita plugin returned invalid compatibility issues.")
    for issue in issues:
        affected_tools = issue.get("affected_tools")
        if (
            issue.get("severity") not in {"warning", "critical"}
            or not isinstance(issue.get("code"), str)
            or not isinstance(issue.get("feature"), str)
            or not isinstance(issue.get("message"), str)
            or not isinstance(affected_tools, list)
            or not all(isinstance(tool, str) for tool in affected_tools)
        ):
            raise InstallerError(
                "The running Krita plugin returned a malformed compatibility issue."
            )
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict) or not all(
        isinstance(name, str) and (value is None or isinstance(value, bool))
        for name, value in capabilities.items()
    ):
        raise InstallerError("The running Krita plugin returned invalid capability values.")
    if payload.get("evaluation_complete") is not True or status == "unknown":
        raise InstallerError(
            f"Krita {version} could not complete the required runtime capability check."
        )
    critical = [issue for issue in issues if issue.get("severity") == "critical"]
    if status == "incompatible" or critical:
        features = ", ".join(
            str(issue.get("feature") or issue.get("code") or "unknown")
            for issue in critical
        )
        raise InstallerError(
            f"Krita {version} is incompatible with this MCP release"
            + (f": {features}." if features else ".")
        )
    return payload


def run_offline_preflight(paths: InstallPaths, distribution_version: str) -> BridgeConfig:
    try:
        config = BridgeConfig.load(paths.config_file)
    except ConfigError as exc:
        raise InstallerError("The bridge configuration failed validation.") from exc
    resolve_allowed_roots(config.allowed_roots)
    verify_installed_payload(paths, distribution_version)
    return config


def run_live_preflight(
    paths: InstallPaths, distribution_version: str
) -> LivePreflightResult:
    config = run_offline_preflight(paths, distribution_version)
    api = BridgeAPI(config)
    documents = api.execute("document", {"action": "list"})
    _validate_runtime_versions(documents, distribution_version)
    original_document = documents.get("active_document_id")
    temporary_document: str | None = None
    temporary_paths: list[Path] = []
    check_id = uuid4().hex
    caught: Exception | None = None
    result: LivePreflightResult | None = None
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
        probe_layer_id = _create_probe_layer(
            api,
            temporary_document,
            distribution_version,
            check_id,
        )
        state = api.execute("state", {"document_id": temporary_document})
        _validate_runtime_versions(state, distribution_version)
        state_compatibility = _validate_compatibility_report(
            state.get("runtime_compatibility")
        )
        compatibility_response = api.execute(
            "document",
            {"action": "compatibility", "document_id": temporary_document},
        )
        _validate_runtime_versions(compatibility_response, distribution_version)
        compatibility = _validate_compatibility_report(
            compatibility_response.get("runtime_compatibility")
        )
        if state_compatibility != compatibility:
            raise InstallerError(
                "krita_state and krita_document returned inconsistent compatibility reports."
            )
        issues = [dict(issue) for issue in compatibility["issues"]]
        _run_optional_runtime_probes(
            api,
            document_id=temporary_document,
            layer_id=probe_layer_id,
            distribution_version=distribution_version,
            check_id=check_id,
            issues=issues,
            capabilities=compatibility.get("capabilities", {}),
        )
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
        png_path = config.allowed_roots[0] / f".krita-codex-preflight-{check_id}.png"
        kra_path = config.allowed_roots[0] / f".krita-codex-preflight-{check_id}.kra"
        temporary_paths.extend((png_path, kra_path))
        exported = api.execute(
            "save_export",
            {
                "png_path": str(png_path),
                "kra_path": str(kra_path),
                "overwrite": False,
                "document_id": temporary_document,
            },
        )
        _validate_runtime_versions(exported, distribution_version)
        if not png_path.is_file() or not png_path.read_bytes().startswith(PNG_SIGNATURE):
            raise InstallerError("The preflight export did not create a valid PNG.")
        if not kra_path.is_file() or kra_path.stat().st_size == 0:
            raise InstallerError("The preflight export did not create a valid KRA file.")
        status = "limited" if issues else "compatible"
        result = LivePreflightResult(
            krita_version=str(compatibility["krita_version"]),
            status=status,
            validation=str(compatibility.get("validation", "capability_checked")),
            profile_version=int(compatibility["profile_version"]),
            issues=tuple(issues),
        )
    except Exception as exc:
        caught = exc
    finally:
        cleanup_failure = _cleanup_live_check(
            api,
            temporary_document=temporary_document,
            original_document=(
                original_document if isinstance(original_document, str) else None
            ),
            temporary_paths=temporary_paths,
        )
        if caught is None and cleanup_failure is not None:
            caught = cleanup_failure
    if caught is not None:
        if isinstance(caught, InstallerError):
            raise caught
        raise InstallerError("The live Krita preflight failed.") from caught
    if result is None:  # pragma: no cover - guarded by the exception path above.
        raise InstallerError("The live Krita preflight did not produce a result.")
    return result


def _create_probe_layer(
    api: BridgeAPI,
    document_id: str,
    distribution_version: str,
    check_id: str,
) -> str:
    created = api.execute(
        "layers",
        {
            "action": "create",
            "document_id": document_id,
            "kind": "paintlayer",
            "name": f"Compatibility Paint {check_id}",
            "response_mode": "compact",
        },
    )
    _validate_runtime_versions(created, distribution_version)
    layer_id = created.get("affected_layer_id")
    if not isinstance(layer_id, str) or not layer_id:
        raise InstallerError("Krita could not create the compatibility-check paint layer.")
    return layer_id


def _run_optional_runtime_probes(
    api: BridgeAPI,
    *,
    document_id: str,
    layer_id: str,
    distribution_version: str,
    check_id: str,
    issues: list[dict[str, Any]],
    capabilities: object,
) -> None:
    draw_succeeded = False
    native_line_available = (
        isinstance(capabilities, dict)
        and capabilities.get("native_paint_line") is True
    )
    if native_line_available:
        try:
            drawn = api.execute(
                "draw",
                {
                    "document_id": document_id,
                    "layer_id": layer_id,
                    "shape": "line",
                    "points": [
                        {"x": 8, "y": 8, "pressure": 1.0},
                        {"x": 48, "y": 48, "pressure": 1.0},
                    ],
                    "stroke_style": "ForegroundColor",
                    "fill_style": "None",
                    "brush_size": 4.0,
                },
            )
            _validate_runtime_versions(drawn, distribution_version)
            if drawn.get("verified_content_change") is True:
                draw_succeeded = True
            else:
                _append_probe_issue(
                    issues,
                    code="NATIVE_DRAW_PROBE_UNVERIFIED",
                    feature="native_drawing",
                    affected_tools=("krita_paint_path", "krita_draw"),
                    message="The native drawing probe returned no verified content change.",
                )
        except BridgeRequestError as exc:
            _append_probe_issue(
                issues,
                code="NATIVE_DRAW_PROBE_FAILED",
                feature="native_drawing",
                affected_tools=("krita_paint_path", "krita_draw"),
                message=f"{exc.code}: {exc}",
            )

    if draw_succeeded:
        try:
            for action in ("undo", "redo"):
                history = api.execute(
                    "history",
                    {"action": action, "steps": 1, "document_id": document_id},
                )
                _validate_runtime_versions(history, distribution_version)
        except BridgeRequestError as exc:
            _append_probe_issue(
                issues,
                code="HISTORY_PROBE_FAILED",
                feature="native_history",
                affected_tools=("krita_history",),
                message=f"{exc.code}: {exc}",
            )

    try:
        group = api.execute(
            "layers",
            {
                "action": "create",
                "document_id": document_id,
                "kind": "grouplayer",
                "name": f"Compatibility Group {check_id}",
                "response_mode": "compact",
            },
        )
        _validate_runtime_versions(group, distribution_version)
        group_id = group.get("affected_layer_id")
        if not isinstance(group_id, str) or not group_id:
            _append_probe_issue(
                issues,
                code="LAYER_WORKFLOW_PROBE_UNVERIFIED",
                feature="verified_layer_reparenting",
                affected_tools=("krita_layers",),
                message="Krita did not identify the compatibility-check group.",
            )
            return
        moved = api.execute(
            "layers",
            {
                "action": "reorder",
                "document_id": document_id,
                "layer_id": layer_id,
                "parent_id": group_id,
                "response_mode": "compact",
            },
        )
        _validate_runtime_versions(moved, distribution_version)
        affected = moved.get("affected_layer")
        if not isinstance(affected, dict) or affected.get("parent_id") != group_id:
            _append_probe_issue(
                issues,
                code="LAYER_WORKFLOW_PROBE_UNVERIFIED",
                feature="verified_layer_reparenting",
                affected_tools=("krita_layers",),
                message="Krita did not verify the compatibility-check layer move.",
            )
    except BridgeRequestError as exc:
        _append_probe_issue(
            issues,
            code="LAYER_WORKFLOW_PROBE_FAILED",
            feature="verified_layer_reparenting",
            affected_tools=("krita_layers",),
            message=f"{exc.code}: {exc}",
        )


def _append_probe_issue(
    issues: list[dict[str, Any]],
    *,
    code: str,
    feature: str,
    affected_tools: tuple[str, ...],
    message: str,
) -> None:
    issues.append(
        {
            "severity": "warning",
            "code": code,
            "feature": feature,
            "affected_tools": list(affected_tools),
            "message": message,
        }
    )
def _cleanup_live_check(
    api: BridgeAPI,
    *,
    temporary_document: str | None,
    original_document: str | None,
    temporary_paths: list[Path],
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
    for path in temporary_paths:
        if path.exists():
            try:
                path.unlink()
            except OSError as exc:
                failure = failure or exc
    return failure
