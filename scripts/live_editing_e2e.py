from __future__ import annotations

import argparse
import asyncio
import base64
import json
import struct
import zlib
from contextlib import suppress
from pathlib import Path

from krita_codex_mcp.bridge_client import BridgeClient
from krita_codex_protocol import BridgeConfig

DOCUMENT_NAME = "Codex Editing E2E"


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def _rgba_png(width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    rows = []
    for y in range(height):
        row = b"".join(bytes(pixels[y * width + x]) for x in range(width))
        rows.append(b"\x00" + row)
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            _png_chunk(b"IDAT", zlib.compress(b"".join(rows))),
            _png_chunk(b"IEND", b""),
        )
    )


def _png_dimensions(payload: bytes) -> tuple[int, int]:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Preview payload is not a PNG")
    return struct.unpack(">II", payload[16:24])


def _assert_rgba(sample: dict[str, object], expected: list[int], label: str) -> None:
    actual = sample["sample"]["rgba"]
    if actual != expected:
        raise RuntimeError(f"{label}: expected RGBA {expected}, got {actual}")


def _assert_alpha(sample: dict[str, object], expected: int, label: str) -> None:
    actual = sample["sample"]["rgba"][3]
    if actual != expected:
        raise RuntimeError(f"{label}: expected alpha {expected}, got {actual}")


async def _close_stale(client: BridgeClient) -> None:
    listed = await client.execute("document", {"action": "list"})
    for document in listed["documents"]:
        if document["name"] == DOCUMENT_NAME:
            with suppress(Exception):
                await client.execute(
                    "edit",
                    {"action": "deselect", "document_id": document["document_id"]},
                )
            await client.execute(
                "document",
                {
                    "action": "close",
                    "document_id": document["document_id"],
                    "discard_changes": True,
                },
            )


async def run(output_dir: Path) -> dict[str, object]:
    client = BridgeClient(BridgeConfig.load())
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = output_dir / "editing-import-source.png"
    kra_path = output_dir / "editing-live.kra"
    source_path.write_bytes(
        _rgba_png(
            2,
            2,
            [
                (255, 0, 0, 255),
                (0, 255, 0, 255),
                (0, 0, 255, 255),
                (0, 0, 0, 0),
            ],
        )
    )
    await _close_stale(client)
    document_id: str | None = None
    reopened_id: str | None = None
    try:
        created = await client.execute(
            "document",
            {
                "action": "create",
                "width": 16,
                "height": 12,
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
        created_layer = await client.execute(
            "layers",
            {
                "action": "create",
                "document_id": document_id,
                "name": "Editing Target",
                "kind": "paintlayer",
            },
        )
        layer_id = created_layer["affected_layer_id"]
        await client.execute(
            "layers",
            {
                "action": "set_properties",
                "document_id": document_id,
                "layer_id": background_id,
                "visible": False,
            },
        )

        selected = await client.execute(
            "edit",
            {
                "action": "select_rect",
                "document_id": document_id,
                "x": 2,
                "y": 3,
                "width": 4,
                "height": 4,
                "combine": "replace",
            },
        )
        if selected["selection_bounds"] != {"x": 2, "y": 3, "width": 4, "height": 4}:
            raise RuntimeError(f"Unexpected selection bounds: {selected['selection_bounds']}")
        await client.execute(
            "edit",
            {
                "action": "fill",
                "document_id": document_id,
                "layer_id": layer_id,
                "color": "#CC3366FF",
            },
        )
        inside = await client.execute(
            "canvas", {"action": "sample_color", "document_id": document_id, "x": 3, "y": 4}
        )
        outside = await client.execute(
            "canvas", {"action": "sample_color", "document_id": document_id, "x": 0, "y": 0}
        )
        _assert_rgba(inside, [204, 51, 102, 255], "selection fill")
        _assert_rgba(outside, [0, 0, 0, 0], "selection isolation")

        preview = await client.execute(
            "preview",
            {
                "document_id": document_id,
                "max_width": 64,
                "max_height": 64,
                "region": {"x": 0, "y": 0, "width": 8, "height": 8},
                "scale": 4,
            },
        )
        preview_bytes = base64.b64decode(preview["png_base64"], validate=True)
        if _png_dimensions(preview_bytes) != (32, 32):
            raise RuntimeError("Cropped preview was not nearest-neighbor upscaled to 32x32")

        await client.execute(
            "edit",
            {
                "action": "clear",
                "document_id": document_id,
                "layer_id": layer_id,
            },
        )
        cleared = await client.execute(
            "canvas", {"action": "sample_color", "document_id": document_id, "x": 3, "y": 4}
        )
        _assert_alpha(cleared, 0, "selection clear")
        undone = await client.execute(
            "history", {"action": "undo", "document_id": document_id, "steps": 1}
        )
        if undone["completed_steps"] != 1:
            raise RuntimeError("Clear did not produce one understandable Krita undo step")
        restored = await client.execute(
            "canvas", {"action": "sample_color", "document_id": document_id, "x": 3, "y": 4}
        )
        _assert_rgba(restored, [204, 51, 102, 255], "clear undo")

        await client.execute("edit", {"action": "deselect", "document_id": document_id})
        opaque = await client.execute(
            "edit",
            {
                "action": "select_opaque",
                "document_id": document_id,
                "layer_id": layer_id,
                "combine": "replace",
            },
        )
        if opaque["selection_bounds"] != {"x": 2, "y": 3, "width": 4, "height": 4}:
            raise RuntimeError(f"Select opaque returned wrong bounds: {opaque['selection_bounds']}")

        resized = await client.execute(
            "canvas",
            {
                "action": "resize",
                "document_id": document_id,
                "width": 20,
                "height": 16,
                "anchor": "center",
            },
        )
        if (resized["width"], resized["height"]) != (20, 16):
            raise RuntimeError("Canvas resize returned wrong dimensions")
        shifted = await client.execute(
            "canvas", {"action": "sample_color", "document_id": document_id, "x": 5, "y": 6}
        )
        _assert_rgba(shifted, [204, 51, 102, 255], "center anchor")

        scaled = await client.execute(
            "canvas",
            {
                "action": "scale",
                "document_id": document_id,
                "width": 40,
                "height": 32,
                "strategy": "NearestNeighbor",
            },
        )
        if (scaled["width"], scaled["height"]) != (40, 32):
            raise RuntimeError("Image scale returned wrong dimensions")
        scaled_sample = await client.execute(
            "canvas", {"action": "sample_color", "document_id": document_id, "x": 10, "y": 12}
        )
        _assert_rgba(scaled_sample, [204, 51, 102, 255], "nearest-neighbor scale")

        await client.execute(
            "canvas",
            {"action": "flip", "document_id": document_id, "direction": "horizontal"},
        )
        mirrored = await client.execute(
            "canvas", {"action": "sample_color", "document_id": document_id, "x": 29, "y": 12}
        )
        _assert_rgba(mirrored, [204, 51, 102, 255], "horizontal flip")

        cropped = await client.execute(
            "canvas",
            {
                "action": "crop",
                "document_id": document_id,
                "x": 20,
                "y": 4,
                "width": 16,
                "height": 20,
            },
        )
        if (cropped["width"], cropped["height"]) != (16, 20):
            raise RuntimeError("Canvas crop returned wrong dimensions")
        cropped_sample = await client.execute(
            "canvas", {"action": "sample_color", "document_id": document_id, "x": 9, "y": 8}
        )
        _assert_rgba(cropped_sample, [204, 51, 102, 255], "crop coordinates")

        rotated = await client.execute(
            "canvas",
            {"action": "rotate", "document_id": document_id, "angle_degrees": 90.0},
        )
        if (rotated["width"], rotated["height"]) != (20, 16):
            raise RuntimeError("90-degree rotation did not swap canvas dimensions")

        imported = await client.execute(
            "canvas",
            {
                "action": "import_layer",
                "document_id": document_id,
                "path": str(source_path),
                "name": "Embedded Reference",
                "x": 1,
                "y": 1,
            },
        )
        imported_layer_id = imported["layer_id"]
        imported_sample = await client.execute(
            "canvas", {"action": "sample_color", "document_id": document_id, "x": 1, "y": 1}
        )
        _assert_rgba(imported_sample, [255, 0, 0, 255], "editable import")
        source_path.unlink()

        await client.execute("edit", {"action": "deselect", "document_id": document_id})
        await client.execute(
            "save_export",
            {
                "document_id": document_id,
                "kra_path": str(kra_path),
                "overwrite": True,
            },
        )
        await client.execute(
            "document",
            {"action": "close", "document_id": document_id, "discard_changes": False},
        )
        document_id = None
        reopened = await client.execute("document", {"action": "open", "path": str(kra_path)})
        reopened_id = reopened["affected_document_id"]
        reopened_state = await client.execute("state", {"document_id": reopened_id})
        flat_layers = []
        pending = list(reopened_state["layers"])
        while pending:
            layer = pending.pop()
            flat_layers.append(layer)
            pending.extend(layer.get("children", []))
        matching = [layer for layer in flat_layers if layer["name"] == "Embedded Reference"]
        if len(matching) != 1 or matching[0]["type"].lower() != "paintlayer":
            raise RuntimeError("Imported image did not reopen as one editable paint layer")
        if matching[0]["id"] != imported_layer_id:
            raise RuntimeError("Imported layer UUID did not survive the KRA round trip")
        reopened_sample = await client.execute(
            "canvas", {"action": "sample_color", "document_id": reopened_id, "x": 1, "y": 1}
        )
        _assert_rgba(reopened_sample, [255, 0, 0, 255], "embedded import round trip")

        return {
            "document_id_round_trip": reopened_id,
            "imported_layer_id": imported_layer_id,
            "preview_dimensions": [32, 32],
            "canvas_dimensions": [rotated["width"], rotated["height"]],
            "selection_fill_clear_undo": True,
            "select_opaque": True,
            "nearest_neighbor_scale": True,
            "horizontal_flip": True,
            "editable_import_survived_source_deletion": True,
            "kra_path": str(kra_path),
        }
    finally:
        source_path.unlink(missing_ok=True)
        for target_id in (document_id, reopened_id):
            if target_id is None:
                continue
            with suppress(Exception):
                await client.execute(
                    "edit", {"action": "deselect", "document_id": target_id}
                )
            with suppress(Exception):
                await client.execute(
                    "document",
                    {
                        "action": "close",
                        "document_id": target_id,
                        "discard_changes": True,
                    },
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("e2e-output"))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output_dir)), indent=2))


if __name__ == "__main__":
    main()
