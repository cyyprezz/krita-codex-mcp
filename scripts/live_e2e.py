from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from krita_codex_mcp.bridge_client import BridgeCallError, BridgeClient
from krita_codex_protocol import BridgeConfig, PathPolicy, PathPolicyError

DOCUMENT_NAME = "Codex Core E2E"


def _find_layer(layers: list[dict[str, Any]], layer_id: str) -> dict[str, Any] | None:
    for layer in layers:
        if layer.get("id") == layer_id:
            return layer
        found = _find_layer(layer.get("children", []), layer_id)
        if found is not None:
            return found
    return None


def _decode_preview(result: dict[str, Any], destination: Path) -> str:
    payload = base64.b64decode(result["png_base64"], validate=True)
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Krita preview is not a PNG")
    destination.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


async def _expect_error(
    client: BridgeClient,
    operation: str,
    arguments: dict[str, Any],
    expected_code: str,
) -> None:
    try:
        await client.execute(operation, arguments)
    except BridgeCallError as exc:
        if exc.code != expected_code:
            raise RuntimeError(
                f"Expected {expected_code} from {operation}, received {exc.code}"
            ) from exc
    else:
        raise RuntimeError(f"Expected {expected_code} from {operation}")


def _check_wrong_token(config: BridgeConfig) -> None:
    request = urllib.request.Request(
        config.execute_url,
        data=json.dumps(
            {
                "protocol_version": 1,
                "request_id": "live-auth-check",
                "operation": "state",
                "arguments": {},
            }
        ).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {'x' * 32}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        urllib.request.urlopen(request, timeout=config.request_timeout_seconds)
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        if exc.code == 401 and body.get("error", {}).get("code") == "UNAUTHORIZED":
            return
        raise RuntimeError(f"Unexpected authentication response: HTTP {exc.code}") from exc
    raise RuntimeError("Bridge accepted an invalid authentication token")


async def _cleanup_stale(client: BridgeClient) -> None:
    listed = await client.execute("document", {"action": "list"})
    for document in listed["documents"]:
        if document["name"] == DOCUMENT_NAME:
            await client.execute(
                "document",
                {
                    "action": "close",
                    "document_id": document["document_id"],
                    "discard_changes": True,
                },
            )


async def run(output_dir: Path) -> dict[str, Any]:
    config = BridgeConfig.load()
    try:
        safe_output = (
            PathPolicy(config.allowed_roots)
            .validate_output(
                str(output_dir / "path-check.tmp"),
                extensions={".tmp"},
                overwrite=True,
                require_parent=False,
            )
            .parent
        )
    except PathPolicyError as exc:
        raise RuntimeError("--output-dir must be beneath a configured allowed root") from exc
    safe_output.mkdir(parents=True, exist_ok=True)

    client = BridgeClient(config)
    await asyncio.to_thread(_check_wrong_token, config)
    await _cleanup_stale(client)
    created_document = await client.execute(
        "document",
        {
            "action": "create",
            "width": 512,
            "height": 512,
            "name": DOCUMENT_NAME,
            "color_model": "RGBA",
            "color_depth": "U8",
            "profile": "",
            "resolution": 72.0,
        },
    )
    document_id = created_document["affected_document_id"]
    state = await client.execute("state", {"document_id": document_id})
    if not state["capabilities"]["native_paint_line"]:
        raise RuntimeError("This Krita build does not expose Node.paintLine")

    before = await client.execute("preview", {"max_width": 512, "max_height": 512})
    before_hash = _decode_preview(before, safe_output / "live-preview-before.png")

    layers = await client.execute("layers", {"action": "read"})
    original_layer_count = len(layers["layers"])
    created = await client.execute(
        "layers",
        {"action": "create", "kind": "paintlayer", "name": "Codex E2E Paint"},
    )
    layer_id = created["affected_layer_id"]
    await client.execute(
        "layers", {"action": "rename", "layer_id": layer_id, "name": "Codex E2E Native Paint"}
    )
    hidden = await client.execute(
        "layers",
        {"action": "set_properties", "layer_id": layer_id, "visible": False, "opacity": 0.5},
    )
    hidden_layer = _find_layer(hidden["layers"], layer_id)
    if (
        hidden_layer is None
        or hidden_layer["visible"]
        or abs(hidden_layer["opacity"] - 0.5) > (1 / 255)
    ):
        raise RuntimeError("Layer visibility/opacity update did not round-trip")
    await client.execute(
        "layers",
        {"action": "set_properties", "layer_id": layer_id, "visible": True, "opacity": 1.0},
    )

    await _expect_error(
        client,
        "paint_path",
        {
            "layer_id": layer_id,
            "preset_name": "__krita_codex_missing_preset__",
            "points": [{"x": 40, "y": 40}, {"x": 100, "y": 100}],
        },
        "PRESET_NOT_FOUND",
    )
    await _expect_error(
        client,
        "paint_path",
        {
            "layer_id": layer_id,
            "points": [{"x": -1, "y": 40}, {"x": 100, "y": 100}],
        },
        "INVALID_ARGUMENT",
    )
    group = await client.execute(
        "layers", {"action": "create", "kind": "grouplayer", "name": "Codex E2E Group"}
    )
    await _expect_error(
        client,
        "paint_path",
        {
            "layer_id": group["affected_layer_id"],
            "points": [{"x": 40, "y": 40}, {"x": 100, "y": 100}],
        },
        "LAYER_NOT_PAINTABLE",
    )
    try:
        painted = await client.execute(
            "paint_path",
            {
                "layer_id": layer_id,
                "brush_size": 36.0,
                "points": [
                    {"x": 64, "y": 384, "pressure": 0.25},
                    {"x": 256, "y": 96, "pressure": 1.0},
                    {"x": 448, "y": 384, "pressure": 0.45},
                ],
            },
        )
    except BridgeCallError as exc:
        raise RuntimeError(
            json.dumps(
                {"code": exc.code, "message": exc.message, "details": dict(exc.details)},
                ensure_ascii=False,
            )
        ) from exc
    if not painted["native_brush_engine"] or painted["segments"] != 2:
        raise RuntimeError("Native painting did not report the expected two segments")

    after = await client.execute("preview", {"max_width": 512, "max_height": 512})
    painted_hash = _decode_preview(after, safe_output / "live-preview-painted.png")
    if painted_hash == before_hash:
        raise RuntimeError("Painted preview is identical to the blank preview")

    kra_path = safe_output / "krita-codex-live-e2e.kra"
    png_path = safe_output / "krita-codex-live-e2e.png"
    saved = await client.execute(
        "save_export",
        {"kra_path": str(kra_path), "png_path": str(png_path), "overwrite": True},
    )
    if not kra_path.is_file() or not png_path.is_file():
        raise RuntimeError("Krita did not create both requested output files")
    await _expect_error(
        client,
        "save_export",
        {"png_path": str(png_path), "overwrite": False},
        "PATH_NOT_ALLOWED",
    )
    await _expect_error(
        client,
        "save_export",
        {
            "png_path": str(
                Path(config.allowed_roots[0].anchor) / "krita-codex-outside-allowed-root.png"
            )
        },
        "PATH_NOT_ALLOWED",
    )

    final_state = await client.execute("state", {"document_id": document_id})
    result = {
        "krita_version": final_state["krita_version"],
        "canvas": [final_state["document"]["width"], final_state["document"]["height"]],
        "original_top_level_layers": original_layer_count,
        "test_layer_id": layer_id,
        "preset": painted["preset"],
        "segments": painted["segments"],
        "undo_steps": painted["undo_steps"],
        "before_preview_sha256": before_hash,
        "painted_preview_sha256": painted_hash,
        "saved_outputs": saved["outputs"],
        "negative_checks": [
            "wrong_token",
            "missing_preset",
            "out_of_bounds",
            "group_not_paintable",
            "overwrite_blocked",
            "outside_root_blocked",
        ],
    }
    await client.execute(
        "document",
        {"action": "close", "document_id": document_id, "discard_changes": True},
    )
    return result


async def run_with_cleanup(output_dir: Path) -> dict[str, Any]:
    try:
        return await run(output_dir)
    except Exception:
        await _cleanup_stale(BridgeClient(BridgeConfig.load()))
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the live Krita bridge smoke test")
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    result = asyncio.run(run_with_cleanup(arguments.output_dir.resolve()))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
