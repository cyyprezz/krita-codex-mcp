from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import struct
from pathlib import Path

from krita_codex_mcp.bridge_client import BridgeCallError, BridgeClient
from krita_codex_protocol import BridgeConfig


def flatten_layers(layers: list[dict[str, object]]) -> list[dict[str, object]]:
    flattened: list[dict[str, object]] = []
    for layer in layers:
        flattened.append(layer)
        children = layer.get("children")
        if isinstance(children, list):
            flattened.extend(flatten_layers(children))
    return flattened


async def cleanup_stale_test_document() -> dict[str, object]:
    client = BridgeClient(BridgeConfig.load())
    listed = await client.execute("document", {"action": "list"})
    closed: list[str] = []
    for document in listed["documents"]:
        if document["name"] in {"Codex Expansion E2E", "Codex Expansion Decoy"}:
            await client.execute(
                "document",
                {
                    "action": "close",
                    "document_id": document["document_id"],
                    "discard_changes": True,
                },
            )
            closed.append(document["document_id"])
    return {"closed_stale_documents": closed}


async def run(output_dir: Path) -> dict[str, object]:
    config = BridgeConfig.load()
    client = BridgeClient(config)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    kra_path = output_dir / "expansion-live.kra"
    png_path = output_dir / "expansion-live.png"
    frame_path = output_dir / "expansion-frame-group.png"
    cropped_frame_path = output_dir / "expansion-frame-group-cropped.png"
    animation_package_path = output_dir / "expansion-animation"

    before = await client.execute("document", {"action": "list"})
    created = await client.execute(
        "document",
        {
            "action": "create",
            "width": 320,
            "height": 240,
            "name": "Codex Expansion E2E",
            "color_model": "RGBA",
            "color_depth": "U8",
            "profile": "",
            "resolution": 144.0,
        },
    )
    document_id = created["affected_document_id"]
    brush_before = await client.execute(
        "brush", {"action": "read", "document_id": document_id}
    )
    decoy = await client.execute(
        "document",
        {
            "action": "create",
            "width": 64,
            "height": 64,
            "name": "Codex Expansion Decoy",
            "color_model": "RGBA",
            "color_depth": "U8",
            "profile": "",
            "resolution": 72.0,
        },
    )
    decoy_id = decoy["affected_document_id"]
    targeted_state = await client.execute("state", {"document_id": document_id})
    if targeted_state["document"]["name"] != "Codex Expansion E2E":
        raise RuntimeError("Targeted state returned the wrong document")
    if targeted_state["document_active"]:
        raise RuntimeError("Decoy document was expected to remain active during targeted state")
    listed = await client.execute("document", {"action": "list"})
    if document_id not in {item["document_id"] for item in listed["documents"]}:
        raise RuntimeError("Created document ID was not stable in the document list")

    presets = await client.execute(
        "brush",
        {
            "action": "list_presets",
            "document_id": document_id,
            "query": "Basic-5",
            "limit": 20,
        },
    )
    if not presets["presets"]:
        raise RuntimeError("No Basic-5 preset was found")
    preset = presets["presets"][0]
    brush_set = await client.execute(
        "brush",
        {
            "action": "set",
            "document_id": document_id,
            "preset_name": preset,
            "brush_size": 18.0,
            "opacity": 0.8,
            "flow": 0.7,
            "rotation": 15.0,
            "eraser_mode": False,
            "disable_pressure": False,
            "foreground": "#3366CC",
        },
    )
    layer = await client.execute(
        "layers",
        {
            "action": "create",
            "document_id": document_id,
            "kind": "paintlayer",
            "name": "Expansion Paint",
        },
    )
    layer_id = layer["affected_layer_id"]
    painted = await client.execute(
        "paint_path",
        {
            "layer_id": layer_id,
            "document_id": document_id,
            "preset_name": preset,
            "brush_size": 18.0,
            "points": [
                {"x": 40, "y": 180, "pressure": 0.25},
                {"x": 160, "y": 60, "pressure": 1.0},
                {"x": 280, "y": 180, "pressure": 0.5},
            ],
        },
    )
    undo = await client.execute(
        "history", {"action": "undo", "steps": 2, "document_id": document_id}
    )
    redo = await client.execute(
        "history", {"action": "redo", "steps": 2, "document_id": document_id}
    )
    if undo["completed_steps"] != 2 or redo["completed_steps"] != 2:
        raise RuntimeError(f"Unexpected undo/redo result: undo={undo}, redo={redo}")

    duplicate = await client.execute(
        "layers",
        {
            "action": "duplicate",
            "document_id": document_id,
            "layer_id": layer_id,
            "name": "Expansion Paint Offset",
        },
    )
    duplicate_id = duplicate["affected_layer_id"]
    translated = await client.execute(
        "layers",
        {
            "action": "translate",
            "document_id": document_id,
            "layer_id": duplicate_id,
            "offset_x": 12,
            "offset_y": -8,
        },
    )
    group = await client.execute(
        "layers",
        {
            "action": "create",
            "document_id": document_id,
            "kind": "grouplayer",
            "name": "Animation Frames",
        },
    )
    group_id = group["affected_layer_id"]
    reordered = await client.execute(
        "layers",
        {
            "action": "reorder",
            "document_id": document_id,
            "layer_id": duplicate_id,
            "parent_id": group_id,
        },
    )
    reordered_layers = flatten_layers(reordered["layers"])
    moved_layer = next(item for item in reordered_layers if item["id"] == duplicate_id)
    if moved_layer["parent_id"] != group_id:
        raise RuntimeError(
            f"Duplicated layer did not move into the requested group: {moved_layer}"
        )
    if moved_layer["position"] != {"x": 12, "y": -8}:
        raise RuntimeError(f"Unexpected translated position: {moved_layer['position']}")
    if translated["affected_layer_id"] != duplicate_id:
        raise RuntimeError("Translated layer ID changed unexpectedly")
    await client.execute(
        "layers",
        {
            "action": "reorder",
            "document_id": document_id,
            "layer_id": layer_id,
            "parent_id": group_id,
        },
    )

    asset = await client.execute(
        "asset",
        {
            "action": "export_layer",
            "document_id": document_id,
            "layer_id": group_id,
            "output_path": str(frame_path),
            "crop": "canvas",
            "overwrite": True,
            "preview_max_width": 320,
            "preview_max_height": 240,
        },
    )
    preview_bytes = base64.b64decode(asset["png_base64"], validate=True)
    if not preview_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Layer export did not return a direct PNG preview")
    frame_bytes = frame_path.read_bytes()
    if not frame_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Layer export did not create a PNG file")
    frame_dimensions = struct.unpack(">II", frame_bytes[16:24])
    if frame_dimensions != (320, 240):
        raise RuntimeError(f"Unexpected full-canvas frame dimensions: {frame_dimensions}")
    if frame_bytes[25] not in {4, 6}:
        raise RuntimeError("Layer export PNG does not contain an alpha channel")

    cropped_asset = await client.execute(
        "asset",
        {
            "action": "export_layer",
            "document_id": document_id,
            "layer_id": group_id,
            "output_path": str(cropped_frame_path),
            "crop": "content",
            "overwrite": True,
            "preview_max_width": 320,
            "preview_max_height": 240,
        },
    )
    cropped_bytes = cropped_frame_path.read_bytes()
    cropped_dimensions = struct.unpack(">II", cropped_bytes[16:24])
    cropped_rect = cropped_asset["export_rect"]
    if cropped_dimensions != (cropped_rect["width"], cropped_rect["height"]):
        raise RuntimeError(
            f"Content crop dimensions do not match metadata: {cropped_dimensions}"
        )
    if not 0 < cropped_dimensions[0] < 320 or not 0 < cropped_dimensions[1] < 240:
        raise RuntimeError(f"Content crop did not reduce the canvas: {cropped_dimensions}")
    if cropped_bytes[25] not in {4, 6}:
        raise RuntimeError("Content-cropped PNG does not contain an alpha channel")

    animation_arguments = {
        "action": "export_animation",
        "document_id": document_id,
        "layer_id": group_id,
        "output_path": str(animation_package_path),
        "crop": "union",
        "overwrite": True,
        "frame_ids": [layer_id, duplicate_id],
        "animation_name": "Hero Test",
        "columns": 2,
        "padding": 2,
        "frame_duration_ms": 125,
        "pivot_x": 0.5,
        "pivot_y": 0.1,
        "pixels_per_unit": 32,
        "loop": True,
        "preview_max_width": 640,
        "preview_max_height": 240,
    }
    animation = await client.execute("asset", animation_arguments)
    animation_preview = base64.b64decode(animation["png_base64"], validate=True)
    if not animation_preview.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Animation export did not return a direct spritesheet preview")
    manifest_path = animation_package_path / "manifest.json"
    sheet_path = animation_package_path / "spritesheet.png"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["schema"] != "dev.desens.krita-codex.unity-animation":
        raise RuntimeError(f"Unexpected animation manifest schema: {manifest['schema']}")
    if [frame["layer_id"] for frame in manifest["frames"]] != [layer_id, duplicate_id]:
        raise RuntimeError("Explicit animation frame order was not preserved")
    if manifest["animation"]["frame_count"] != 2 or not manifest["animation"]["loop"]:
        raise RuntimeError("Animation timing metadata is incomplete")
    if hashlib.sha256(sheet_path.read_bytes()).hexdigest() != manifest["sheet"]["sha256"]:
        raise RuntimeError("Spritesheet checksum does not match the manifest")
    for frame in manifest["frames"]:
        top = frame["sheet_rect_png_top_left"]
        unity = frame["sprite_rect_unity_bottom_left"]
        expected_y = manifest["sheet"]["height"] - top["y"] - top["height"]
        if unity["y"] != expected_y:
            raise RuntimeError("Unity bottom-left sprite rectangle conversion is incorrect")

    sheet_meta_path = animation_package_path / "spritesheet.png.meta"
    frame_meta_path = animation_package_path / "frames" / "0000.png.meta"
    sheet_meta_path.write_text("stable-sheet-guid", encoding="utf-8")
    frame_meta_path.write_text("stable-frame-guid", encoding="utf-8")
    overwritten_animation = await client.execute("asset", animation_arguments)
    if sheet_meta_path.read_text(encoding="utf-8") != "stable-sheet-guid":
        raise RuntimeError("Spritesheet Unity .meta file was not preserved during overwrite")
    if frame_meta_path.read_text(encoding="utf-8") != "stable-frame-guid":
        raise RuntimeError("Frame Unity .meta file was not preserved during overwrite")

    animation_non_overwrite_refused = False
    try:
        await client.execute("asset", {**animation_arguments, "overwrite": False})
    except BridgeCallError as exc:
        if exc.code != "PATH_NOT_ALLOWED":
            raise
        animation_non_overwrite_refused = True
    if not animation_non_overwrite_refused:
        raise RuntimeError("Existing animation package was replaced without overwrite=true")

    disposable = await client.execute(
        "layers",
        {
            "action": "duplicate",
            "document_id": document_id,
            "layer_id": layer_id,
            "name": "Delete Me",
        },
    )
    deleted_id = disposable["affected_layer_id"]
    deleted = await client.execute(
        "layers",
        {"action": "delete", "document_id": document_id, "layer_id": deleted_id},
    )
    if deleted["deleted_layer_id"] != deleted_id:
        raise RuntimeError("Deleted layer ID was not reported")
    if any(item["id"] == deleted_id for item in flatten_layers(deleted["layers"])):
        raise RuntimeError("Deleted layer is still present in the returned layer tree")

    previous = brush_before.get("painting") or {}
    restore = {"action": "set"}
    brush_size = previous.get("brush_size")
    if isinstance(brush_size, (int, float)) and 0.1 <= brush_size <= 1000.0:
        restore["brush_size"] = brush_size
    for field in ("opacity", "flow"):
        value = previous.get(field)
        if isinstance(value, (int, float)) and 0.0 <= value <= 1.0:
            restore[field] = value
    rotation = previous.get("rotation")
    if isinstance(rotation, (int, float)) and -3600.0 <= rotation <= 3600.0:
        restore["rotation"] = rotation
    for field in ("eraser_mode", "disable_pressure"):
        value = previous.get(field)
        if isinstance(value, bool):
            restore[field] = value
    if previous.get("preset"):
        restore["preset_name"] = previous["preset"]
    foreground = previous.get("foreground") or {}
    if foreground.get("hex"):
        restore["foreground"] = foreground["hex"]
    if len(restore) > 1:
        restore["document_id"] = document_id
        await client.execute("brush", restore)

    unsaved_refused = False
    try:
        await client.execute(
            "document",
            {"action": "close", "document_id": document_id, "discard_changes": False},
        )
    except BridgeCallError as exc:
        if exc.code != "UNSAVED_CHANGES":
            raise
        unsaved_refused = True
    if not unsaved_refused:
        raise RuntimeError("Modified document closed without explicit discard permission")

    saved = await client.execute(
        "save_export",
        {
            "kra_path": str(kra_path),
            "png_path": str(png_path),
            "overwrite": True,
            "document_id": document_id,
        },
    )
    await client.execute(
        "layers",
        {
            "action": "rename",
            "document_id": document_id,
            "layer_id": layer_id,
            "name": "Expansion Paint Checkpointed",
        },
    )
    checkpoint = await client.execute(
        "document", {"action": "save", "document_id": document_id}
    )
    await client.execute(
        "document",
        {"action": "close", "document_id": document_id, "discard_changes": False},
    )
    opened = await client.execute("document", {"action": "open", "path": str(kra_path)})
    opened_id = opened["affected_document_id"]
    reopened_layers = await client.execute(
        "layers", {"action": "read", "document_id": opened_id}
    )
    reopened_names = {item["name"] for item in flatten_layers(reopened_layers["layers"])}
    expected_names = {
        "Expansion Paint Checkpointed",
        "Expansion Paint Offset",
        "Animation Frames",
    }
    if not expected_names.issubset(reopened_names):
        raise RuntimeError(
            f"Saved layer structure did not survive reopen: names={sorted(reopened_names)}"
        )
    await client.execute(
        "document", {"action": "close", "document_id": opened_id, "discard_changes": False}
    )
    await client.execute(
        "document", {"action": "close", "document_id": decoy_id, "discard_changes": True}
    )

    return {
        "documents_before": len(before["documents"]),
        "created_document_id": document_id,
        "decoy_document_id": decoy_id,
        "targeted_state_while_inactive": not targeted_state["document_active"],
        "preset_matches": presets["total"],
        "brush_after_set": brush_set["painting"],
        "painted_segments": painted["segments"],
        "undo_steps": undo["completed_steps"],
        "redo_steps": redo["completed_steps"],
        "duplicated_layer_id": duplicate_id,
        "translated_position": moved_layer["position"],
        "group_layer_id": group_id,
        "frame_export": asset["output"],
        "frame_export_dimensions": list(frame_dimensions),
        "frame_preview_bytes": len(preview_bytes),
        "cropped_frame_export": cropped_asset["output"],
        "cropped_frame_dimensions": list(cropped_dimensions),
        "animation_package": animation["output"],
        "animation_frame_count": animation["frame_count"],
        "animation_sheet": animation["sheet"],
        "animation_preview_bytes": len(animation_preview),
        "animation_overwrite_bytes": overwritten_animation["output"]["bytes"],
        "animation_meta_preserved": True,
        "animation_non_overwrite_refused": animation_non_overwrite_refused,
        "deleted_layer_id": deleted_id,
        "unsaved_close_refused": unsaved_refused,
        "outputs": saved["outputs"],
        "checkpoint_path": checkpoint["saved_path"],
        "reopened_document_id": opened_id,
        "reopened_layer_names": sorted(reopened_names),
    }


async def run_with_cleanup(output_dir: Path) -> dict[str, object]:
    try:
        return await run(output_dir)
    except Exception:
        await cleanup_stale_test_document()
        raise


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Exercise document/history/brush expansion")
    parser.add_argument("--output-dir", type=Path, default=repo_root / "e2e-output")
    parser.add_argument("--cleanup-only", action="store_true")
    arguments = parser.parse_args()
    operation = (
        cleanup_stale_test_document()
        if arguments.cleanup_only
        else run_with_cleanup(arguments.output_dir)
    )
    result = asyncio.run(operation)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
