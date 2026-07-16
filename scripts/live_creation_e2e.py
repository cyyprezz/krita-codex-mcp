from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import suppress
from pathlib import Path

from krita_codex_mcp.bridge_client import BridgeCallError, BridgeClient
from krita_codex_protocol import BridgeConfig

DOCUMENT_NAME = "Codex Creation E2E"


def flatten_layers(layers: list[dict[str, object]]) -> list[dict[str, object]]:
    flattened: list[dict[str, object]] = []
    for layer in layers:
        flattened.append(layer)
        children = layer.get("children")
        if isinstance(children, list):
            flattened.extend(flatten_layers(children))
    return flattened


async def sample(client: BridgeClient, document_id: str, x: int, y: int) -> list[int]:
    result = await client.execute(
        "canvas", {"action": "sample_color", "document_id": document_id, "x": x, "y": y}
    )
    return result["sample"]["rgba"]


def expect_rgba(actual: list[int], expected: list[int], label: str) -> None:
    if actual != expected:
        raise RuntimeError(f"{label}: expected {expected}, got {actual}")


async def close_stale(client: BridgeClient) -> None:
    listed = await client.execute("document", {"action": "list"})
    for document in listed["documents"]:
        if document["name"] != DOCUMENT_NAME and not document["name"].startswith(
            "Codex Transform Probe"
        ):
            continue
        with suppress(Exception):
            await client.execute(
                "edit", {"action": "deselect", "document_id": document["document_id"]}
            )
        await client.execute(
            "document",
            {
                "action": "close",
                "document_id": document["document_id"],
                "discard_changes": True,
            },
        )


async def probe_transform_undo(client: BridgeClient) -> dict[str, bool]:
    operations = {
        "scale": {"width": 8, "height": 8, "x": 4, "y": 4, "strategy": "NearestNeighbor"},
        "rotate": {"angle_degrees": 90.0},
        "shear": {"shear_x_degrees": 15.0, "shear_y_degrees": 0.0},
        "crop": {"x": 0, "y": 0, "width": 6, "height": 6},
        "flip": {"direction": "horizontal"},
    }
    results: dict[str, bool] = {}
    for action, options in operations.items():
        document_id: str | None = None
        try:
            created = await client.execute(
                "document",
                {
                    "action": "create",
                    "width": 16,
                    "height": 16,
                    "name": f"Codex Transform Probe {action}",
                    "color_model": "RGBA",
                    "color_depth": "U8",
                    "profile": "",
                    "resolution": 72.0,
                },
            )
            document_id = created["affected_document_id"]
            state = await client.execute("state", {"document_id": document_id})
            layer_id = state["active_layer_id"]
            await client.execute(
                "edit",
                {
                    "action": "select_rect",
                    "document_id": document_id,
                    "x": 4,
                    "y": 4,
                    "width": 4,
                    "height": 4,
                    "combine": "replace",
                },
            )
            await client.execute(
                "edit",
                {
                    "action": "fill",
                    "document_id": document_id,
                    "layer_id": layer_id,
                    "color": "#FF0000FF",
                },
            )
            before = await client.execute("layers", {"action": "read", "document_id": document_id})
            before_layer = next(
                item for item in flatten_layers(before["layers"]) if item["id"] == layer_id
            )
            await client.execute(
                "layers",
                {
                    "action": action,
                    "document_id": document_id,
                    "layer_id": layer_id,
                    **options,
                },
            )
            after = await client.execute("layers", {"action": "read", "document_id": document_id})
            after_layer = next(
                item for item in flatten_layers(after["layers"]) if item["id"] == layer_id
            )
            await client.execute(
                "history", {"action": "undo", "steps": 1, "document_id": document_id}
            )
            undone = await client.execute("layers", {"action": "read", "document_id": document_id})
            undone_layer = next(
                item for item in flatten_layers(undone["layers"]) if item["id"] == layer_id
            )
            changed = after_layer["bounds"] != before_layer["bounds"]
            if action == "flip":
                changed = True
            results[action] = changed and undone_layer["bounds"] == before_layer["bounds"]
        finally:
            if document_id is not None:
                with suppress(Exception):
                    await client.execute("edit", {"action": "deselect", "document_id": document_id})
                with suppress(Exception):
                    await client.execute(
                        "document",
                        {"action": "close", "document_id": document_id, "discard_changes": True},
                    )
    return results


async def run(output_dir: Path) -> dict[str, object]:
    client = BridgeClient(BridgeConfig.load())
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    kra_path = output_dir / "creation-live.kra"
    await close_stale(client)
    document_id: str | None = None
    reopened_id: str | None = None
    try:
        created = await client.execute(
            "document",
            {
                "action": "create",
                "width": 64,
                "height": 64,
                "name": DOCUMENT_NAME,
                "color_model": "RGBA",
                "color_depth": "U8",
                "profile": "",
                "resolution": 72.0,
            },
        )
        document_id = created["affected_document_id"]
        state = await client.execute("state", {"document_id": document_id})
        background_id = state["active_layer_id"]
        layer = await client.execute(
            "layers",
            {
                "action": "create",
                "document_id": document_id,
                "name": "Creation Target",
                "kind": "paintlayer",
            },
        )
        layer_id = layer["affected_layer_id"]
        await client.execute(
            "layers",
            {
                "action": "set_properties",
                "document_id": document_id,
                "layer_id": background_id,
                "visible": False,
            },
        )

        project_palette = await client.execute(
            "color",
            {
                "action": "set_project_palette",
                "document_id": document_id,
                "project_name": "Hero Core",
                "entries": [
                    {"name": "Outline", "color": "#1A1A24FF"},
                    {"name": "Primary", "color": "#D43D6BFF"},
                    {"name": "Accent", "color": "#42D6C6FF"},
                ],
            },
        )
        if not project_palette["stored_in_kra"]:
            raise RuntimeError("Project palette was not reported as KRA-backed")

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
            raise RuntimeError("No Basic-5 brush preset is installed")
        preset = presets["presets"][0]

        rectangle = await client.execute(
            "draw",
            {
                "shape": "rectangle",
                "document_id": document_id,
                "layer_id": layer_id,
                "rect": {"x": 8, "y": 8, "width": 16, "height": 12},
                "stroke_style": "None",
                "fill_style": "ForegroundColor",
                "preset_name": preset,
                "brush_size": 2.0,
                "foreground": "#FF0000FF",
            },
        )
        expect_rgba(await sample(client, document_id, 12, 12), [255, 0, 0, 255], "rectangle")
        undone = await client.execute(
            "history", {"action": "undo", "steps": 1, "document_id": document_id}
        )
        if undone["completed_steps"] != 1:
            raise RuntimeError("Rectangle did not create one undo step")
        expect_rgba(await sample(client, document_id, 12, 12), [0, 0, 0, 0], "rectangle undo")
        await client.execute(
            "history", {"action": "redo", "steps": 1, "document_id": document_id}
        )
        expect_rgba(await sample(client, document_id, 12, 12), [255, 0, 0, 255], "rectangle redo")

        shapes = []
        shapes.append(
            await client.execute(
                "draw",
                {
                    "shape": "ellipse",
                    "document_id": document_id,
                    "layer_id": layer_id,
                    "rect": {"x": 32, "y": 8, "width": 16, "height": 12},
                    "stroke_style": "ForegroundColor",
                    "fill_style": "ForegroundColor",
                    "foreground": "#00FF00FF",
                },
            )
        )
        shapes.append(
            await client.execute(
                "draw",
                {
                    "shape": "polygon",
                    "document_id": document_id,
                    "layer_id": layer_id,
                    "points": [
                        {"x": 8, "y": 32},
                        {"x": 24, "y": 32},
                        {"x": 16, "y": 48},
                    ],
                    "stroke_style": "ForegroundColor",
                    "fill_style": "ForegroundColor",
                    "foreground": "#0000FFFF",
                },
            )
        )
        shapes.append(
            await client.execute(
                "draw",
                {
                    "shape": "path",
                    "document_id": document_id,
                    "layer_id": layer_id,
                    "points": [
                        {"x": 34, "y": 34},
                        {"x": 48, "y": 34},
                        {"x": 48, "y": 48},
                    ],
                    "closed": True,
                    "stroke_style": "ForegroundColor",
                    "fill_style": "BackgroundColor",
                    "foreground": "#FFFFFFFF",
                    "background": "#FFFF00FF",
                },
            )
        )
        line = await client.execute(
            "draw",
            {
                "shape": "line",
                "document_id": document_id,
                "layer_id": layer_id,
                "points": [
                    {"x": 4, "y": 58, "pressure": 0.25},
                    {"x": 58, "y": 58, "pressure": 1.0},
                ],
                "stroke_style": "ForegroundColor",
                "foreground": "#FFFFFFFF",
            },
        )
        expect_rgba(await sample(client, document_id, 40, 14), [0, 255, 0, 255], "ellipse")
        expect_rgba(await sample(client, document_id, 16, 38), [0, 0, 255, 255], "polygon")

        replacement = await client.execute(
            "color",
            {
                "action": "replace_color",
                "document_id": document_id,
                "layer_id": layer_id,
                "from_color": "#FF0000FF",
                "to_color": "#FF00FFFF",
                "tolerance": 0,
            },
        )
        if replacement["replaced_pixels"] <= 0 or replacement["undo_steps"] != 1:
            raise RuntimeError(f"Unexpected replacement result: {replacement}")
        expect_rgba(await sample(client, document_id, 12, 12), [255, 0, 255, 255], "replace")
        replace_history = await client.execute(
            "history", {"action": "status", "document_id": document_id}
        )
        replace_undo = await client.execute(
            "history", {"action": "undo", "steps": 1, "document_id": document_id}
        )
        replace_undo_sample = await sample(client, document_id, 12, 12)
        if replace_undo_sample != [255, 0, 0, 255]:
            raise RuntimeError(
                "replace undo failed: "
                f"status={replace_history}, undo={replace_undo}, sample={replace_undo_sample}"
            )
        await client.execute(
            "history", {"action": "redo", "steps": 1, "document_id": document_id}
        )

        adjusted = await client.execute(
            "color",
            {
                "action": "adjust",
                "document_id": document_id,
                "layer_id": layer_id,
                "filter_name": "invert",
                "settings": {},
                "rect": {"x": 8, "y": 8, "width": 16, "height": 12},
            },
        )
        if adjusted["undo_steps"] != 1:
            raise RuntimeError("Invert filter did not report one undo step")
        expect_rgba(await sample(client, document_id, 12, 12), [0, 255, 0, 255], "invert")
        await client.execute(
            "history", {"action": "undo", "steps": 1, "document_id": document_id}
        )
        expect_rgba(await sample(client, document_id, 12, 12), [255, 0, 255, 255], "invert undo")

        await client.execute(
            "edit",
            {
                "action": "select_rect",
                "document_id": document_id,
                "x": 8,
                "y": 8,
                "width": 16,
                "height": 12,
                "combine": "replace",
            },
        )
        copied = await client.execute(
            "edit",
            {"action": "copy", "document_id": document_id, "layer_id": layer_id},
        )
        pasted = await client.execute(
            "edit",
            {"action": "paste", "document_id": document_id, "layer_id": layer_id},
        )
        paste_layer_id = pasted["created_layer_id"]
        await client.execute(
            "edit",
            {
                "action": "select_rect",
                "document_id": document_id,
                "x": 8,
                "y": 8,
                "width": 16,
                "height": 12,
                "combine": "replace",
            },
        )
        copied_layer = await client.execute(
            "edit",
            {"action": "copy_to_layer", "document_id": document_id, "layer_id": layer_id},
        )
        copy_layer_id = copied_layer["created_layer_id"]
        await client.execute(
            "history", {"action": "undo", "steps": 1, "document_id": document_id}
        )
        await client.execute(
            "layers",
            {
                "action": "set_properties",
                "document_id": document_id,
                "layer_id": paste_layer_id,
                "visible": False,
            },
        )
        await client.execute(
            "edit",
            {
                "action": "select_rect",
                "document_id": document_id,
                "x": 8,
                "y": 8,
                "width": 16,
                "height": 12,
                "combine": "replace",
            },
        )
        cut = await client.execute(
            "edit",
            {"action": "cut", "document_id": document_id, "layer_id": layer_id},
        )
        expect_rgba(await sample(client, document_id, 12, 12), [0, 0, 0, 0], "cut")
        await client.execute(
            "history", {"action": "undo", "steps": 1, "document_id": document_id}
        )
        expect_rgba(await sample(client, document_id, 12, 12), [255, 0, 255, 255], "cut undo")

        await client.execute("edit", {"action": "deselect", "document_id": document_id})
        transform_source = await client.execute(
            "layers",
            {
                "action": "duplicate",
                "document_id": document_id,
                "layer_id": layer_id,
                "name": "Transform Target",
            },
        )
        transform_id = transform_source["affected_layer_id"]
        scaled = await client.execute(
            "layers",
            {
                "action": "scale",
                "document_id": document_id,
                "layer_id": transform_id,
                "width": 48,
                "height": 48,
                "x": 0,
                "y": 0,
                "strategy": "NearestNeighbor",
            },
        )
        try:
            await client.execute(
                "layers",
                {
                    "action": "rotate",
                    "document_id": document_id,
                    "layer_id": transform_id,
                    "angle_degrees": 15.0,
                },
            )
        except BridgeCallError as exc:
            if exc.code != "UNSAFE_TRANSFORM_SEQUENCE":
                raise
            blocked_transform_sequence = exc.code
        else:
            raise RuntimeError("Second destructive transform was not blocked safely")
        flipped = await client.execute(
            "layers",
            {
                "action": "flip",
                "document_id": document_id,
                "layer_id": transform_id,
                "direction": "horizontal",
            },
        )
        direct_transforms = [scaled]
        if any(not item.get("backup_layer_id") for item in direct_transforms):
            raise RuntimeError("A non-undoable native transform did not create a backup layer")
        if flipped.get("undo_steps") != 1:
            raise RuntimeError("Native layer flip did not report one undo step")

        await client.execute(
            "save_export",
            {"document_id": document_id, "kra_path": str(kra_path), "overwrite": True},
        )
        await client.execute(
            "document",
            {"action": "close", "document_id": document_id, "discard_changes": False},
        )
        document_id = None
        reopened = await client.execute("document", {"action": "open", "path": str(kra_path)})
        reopened_id = reopened["affected_document_id"]
        color_state = await client.execute("color", {"action": "read", "document_id": reopened_id})
        reopened_palette = color_state["project_palette"]
        if reopened_palette is None or reopened_palette["name"] != "Hero Core":
            raise RuntimeError("Project palette did not survive the KRA round trip")
        reopened_state = await client.execute("state", {"document_id": reopened_id})
        reopened_ids = {item["id"] for item in flatten_layers(reopened_state["layers"])}
        if transform_id not in reopened_ids or paste_layer_id not in reopened_ids:
            raise RuntimeError("Creation layers did not survive the KRA round trip")
        reopened_transform = await client.execute(
            "layers",
            {
                "action": "rotate",
                "document_id": reopened_id,
                "layer_id": transform_id,
                "angle_degrees": 5.0,
            },
        )
        if not reopened_transform.get("backup_layer_id"):
            raise RuntimeError("Transform guard did not reset after close and reopen")
        transform_undo = await probe_transform_undo(client)

        return {
            "preset": preset,
            "rectangle_undo_redo": rectangle["undo_steps"] == 1,
            "native_shapes": [item["shape"] for item in shapes] + [line["shape"]],
            "replace_pixels": replacement["replaced_pixels"],
            "replace_undo": True,
            "filter": adjusted["filter_name"],
            "filter_undo": True,
            "clipboard_copy_action": copied["native_action"],
            "clipboard_paste_layer_id": paste_layer_id,
            "copy_to_layer_id": copy_layer_id,
            "cut_undo": cut["undo_steps"] == 1,
            "transforms": [
                scaled["action"],
                flipped["action"],
            ],
            "blocked_transform_sequence": blocked_transform_sequence,
            "reopened_transform": reopened_transform["action"],
            "transform_undo": transform_undo,
            "transform_backups": [item["backup_layer_id"] for item in direct_transforms],
            "project_palette_round_trip": reopened_palette,
            "kra_path": str(kra_path),
        }
    finally:
        for target_id in (document_id, reopened_id):
            if target_id is None:
                continue
            with suppress(Exception):
                await client.execute("edit", {"action": "deselect", "document_id": target_id})
            with suppress(Exception):
                await client.execute(
                    "document",
                    {"action": "close", "document_id": target_id, "discard_changes": True},
                )


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Exercise drawing, colors, clipboard, transforms")
    parser.add_argument("--output-dir", type=Path, default=repo_root / "e2e-output")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output_dir)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
