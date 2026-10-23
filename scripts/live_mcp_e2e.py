from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path

import mcp_types as types
from mcp import Client, StdioServerParameters


async def run(executable: Path, repo_root: Path) -> dict[str, object]:
    parameters = StdioServerParameters(command=str(executable), cwd=repo_root)
    async with Client(parameters) as session:
        listed = await session.list_tools()
        names = [tool.name for tool in listed.tools]
        expected = {
            "krita_state",
            "krita_preview",
            "krita_canvas",
            "krita_edit",
            "krita_document",
            "krita_layers",
            "krita_history",
            "krita_brush",
            "krita_paint_path",
            "krita_draw",
            "krita_color",
            "krita_asset",
            "krita_save_export",
        }
        if set(names) != expected:
            raise RuntimeError(f"Unexpected MCP tool surface: {names}")
        asset_tool = next(tool for tool in listed.tools if tool.name == "krita_asset")
        if "output_path" not in asset_tool.input_schema.get("required", []):
            raise RuntimeError("krita_asset does not require its output path")
        asset_actions = asset_tool.input_schema["properties"]["action"].get("enum", [])
        if asset_actions != [
            "export_layer",
            "export_animation",
            "export_tileset",
            "export_regions",
        ]:
            raise RuntimeError(f"Unexpected krita_asset actions: {asset_actions}")

        created_document_id = None
        asset_smoke = None
        creation_smoke = None
        try:
            state = await session.call_tool("krita_state", {})
            if state.is_error:
                created = await session.call_tool(
                    "krita_document",
                    {
                        "action": "create",
                        "width": 128,
                        "height": 96,
                        "name": "Codex MCP Smoke Test",
                    },
                )
                if created.is_error:
                    raise RuntimeError("krita_document could not create the smoke-test document")
                created_payload = _structured_payload(created)
                created_document_id = created_payload.get("affected_document_id")
                if not isinstance(created_document_id, str):
                    raise RuntimeError("krita_document returned no affected document ID")
                state = await session.call_tool("krita_state", {})
            if state.is_error:
                raise RuntimeError("krita_state returned an MCP tool error")

            if created_document_id is not None:
                state_payload = _structured_payload(state)
                background_id = state_payload.get("active_layer_id")
                if not isinstance(background_id, str):
                    raise RuntimeError("krita_state returned no active background layer ID")
                layer = await session.call_tool(
                    "krita_layers",
                    {
                        "action": "create",
                        "document_id": created_document_id,
                        "name": "MCP Asset Smoke",
                        "kind": "paintlayer",
                    },
                )
                layer_id = _structured_payload(layer).get("affected_layer_id")
                if layer.is_error or not isinstance(layer_id, str):
                    raise RuntimeError("krita_layers could not create the asset-smoke layer")
                hidden = await session.call_tool(
                    "krita_layers",
                    {
                        "action": "set_properties",
                        "document_id": created_document_id,
                        "layer_id": background_id,
                        "visible": False,
                    },
                )
                if hidden.is_error:
                    raise RuntimeError("krita_layers could not hide the smoke background")
                selected = await session.call_tool(
                    "krita_edit",
                    {
                        "action": "select_rect",
                        "document_id": created_document_id,
                        "x": 0,
                        "y": 0,
                        "width": 32,
                        "height": 32,
                    },
                )
                filled = await session.call_tool(
                    "krita_edit",
                    {
                        "action": "fill",
                        "document_id": created_document_id,
                        "layer_id": layer_id,
                        "color": "#7E57C2FF",
                    },
                )
                if selected.is_error or filled.is_error:
                    raise RuntimeError("krita_edit could not prepare the asset-smoke tile")
                await session.call_tool(
                    "krita_edit",
                    {"action": "deselect", "document_id": created_document_id},
                )
                drawn = await session.call_tool(
                    "krita_draw",
                    {
                        "shape": "ellipse",
                        "document_id": created_document_id,
                        "layer_id": layer_id,
                        "x": 8,
                        "y": 8,
                        "width": 12,
                        "height": 12,
                        "stroke_style": "None",
                        "fill_style": "ForegroundColor",
                        "foreground": "#42D6C6FF",
                    },
                )
                palette = await session.call_tool(
                    "krita_color",
                    {
                        "action": "set_project_palette",
                        "document_id": created_document_id,
                        "project_name": "MCP Smoke",
                        "entries": [{"name": "Accent", "color": "#42D6C6FF"}],
                    },
                )
                if drawn.is_error or palette.is_error:
                    raise RuntimeError("krita_draw or krita_color failed through STDIO MCP")
                creation_smoke = {
                    "shape": _structured_payload(drawn).get("shape"),
                    "project_palette": _structured_payload(palette).get("project_palette"),
                }
                output_parent = repo_root / "e2e-output"
                output_parent.mkdir(parents=True, exist_ok=True)
                output_path = output_parent / "mcp-smoke-regions"
                exported = await session.call_tool(
                    "krita_asset",
                    {
                        "action": "export_tileset",
                        "document_id": created_document_id,
                        "layer_id": layer_id,
                        "output_path": str(output_path),
                        "overwrite": True,
                        "tile_width": 32,
                        "tile_height": 32,
                        "skip_empty": True,
                        "pixels_per_unit": 32,
                    },
                )
                if exported.is_error:
                    raise RuntimeError("krita_asset could not export the smoke-test tileset")
                asset_images = [
                    item for item in exported.content if isinstance(item, types.ImageContent)
                ]
                asset_metadata = _text_payload(exported)
                if len(asset_images) != 1 or asset_images[0].mime_type != "image/png":
                    raise RuntimeError("krita_asset returned no direct PNG ImageContent")
                if asset_metadata.get("region_count") != 1:
                    raise RuntimeError("krita_asset skip_empty did not retain exactly one tile")
                manifest_path = output_path / "manifest.json"
                if not manifest_path.is_file():
                    raise RuntimeError("krita_asset created no managed manifest")
                asset_smoke = {
                    "action": asset_metadata.get("action"),
                    "region_count": asset_metadata.get("region_count"),
                    "image_mime_type": asset_images[0].mime_type,
                    "manifest_path": str(manifest_path),
                }

            preview = await session.call_tool(
                "krita_preview", {"max_width": 256, "max_height": 256}
            )
            if preview.is_error:
                raise RuntimeError("krita_preview returned an MCP tool error")
            images = [item for item in preview.content if isinstance(item, types.ImageContent)]
            texts = [item for item in preview.content if isinstance(item, types.TextContent)]
            if len(images) != 1 or images[0].mime_type != "image/png":
                raise RuntimeError("krita_preview did not return exactly one PNG ImageContent")
            image_bytes = base64.b64decode(images[0].data, validate=True)
            if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("MCP ImageContent payload is not a PNG")
            if not texts:
                raise RuntimeError("krita_preview did not return its metadata TextContent")
            preview_metadata = _text_payload(preview)
            if created_document_id is not None and (
                preview_metadata.get("width") != 128
                or preview_metadata.get("height") != 96
            ):
                raise RuntimeError(
                    "Whole-canvas preview dropped transparent margins: "
                    f"{preview_metadata.get('width')}x{preview_metadata.get('height')}"
                )

            return {
                "tools": names,
                "asset_actions": asset_actions,
                "created_smoke_document": created_document_id is not None,
                "state_content_types": [type(item).__name__ for item in state.content],
                "preview_content_types": [type(item).__name__ for item in preview.content],
                "preview_mime_type": images[0].mime_type,
                "preview_bytes": len(image_bytes),
                "preview_size": [
                    preview_metadata.get("width"),
                    preview_metadata.get("height"),
                ],
                "asset_smoke": asset_smoke,
                "creation_smoke": creation_smoke,
            }
        finally:
            if created_document_id is not None:
                await session.call_tool(
                    "krita_edit",
                    {"action": "deselect", "document_id": created_document_id},
                )
                closed = await session.call_tool(
                    "krita_document",
                    {
                        "action": "close",
                        "document_id": created_document_id,
                        "discard_changes": True,
                    },
                )
                if closed.is_error:
                    raise RuntimeError("Could not close the MCP smoke-test document")
                closed_payload = _structured_payload(closed)
                if (
                    closed_payload.get("documents") == []
                    and closed_payload.get("active_document_id") is not None
                ):
                    raise RuntimeError("Closing the last document retained a stale active ID")


def _structured_payload(result) -> dict[str, object]:
    if isinstance(result.structured_content, dict):
        return result.structured_content
    for item in result.content:
        if isinstance(item, types.TextContent):
            raw = json.loads(item.text)
            if isinstance(raw, dict):
                return raw
    raise RuntimeError("MCP tool returned no structured object")


def _text_payload(result) -> dict[str, object]:
    for item in result.content:
        if isinstance(item, types.TextContent):
            raw = json.loads(item.text)
            if isinstance(raw, dict):
                return raw
    raise RuntimeError("MCP tool returned no text metadata object")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    default_executable = repo_root / ".venv" / "Scripts" / "krita-codex-mcp.exe"
    parser = argparse.ArgumentParser(description="Exercise the installed MCP STDIO surface")
    parser.add_argument("--executable", type=Path, default=default_executable)
    arguments = parser.parse_args()
    executable = arguments.executable.resolve()
    if not executable.is_file():
        raise SystemExit(f"MCP executable not found: {executable}")
    result = asyncio.run(run(executable, repo_root))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
