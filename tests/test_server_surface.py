from __future__ import annotations

import tempfile
import unittest
from typing import Any

from mcp.types import ImageContent

from krita_codex_mcp.server import build_server
from krita_codex_protocol import BridgeConfig


class _FakeBridgeClient:
    async def execute(self, operation: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        if operation == "preview":
            return {"png_base64": "aGVsbG8=", "width": 1, "height": 1}
        return {"operation": operation}


class ServerSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        config = BridgeConfig.from_mapping({"token": "x" * 32, "allowed_roots": [self.temp.name]})
        self.server = build_server(config, _FakeBridgeClient())

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_registers_exactly_the_eight_cohesive_tools(self) -> None:
        tools = await self.server.list_tools()
        self.assertEqual(
            [tool.name for tool in tools],
            [
                "krita_state",
                "krita_preview",
                "krita_document",
                "krita_layers",
                "krita_history",
                "krita_brush",
                "krita_paint_path",
                "krita_save_export",
            ],
        )

    async def test_preview_returns_direct_image_content(self) -> None:
        result = await self.server.call_tool("krita_preview", {"max_width": 64, "max_height": 64})
        content, structured = result
        self.assertEqual(len(content), 2)
        self.assertIsInstance(content[1], ImageContent)
        self.assertEqual(content[1].mimeType, "image/png")
        self.assertEqual(content[1].data, "aGVsbG8=")
        self.assertEqual(structured["result"][1]["type"], "image")


if __name__ == "__main__":
    unittest.main()
