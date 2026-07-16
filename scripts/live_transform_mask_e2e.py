from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
from contextlib import suppress
from pathlib import Path

from krita_codex_mcp.bridge_client import BridgeCallError, BridgeClient
from krita_codex_protocol import BridgeConfig

DOCUMENT_NAME = "Codex Transform Mask E2E"


def flatten_layers(layers: list[dict[str, object]]) -> list[dict[str, object]]:
    flattened: list[dict[str, object]] = []
    for layer in layers:
        flattened.append(layer)
        children = layer.get("children")
        if isinstance(children, list):
            flattened.extend(flatten_layers(children))
    return flattened


async def preview_hash(client: BridgeClient, document_id: str) -> str:
    preview = await client.execute(
        "preview",
        {"document_id": document_id, "max_width": 64, "max_height": 64},
    )
    payload = base64.b64decode(preview["png_base64"], validate=True)
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Transform-mask preview is not a PNG")
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


async def fill_rect(
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


async def run(output_dir: Path) -> dict[str, object]:
    client = BridgeClient(BridgeConfig.load())
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    kra_path = output_dir / "transform-mask-live.kra"
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
                "name": "Asymmetric Transform Target",
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
        await fill_rect(
            client,
            document_id,
            layer_id,
            x=8,
            y=10,
            width=6,
            height=12,
            color="#FF0000FF",
        )
        await fill_rect(
            client,
            document_id,
            layer_id,
            x=13,
            y=17,
            width=5,
            height=4,
            color="#0000FFFF",
        )
        await client.execute("edit", {"action": "deselect", "document_id": document_id})
        original_hash = await preview_hash(client, document_id)

        operations = [
            (
                "scale",
                {
                    "width": 20,
                    "height": 24,
                    "x": 16,
                    "y": 12,
                    "strategy": "NearestNeighbor",
                },
            ),
            ("rotate", {"angle_degrees": 30.0}),
            ("shear", {"shear_x_degrees": 12.0, "shear_y_degrees": -5.0}),
        ]
        results: list[dict[str, object]] = []
        hashes = [original_hash]
        for action, options in operations:
            result = await client.execute(
                "layers",
                {
                    "action": action,
                    "document_id": document_id,
                    "layer_id": layer_id,
                    "transform_mode": "mask",
                    **options,
                },
            )
            if result.get("transform_mode") != "mask" or not result.get(
                "transform_mask_id"
            ):
                raise RuntimeError(f"{action} did not create a transform mask")
            results.append(result)
            hashes.append(await preview_hash(client, document_id))
        if len(set(hashes)) != len(hashes):
            raise RuntimeError("One of the sequential transform masks had no visible effect")

        layers = await client.execute("layers", {"action": "read", "document_id": document_id})
        flat = flatten_layers(layers["layers"])
        operation_mask_ids = [str(item["transform_mask_id"]) for item in results]
        if len(set(operation_mask_ids)) != len(operation_mask_ids):
            raise RuntimeError("Sequential operations reused an unsafe transform-mask instance")
        mask_nodes = [item for item in flat if item["id"] in operation_mask_ids]
        if len(mask_nodes) != 3 or any(
            str(item["type"]).lower() != "transformmask" for item in mask_nodes
        ):
            raise RuntimeError("Layer tree did not expose all three transform masks")
        for index, item in enumerate(results, start=1):
            if not item.get("transform_mask_created") or item.get(
                "transform_mask_count"
            ) != index:
                raise RuntimeError("Transform-mask creation reporting is inconsistent")

        transformed_hash = hashes[-1]
        for action, arguments in (
            ("delete", {}),
            ("set_properties", {"visible": False}),
        ):
            try:
                await client.execute(
                    "layers",
                    {
                        "action": action,
                        "document_id": document_id,
                        "layer_id": operation_mask_ids[0],
                        **arguments,
                    },
                )
            except BridgeCallError as exc:
                if exc.code != "UNSAFE_TRANSFORM_MASK_MUTATION":
                    raise
            else:
                raise RuntimeError(f"Unsafe same-session transform-mask {action} was allowed")
        try:
            await client.execute(
                "layers",
                {
                    "action": "rotate",
                    "document_id": document_id,
                    "layer_id": layer_id,
                    "angle_degrees": 1.0,
                    "transform_mode": "direct",
                },
            )
        except BridgeCallError as exc:
            if exc.code != "UNSAFE_TRANSFORM_BACKEND_MIX":
                raise
        else:
            raise RuntimeError("Unsafe transform backend mixing was allowed")
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
        reopened_layers = await client.execute(
            "layers", {"action": "read", "document_id": reopened_id}
        )
        reopened_ids = {item["id"] for item in flatten_layers(reopened_layers["layers"])}
        if layer_id not in reopened_ids or not set(operation_mask_ids).issubset(reopened_ids):
            raise RuntimeError("Layer or transform-mask UUIDs changed across the KRA round trip")
        if await preview_hash(client, reopened_id) != transformed_hash:
            raise RuntimeError("Transform-mask projection changed across the KRA round trip")

        try:
            await client.execute(
                "layers",
                {
                    "action": "rotate",
                    "document_id": reopened_id,
                    "layer_id": layer_id,
                    "angle_degrees": 1.0,
                    "transform_mode": "mask",
                },
            )
        except BridgeCallError as exc:
            if exc.code != "UNSAFE_TRANSFORM_MASK_SEQUENCE":
                raise
        else:
            raise RuntimeError("Transform-mask safety limit did not fail closed")

        for mask_id in reversed(operation_mask_ids):
            await client.execute(
                "layers",
                {
                    "action": "delete",
                    "document_id": reopened_id,
                    "layer_id": mask_id,
                },
            )
        restored_hash = await preview_hash(client, reopened_id)
        if restored_hash != original_hash:
            raise RuntimeError("Removing transform masks did not restore the source projection")

        return {
            "mask_ids": operation_mask_ids,
            "mask_types": [item["type"] for item in mask_nodes],
            "preview_hashes": hashes,
            "matrices": [item["affine_matrix"] for item in results],
            "kra_round_trip": True,
            "source_restored": True,
            "mask_limit_enforced": True,
            "same_session_mutation_guard": True,
            "backend_mix_guard": True,
            "kra_path": str(kra_path),
        }
    finally:
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
                    {"action": "close", "document_id": target_id, "discard_changes": True},
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build/live-transform-mask"),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(run(args.output_dir)),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
