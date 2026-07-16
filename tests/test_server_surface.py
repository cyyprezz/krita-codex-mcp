from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp.types import ImageContent

from krita_codex_mcp.server import build_server
from krita_codex_protocol import BridgeConfig

PNG_BASE64 = "iVBORw0KGgpwYXlsb2Fk"


class _FakeBridgeClient:
    async def execute(self, operation: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        if operation in {"preview", "asset"}:
            return {"png_base64": PNG_BASE64, "width": 1, "height": 1}
        return {"operation": operation}


class ServerSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        config = BridgeConfig.from_mapping({"token": "x" * 32, "allowed_roots": [self.temp.name]})
        self.server = build_server(config, _FakeBridgeClient())

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_registers_exactly_the_thirteen_cohesive_tools(self) -> None:
        tools = await self.server.list_tools()
        self.assertEqual(len(tools), 13)
        self.assertEqual(
            {tool.name for tool in tools},
            {
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
            },
        )

    async def test_preview_returns_direct_image_content(self) -> None:
        result = await self.server.call_tool("krita_preview", {"max_width": 64, "max_height": 64})
        content, structured = result
        self.assertEqual(len(content), 2)
        self.assertIsInstance(content[1], ImageContent)
        self.assertEqual(content[1].mimeType, "image/png")
        self.assertEqual(content[1].data, PNG_BASE64)
        self.assertEqual(structured["result"][1]["type"], "image")

    async def test_asset_export_returns_direct_image_content(self) -> None:
        result = await self.server.call_tool(
            "krita_asset",
            {
                "action": "export_layer",
                "layer_id": "layer-1",
                "output_path": str(Path(self.temp.name) / "frame.png"),
                "preview_max_width": 64,
                "preview_max_height": 64,
            },
        )
        content, structured = result
        self.assertEqual(len(content), 2)
        self.assertIsInstance(content[1], ImageContent)
        self.assertEqual(content[1].mimeType, "image/png")
        self.assertEqual(structured["result"][1]["type"], "image")

    async def test_asset_schema_requires_layer_and_output_path(self) -> None:
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        required = tools["krita_asset"].inputSchema["required"]
        self.assertIn("layer_id", required)
        self.assertIn("output_path", required)
        actions = tools["krita_asset"].inputSchema["properties"]["action"]["enum"]
        self.assertEqual(
            actions,
            ["export_layer", "export_animation", "export_tileset", "export_regions"],
        )
        properties = tools["krita_asset"].inputSchema["properties"]
        for field in ("tile_width", "tile_height", "grid_columns", "grid_rows", "regions"):
            self.assertIn(field, properties)

    async def test_canvas_and_edit_schemas_expose_bounded_actions(self) -> None:
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        self.assertEqual(
            tools["krita_canvas"].inputSchema["properties"]["action"]["enum"],
            [
                "read",
                "sample_color",
                "resize",
                "scale",
                "crop",
                "rotate",
                "flip",
                "import_layer",
            ],
        )
        self.assertEqual(
            tools["krita_edit"].inputSchema["properties"]["action"]["enum"],
            [
                "selection_status",
                "select_rect",
                "select_all",
                "select_opaque",
                "deselect",
                "invert",
                "grow",
                "shrink",
                "move_selection",
                "resize_selection",
                "copy",
                "cut",
                "paste",
                "copy_to_layer",
                "cut_to_layer",
                "fill",
                "clear",
            ],
        )
        self.assertIn("document_id", tools["krita_canvas"].inputSchema["properties"])
        self.assertIn("document_id", tools["krita_edit"].inputSchema["properties"])

    async def test_layer_schema_exposes_persistent_asset_operations(self) -> None:
        tools = await self.server.list_tools()
        layer_tool = next(tool for tool in tools if tool.name == "krita_layers")
        self.assertEqual(
            layer_tool.inputSchema["properties"]["action"]["enum"],
            [
                "read",
                "create",
                "activate",
                "rename",
                "set_properties",
                "duplicate",
                "delete",
                "reorder",
                "translate",
                "scale",
                "rotate",
                "shear",
                "crop",
                "flip",
                "merge_down",
            ],
        )
        for field in ("locked", "alpha_locked", "inherit_alpha", "blending_mode"):
            self.assertIn(field, layer_tool.inputSchema["properties"])
        self.assertEqual(
            layer_tool.inputSchema["properties"]["transform_mode"]["enum"],
            ["mask", "direct"],
        )
        self.assertEqual(
            layer_tool.inputSchema["properties"]["response_mode"]["enum"],
            ["compact", "full"],
        )
        self.assertIn("checkpoint_reopen", layer_tool.inputSchema["properties"])

    async def test_document_targeting_and_checkpoint_are_exposed(self) -> None:
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        document_actions = tools["krita_document"].inputSchema["properties"]["action"]["enum"]
        self.assertIn("save", document_actions)
        for name in (
            "krita_state",
            "krita_preview",
            "krita_canvas",
            "krita_edit",
            "krita_layers",
            "krita_history",
            "krita_brush",
            "krita_paint_path",
            "krita_draw",
            "krita_color",
            "krita_asset",
            "krita_save_export",
        ):
            self.assertIn("document_id", tools[name].inputSchema["properties"], name)

    async def test_draw_and_color_schemas_are_cohesive_and_bounded(self) -> None:
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        self.assertEqual(
            tools["krita_draw"].inputSchema["properties"]["shape"]["enum"],
            ["line", "path", "rectangle", "ellipse", "polygon"],
        )
        self.assertEqual(
            tools["krita_color"].inputSchema["properties"]["action"]["enum"],
            [
                "read",
                "list_palettes",
                "read_palette",
                "set_project_palette",
                "apply_swatch",
                "replace_color",
                "adjust",
            ],
        )
        self.assertIn("entries", tools["krita_color"].inputSchema["properties"])
        self.assertIn("settings", tools["krita_color"].inputSchema["properties"])


if __name__ == "__main__":
    unittest.main()
