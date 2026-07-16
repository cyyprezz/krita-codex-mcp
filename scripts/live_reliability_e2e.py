from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
from contextlib import suppress
from pathlib import Path

from krita_codex_mcp.bridge_client import BridgeClient
from krita_codex_mcp.service import KritaService
from krita_codex_protocol import BridgeConfig

DOCUMENT_NAME = "Codex Reliability E2E"
LAYER_NAME = "Fresh Empty Paint"
GROUP_NAME = "Fresh Empty Group"


def flatten_layers(layers: list[dict[str, object]]) -> list[dict[str, object]]:
    flattened: list[dict[str, object]] = []
    for layer in layers:
        flattened.append(layer)
        children = layer.get("children")
        if isinstance(children, list):
            flattened.extend(flatten_layers(children))
    return flattened


def find_named(layers: list[dict[str, object]], name: str) -> dict[str, object]:
    matches = [layer for layer in flatten_layers(layers) if layer.get("name") == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one layer named {name!r}, found {len(matches)}")
    return matches[0]


def require_compact(result: dict[str, object], action: str) -> None:
    if result.get("response_mode") != "compact" or "layers" in result:
        raise RuntimeError(f"{action} returned an unexpected full layer tree")
    if not isinstance(result.get("affected_layer"), dict):
        raise RuntimeError(f"{action} did not return an affected-layer summary")


async def preview_hash(client: BridgeClient, document_id: str) -> str:
    result = await client.execute(
        "preview",
        {"document_id": document_id, "max_width": 64, "max_height": 64},
    )
    payload = base64.b64decode(result["png_base64"], validate=True)
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Preview response is not a PNG")
    return hashlib.sha256(payload).hexdigest()


async def close_stale(client: BridgeClient) -> None:
    listed = await client.execute("document", {"action": "list"})
    for document in listed["documents"]:
        if document["name"] != DOCUMENT_NAME:
            continue
        await client.execute(
            "document",
            {
                "action": "close",
                "document_id": document["document_id"],
                "discard_changes": True,
            },
        )


async def run(output_dir: Path) -> dict[str, object]:
    config = BridgeConfig.load()
    client = BridgeClient(config)
    service = KritaService(config, client)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    kra_path = output_dir / "reliability-live.kra"
    await close_stale(client)
    document_id: str | None = None
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

        paint = await client.execute(
            "layers",
            {
                "action": "create",
                "document_id": document_id,
                "name": LAYER_NAME,
                "kind": "paintlayer",
            },
        )
        require_compact(paint, "create paint layer")
        layer_id = paint["affected_layer_id"]
        drawn = await client.execute(
            "draw",
            {
                "shape": "rectangle",
                "document_id": document_id,
                "layer_id": layer_id,
                "rect": {"x": 8, "y": 10, "width": 16, "height": 12},
                "stroke_style": "None",
                "fill_style": "ForegroundColor",
                "foreground": "#E43D30FF",
            },
        )
        if not drawn.get("verified_content_change"):
            raise RuntimeError("Drawing on the fresh paint layer was not verified")

        tree = await client.execute(
            "layers", {"action": "read", "document_id": document_id}
        )
        painted_layer = find_named(tree["layers"], LAYER_NAME)
        bounds = painted_layer["bounds"]
        if bounds["width"] <= 0 or bounds["height"] <= 0:
            raise RuntimeError("Fresh paint layer still has empty bounds after drawing")

        group = await client.execute(
            "layers",
            {
                "action": "create",
                "document_id": document_id,
                "name": GROUP_NAME,
                "kind": "grouplayer",
            },
        )
        require_compact(group, "create group")
        group_id = group["affected_layer_id"]
        moved = await client.execute(
            "layers",
            {
                "action": "reorder",
                "document_id": document_id,
                "layer_id": layer_id,
                "parent_id": group_id,
            },
        )
        require_compact(moved, "reparent")
        if moved["affected_layer"].get("parent_id") != group_id:
            raise RuntimeError("Compact reparent response reports the wrong destination")

        visible_hash = await preview_hash(client, document_id)
        hidden = await client.execute(
            "layers",
            {
                "action": "set_properties",
                "document_id": document_id,
                "layer_id": group_id,
                "visible": False,
            },
        )
        require_compact(hidden, "hide group")
        hidden_hash = await preview_hash(client, document_id)
        shown = await client.execute(
            "layers",
            {
                "action": "set_properties",
                "document_id": document_id,
                "layer_id": group_id,
                "visible": True,
            },
        )
        require_compact(shown, "show group")
        restored_hash = await preview_hash(client, document_id)
        if visible_hash == hidden_hash or restored_hash != visible_hash:
            raise RuntimeError("Preview did not track immediate visibility changes reliably")

        await client.execute(
            "save_export",
            {"document_id": document_id, "kra_path": str(kra_path), "overwrite": True},
        )
        await client.execute(
            "document",
            {"action": "close", "document_id": document_id, "discard_changes": False},
        )
        await asyncio.sleep(1.0)
        document_id = None
        reopened = await client.execute("document", {"action": "open", "path": str(kra_path)})
        await asyncio.sleep(1.0)
        document_id = reopened["affected_document_id"]
        reopened_tree = await client.execute(
            "layers", {"action": "read", "document_id": document_id}
        )
        reopened_group = find_named(reopened_tree["layers"], GROUP_NAME)
        reopened_layer = find_named(reopened_tree["layers"], LAYER_NAME)
        if reopened_group["id"] != group_id or reopened_layer["id"] != layer_id:
            raise RuntimeError("Layer UUIDs did not survive the KRA round trip")
        if reopened_layer["parent_id"] != reopened_group["id"]:
            raise RuntimeError("Reparented layer did not survive inside the fresh group")

        scaled = await client.execute(
            "layers",
            {
                "action": "scale",
                "document_id": document_id,
                "layer_id": layer_id,
                "width": 24,
                "height": 18,
                "x": 8,
                "y": 10,
                "strategy": "NearestNeighbor",
                "transform_mode": "direct",
            },
        )
        require_compact(scaled, "direct scale")
        if not scaled.get("backup_layer_id"):
            raise RuntimeError("First direct transform did not create a safety backup")
        first_document_id = document_id
        document_id = scaled["document_id"]
        layer_id = scaled["affected_layer_id"]
        if document_id != first_document_id:
            raise RuntimeError("Direct scale unexpectedly replaced the document identity")

        rotated = await service.layers(
            action="rotate",
            document_id=document_id,
            layer_id=layer_id,
            angle_degrees=5.0,
            transform_mode="direct",
            checkpoint_reopen=True,
        )
        require_compact(rotated, "checkpoint rotate")
        if not rotated.get("backup_layer_id") or not rotated.get(
            "checkpoint_reopen", {}
        ).get("document_reopened"):
            raise RuntimeError("Second direct transform did not checkpoint and reopen safely")
        second_document_id = document_id
        document_id = rotated["document_id"]
        if document_id == second_document_id:
            raise RuntimeError("Checkpoint rotate did not replace the document identity")
        transformed_hash = await preview_hash(client, document_id)

        full = await client.execute(
            "layers",
            {
                "action": "activate",
                "document_id": document_id,
                "layer_id": rotated["affected_layer_id"],
                "response_mode": "full",
            },
        )
        if full.get("response_mode") != "full" or not isinstance(full.get("layers"), list):
            raise RuntimeError("Explicit full layer response did not include the tree")

        return {
            "draw_on_empty_layer": True,
            "draw_bounds": bounds,
            "reparent_persisted": True,
            "stable_layer_uuid": reopened_layer["id"],
            "preview_visibility_round_trip": True,
            "compact_mutations": True,
            "explicit_full_response": True,
            "sequential_direct_transforms": [scaled["action"], rotated["action"]],
            "final_document_id": document_id,
            "final_preview_sha256": transformed_hash,
            "kra_path": str(kra_path),
        }
    finally:
        if document_id is not None:
            with suppress(Exception):
                await client.execute(
                    "document",
                    {
                        "action": "close",
                        "document_id": document_id,
                        "discard_changes": True,
                    },
                )


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Exercise Krita reliability fixes in a disposable KRA"
    )
    parser.add_argument("--output-dir", type=Path, default=repo_root / "e2e-output")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output_dir)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
