from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from krita_codex_mcp.bridge_client import BridgeCallError, BridgeClient
from krita_codex_protocol import BridgeConfig


async def cleanup_stale_test_document() -> dict[str, object]:
    client = BridgeClient(BridgeConfig.load())
    listed = await client.execute("document", {"action": "list"})
    closed: list[str] = []
    for document in listed["documents"]:
        if document["name"] == "Codex Expansion E2E":
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
    kra_path = output_dir / "expansion-live.kra"
    png_path = output_dir / "expansion-live.png"

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
    brush_before = await client.execute("brush", {"action": "read"})
    document_id = created["affected_document_id"]
    listed = await client.execute("document", {"action": "list"})
    if document_id not in {item["document_id"] for item in listed["documents"]}:
        raise RuntimeError("Created document ID was not stable in the document list")

    presets = await client.execute(
        "brush", {"action": "list_presets", "query": "Basic-5", "limit": 20}
    )
    if not presets["presets"]:
        raise RuntimeError("No Basic-5 preset was found")
    preset = presets["presets"][0]
    brush_set = await client.execute(
        "brush",
        {
            "action": "set",
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
        "layers", {"action": "create", "kind": "paintlayer", "name": "Expansion Paint"}
    )
    layer_id = layer["affected_layer_id"]
    painted = await client.execute(
        "paint_path",
        {
            "layer_id": layer_id,
            "preset_name": preset,
            "brush_size": 18.0,
            "points": [
                {"x": 40, "y": 180, "pressure": 0.25},
                {"x": 160, "y": 60, "pressure": 1.0},
                {"x": 280, "y": 180, "pressure": 0.5},
            ],
        },
    )
    undo = await client.execute("history", {"action": "undo", "steps": 2})
    redo = await client.execute("history", {"action": "redo", "steps": 2})
    if undo["completed_steps"] != 2 or redo["completed_steps"] != 2:
        raise RuntimeError(f"Unexpected undo/redo result: undo={undo}, redo={redo}")

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
        },
    )
    await client.execute(
        "document",
        {"action": "close", "document_id": document_id, "discard_changes": False},
    )
    opened = await client.execute("document", {"action": "open", "path": str(kra_path)})
    opened_id = opened["affected_document_id"]
    await client.execute(
        "document", {"action": "close", "document_id": opened_id, "discard_changes": False}
    )

    return {
        "documents_before": len(before["documents"]),
        "created_document_id": document_id,
        "preset_matches": presets["total"],
        "brush_after_set": brush_set["painting"],
        "painted_segments": painted["segments"],
        "undo_steps": undo["completed_steps"],
        "redo_steps": redo["completed_steps"],
        "unsaved_close_refused": unsaved_refused,
        "outputs": saved["outputs"],
        "reopened_document_id": opened_id,
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
