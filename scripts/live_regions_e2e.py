from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import shutil
import struct
import zlib
from contextlib import suppress
from pathlib import Path

from krita_codex_mcp.bridge_client import BridgeCallError, BridgeClient
from krita_codex_protocol import BridgeConfig

DOCUMENT_NAME = "Codex Regions E2E"


def _flatten(layers: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for layer in layers:
        result.append(layer)
        children = layer.get("children")
        if isinstance(children, list):
            result.extend(_flatten(children))
    return result


def _paeth(a: int, b: int, c: int) -> int:
    prediction = a + b - c
    distance_a = abs(prediction - a)
    distance_b = abs(prediction - b)
    distance_c = abs(prediction - c)
    if distance_a <= distance_b and distance_a <= distance_c:
        return a
    return b if distance_b <= distance_c else c


def _decode_rgba_png(payload: bytes) -> tuple[int, int, bytes]:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Expected a PNG payload")
    offset = 8
    width = height = 0
    compressed = bytearray()
    while offset < len(payload):
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        data = payload[offset + 8 : offset + 8 + length]
        offset += length + 12
        if kind == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", data[:10])
            if bit_depth != 8 or color_type != 6:
                raise RuntimeError(
                    f"Expected an 8-bit RGBA PNG, got depth={bit_depth}, type={color_type}"
                )
        elif kind == b"IDAT":
            compressed.extend(data)
        elif kind == b"IEND":
            break
    raw = zlib.decompress(bytes(compressed))
    stride = width * 4
    result = bytearray(height * stride)
    source_offset = 0
    for y in range(height):
        filter_type = raw[source_offset]
        source_offset += 1
        scanline = raw[source_offset : source_offset + stride]
        source_offset += stride
        row_offset = y * stride
        for index, encoded in enumerate(scanline):
            left = result[row_offset + index - 4] if index >= 4 else 0
            above = result[row_offset - stride + index] if y > 0 else 0
            upper_left = result[row_offset - stride + index - 4] if y > 0 and index >= 4 else 0
            if filter_type == 0:
                value = encoded
            elif filter_type == 1:
                value = encoded + left
            elif filter_type == 2:
                value = encoded + above
            elif filter_type == 3:
                value = encoded + ((left + above) // 2)
            elif filter_type == 4:
                value = encoded + _paeth(left, above, upper_left)
            else:
                raise RuntimeError(f"Unsupported PNG filter {filter_type}")
            result[row_offset + index] = value & 0xFF
    return width, height, bytes(result)


def _pixel(payload: bytes, x: int, y: int) -> tuple[int, int, int, int]:
    width, height, rgba = _decode_rgba_png(payload)
    if not 0 <= x < width or not 0 <= y < height:
        raise RuntimeError("PNG sample coordinate is outside the image")
    offset = (y * width + x) * 4
    return tuple(rgba[offset : offset + 4])  # type: ignore[return-value]


async def _close_stale(client: BridgeClient) -> None:
    listed = await client.execute("document", {"action": "list"})
    for document in listed["documents"]:
        if document["name"] != DOCUMENT_NAME:
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


async def _fill_rect(
    client: BridgeClient,
    document_id: str,
    layer_id: str,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    color: str,
) -> None:
    await client.execute(
        "edit",
        {
            "action": "select_rect",
            "document_id": document_id,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "combine": "replace",
        },
    )
    await client.execute(
        "edit",
        {
            "action": "fill",
            "document_id": document_id,
            "layer_id": layer_id,
            "color": color,
        },
    )


async def _projection_hash(client: BridgeClient, document_id: str) -> str:
    preview = await client.execute(
        "preview", {"document_id": document_id, "max_width": 64, "max_height": 64}
    )
    return hashlib.sha256(base64.b64decode(preview["png_base64"], validate=True)).hexdigest()


async def run(output_dir: Path) -> dict[str, object]:
    client = BridgeClient(BridgeConfig.load())
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    tileset_dir = output_dir / "regions-live-tileset"
    regions_dir = output_dir / "regions-live-explicit"
    for path in (tileset_dir, regions_dir):
        if path.exists():
            shutil.rmtree(path)
    await _close_stale(client)
    document_id: str | None = None
    try:
        created = await client.execute(
            "document",
            {
                "action": "create",
                "width": 16,
                "height": 8,
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
        tiles = await client.execute(
            "layers",
            {
                "action": "create",
                "document_id": document_id,
                "name": "Tiles",
                "kind": "paintlayer",
            },
        )
        tile_layer_id = tiles["affected_layer_id"]
        await client.execute(
            "layers",
            {
                "action": "set_properties",
                "document_id": document_id,
                "layer_id": background_id,
                "visible": False,
            },
        )
        await _fill_rect(
            client,
            document_id,
            tile_layer_id,
            x=0,
            y=0,
            width=8,
            height=8,
            color="#E53935FF",
        )
        await _fill_rect(
            client,
            document_id,
            tile_layer_id,
            x=8,
            y=0,
            width=8,
            height=8,
            color="#1E88E5FF",
        )
        await client.execute("edit", {"action": "deselect", "document_id": document_id})

        properties = await client.execute(
            "layers",
            {
                "action": "set_properties",
                "document_id": document_id,
                "layer_id": tile_layer_id,
                "locked": True,
                "alpha_locked": True,
                "inherit_alpha": True,
                "blending_mode": "multiply",
            },
        )
        serialized = next(
            layer for layer in _flatten(properties["layers"]) if layer["id"] == tile_layer_id
        )
        expected_properties = {
            "locked": True,
            "alpha_locked": True,
            "inherit_alpha": True,
            "blending_mode": "multiply",
        }
        for field, expected in expected_properties.items():
            if serialized[field] != expected:
                raise RuntimeError(f"Layer property {field} was not persisted: {serialized}")
        await client.execute(
            "layers",
            {
                "action": "set_properties",
                "document_id": document_id,
                "layer_id": tile_layer_id,
                "locked": False,
                "alpha_locked": False,
                "inherit_alpha": False,
                "blending_mode": "normal",
            },
        )
        group = await client.execute(
            "layers",
            {
                "action": "create",
                "document_id": document_id,
                "name": "Property Rejection Group",
                "kind": "grouplayer",
            },
        )
        group_rejected = False
        try:
            await client.execute(
                "layers",
                {
                    "action": "set_properties",
                    "document_id": document_id,
                    "layer_id": group["affected_layer_id"],
                    "alpha_locked": True,
                },
            )
        except BridgeCallError as exc:
            if exc.code != "INVALID_LAYER_TYPE":
                raise
            group_rejected = True
        if not group_rejected:
            raise RuntimeError("alpha_locked unexpectedly succeeded on a group layer")
        unknown_mode_rejected = False
        try:
            await client.execute(
                "layers",
                {
                    "action": "set_properties",
                    "document_id": document_id,
                    "layer_id": tile_layer_id,
                    "blending_mode": "unknown_mode",
                },
            )
        except BridgeCallError as exc:
            if exc.code != "INVALID_ARGUMENT":
                raise
            unknown_mode_rejected = True
        if not unknown_mode_rejected:
            raise RuntimeError("Unknown blending mode unexpectedly succeeded")
        await client.execute(
            "layers",
            {
                "action": "delete",
                "document_id": document_id,
                "layer_id": group["affected_layer_id"],
            },
        )

        tileset_arguments = {
            "action": "export_tileset",
            "document_id": document_id,
            "layer_id": tile_layer_id,
            "output_path": str(tileset_dir),
            "overwrite": False,
            "tile_width": 8,
            "tile_height": 8,
            "grid_columns": 2,
            "grid_rows": 1,
            "name_prefix": "test",
            "package_name": "Test Tiles",
            "pixels_per_unit": 8,
            "preview_max_width": 128,
            "preview_max_height": 64,
        }
        tileset = await client.execute("asset", tileset_arguments)
        preview_payload = base64.b64decode(tileset["png_base64"], validate=True)
        preview_width, preview_height, _ = _decode_rgba_png(preview_payload)
        if (preview_width, preview_height) != (16, 8):
            raise RuntimeError("Tileset direct preview has unexpected dimensions")
        manifest_path = tileset_dir / "manifest.json"
        sheet_path = tileset_dir / "spritesheet.png"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["schema"] != "dev.desens.krita-codex.unity-regions":
            raise RuntimeError("Tileset manifest schema is incorrect")
        if manifest["layout"]["kind"] != "grid" or manifest["region_count"] != 2:
            raise RuntimeError("Tileset grid metadata is incomplete")
        if hashlib.sha256(sheet_path.read_bytes()).hexdigest() != manifest["sheet"]["sha256"]:
            raise RuntimeError("Tileset sheet checksum does not match its manifest")
        left_path = tileset_dir / manifest["regions"][0]["file"]
        right_path = tileset_dir / manifest["regions"][1]["file"]
        if _pixel(left_path.read_bytes(), 4, 4) != (229, 57, 53, 255):
            raise RuntimeError("Left exported tile does not contain the expected red pixels")
        if _pixel(right_path.read_bytes(), 4, 4) != (30, 136, 229, 255):
            raise RuntimeError("Right exported tile does not contain the expected blue pixels")
        for region in manifest["regions"]:
            top = region["sheet_rect_png_top_left"]
            unity = region["sprite_rect_unity_bottom_left"]
            if unity["y"] != manifest["sheet"]["height"] - top["y"] - top["height"]:
                raise RuntimeError("Tileset Unity rectangle Y conversion is incorrect")

        sheet_meta = tileset_dir / "spritesheet.png.meta"
        region_meta = tileset_dir / "regions" / "0000.png.meta"
        sheet_meta.write_text("stable-sheet-guid", encoding="utf-8")
        region_meta.write_text("stable-region-guid", encoding="utf-8")
        auto_grid_arguments = {
            key: value
            for key, value in tileset_arguments.items()
            if key not in {"grid_columns", "grid_rows"}
        }
        overwritten = await client.execute(
            "asset", {**auto_grid_arguments, "overwrite": True}
        )
        if sheet_meta.read_text(encoding="utf-8") != "stable-sheet-guid":
            raise RuntimeError("Tileset sheet .meta sidecar was not preserved")
        if region_meta.read_text(encoding="utf-8") != "stable-region-guid":
            raise RuntimeError("Region .meta sidecar was not preserved")
        non_overwrite_refused = False
        try:
            await client.execute("asset", tileset_arguments)
        except BridgeCallError as exc:
            if exc.code != "PATH_NOT_ALLOWED":
                raise
            non_overwrite_refused = True
        if not non_overwrite_refused:
            raise RuntimeError("Existing tileset was replaced without overwrite=true")

        explicit = await client.execute(
            "asset",
            {
                "action": "export_regions",
                "document_id": document_id,
                "layer_id": tile_layer_id,
                "output_path": str(regions_dir),
                "overwrite": False,
                "regions": [
                    {"name": "blue_tile", "x": 8, "y": 0, "width": 8, "height": 8},
                    {"name": "red_tile", "x": 0, "y": 0, "width": 8, "height": 8},
                ],
                "pixels_per_unit": 8,
            },
        )
        explicit_manifest = json.loads(
            (regions_dir / "manifest.json").read_text(encoding="utf-8")
        )
        if [item["name"] for item in explicit_manifest["regions"]] != [
            "blue_tile",
            "red_tile",
        ]:
            raise RuntimeError("Explicit region request order was not preserved")

        bottom = await client.execute(
            "layers",
            {
                "action": "create",
                "document_id": document_id,
                "name": "Merge Bottom",
                "kind": "paintlayer",
            },
        )
        bottom_id = bottom["affected_layer_id"]
        await _fill_rect(
            client,
            document_id,
            bottom_id,
            x=1,
            y=1,
            width=3,
            height=3,
            color="#43A047FF",
        )
        top = await client.execute(
            "layers",
            {
                "action": "create",
                "document_id": document_id,
                "name": "Merge Top",
                "kind": "paintlayer",
            },
        )
        top_id = top["affected_layer_id"]
        await _fill_rect(
            client,
            document_id,
            top_id,
            x=4,
            y=1,
            width=3,
            height=3,
            color="#8E24AAFF",
        )
        await client.execute("edit", {"action": "deselect", "document_id": document_id})
        await client.execute(
            "layers",
            {
                "action": "set_properties",
                "document_id": document_id,
                "layer_id": bottom_id,
                "locked": True,
            },
        )
        locked_merge_rejected = False
        try:
            await client.execute(
                "layers",
                {"action": "merge_down", "document_id": document_id, "layer_id": top_id},
            )
        except BridgeCallError as exc:
            if exc.code != "LAYER_LOCKED":
                raise
            locked_merge_rejected = True
        if not locked_merge_rejected:
            raise RuntimeError("merge_down unexpectedly merged a locked adjacent layer")
        await client.execute(
            "layers",
            {
                "action": "set_properties",
                "document_id": document_id,
                "layer_id": bottom_id,
                "locked": False,
            },
        )
        before_merge = await _projection_hash(client, document_id)
        before_layers = await client.execute(
            "layers", {"action": "read", "document_id": document_id}
        )
        count_before = len(_flatten(before_layers["layers"]))
        merged = await client.execute(
            "layers",
            {"action": "merge_down", "document_id": document_id, "layer_id": top_id},
        )
        if merged["source_layer_id"] != top_id or merged["merged_with_layer_id"] != bottom_id:
            raise RuntimeError("merge_down did not report both consumed layer UUIDs")
        if not merged["metadata_dropped"]:
            raise RuntimeError("merge_down did not disclose dropped layer metadata")
        count_after = len(_flatten(merged["layers"]))
        if count_after != count_before - 1:
            raise RuntimeError(
                "merge_down did not reduce the layer count by exactly one: "
                f"before={count_before}, after={count_after}"
            )
        if await _projection_hash(client, document_id) != before_merge:
            raise RuntimeError("merge_down changed the composited document projection")
        undo = await client.execute(
            "history", {"action": "undo", "document_id": document_id, "steps": 1}
        )
        if undo["completed_steps"] != 1:
            raise RuntimeError("Undo did not restore the pre-merge layer state")
        restored = await client.execute("layers", {"action": "read", "document_id": document_id})
        restored_ids = {layer["id"] for layer in _flatten(restored["layers"])}
        if not {top_id, bottom_id}.issubset(restored_ids):
            raise RuntimeError("Undo did not restore both original layer UUIDs")
        if await _projection_hash(client, document_id) != before_merge:
            raise RuntimeError("Undo after merge_down changed the document projection")

        return {
            "document_id": document_id,
            "layer_properties": expected_properties,
            "group_alpha_lock_rejected": group_rejected,
            "unknown_blending_mode_rejected": unknown_mode_rejected,
            "tileset_package": tileset["output"],
            "tileset_regions": manifest["region_count"],
            "tileset_meta_preserved": True,
            "tileset_non_overwrite_refused": non_overwrite_refused,
            "tileset_overwrite_bytes": overwritten["output"]["bytes"],
            "explicit_region_package": explicit["output"],
            "explicit_region_names": [item["name"] for item in explicit_manifest["regions"]],
            "merge_down_undo": True,
            "locked_merge_rejected": locked_merge_rejected,
        }
    finally:
        if document_id is not None:
            with suppress(Exception):
                await client.execute(
                    "edit", {"action": "deselect", "document_id": document_id}
                )
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
    parser = argparse.ArgumentParser(description="Exercise layer properties and region packages")
    parser.add_argument("--output-dir", type=Path, default=repo_root / "e2e-output")
    arguments = parser.parse_args()
    print(json.dumps(asyncio.run(run(arguments.output_dir)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
