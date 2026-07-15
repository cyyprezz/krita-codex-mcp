from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from krita_codex_mcp.service import InputError, KritaService
from krita_codex_protocol import BridgeConfig


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append((operation, dict(arguments)))
        return {"ok": True}


class KritaServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = _FakeClient()
        config = BridgeConfig.from_mapping({"token": "x" * 32, "allowed_roots": [str(self.root)]})
        self.service = KritaService(config, self.client)

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_normalizes_paint_points(self) -> None:
        await self.service.paint_path(
            points=[{"x": 1, "y": 2}, {"x": 3.5, "y": 4, "pressure": 0.25}]
        )
        operation, arguments = self.client.calls[-1]
        self.assertEqual(operation, "paint_path")
        self.assertEqual(arguments["points"][0]["pressure"], 1.0)
        self.assertEqual(arguments["points"][1]["pressure"], 0.25)

    async def test_rejects_pressure_outside_unit_interval(self) -> None:
        with self.assertRaisesRegex(InputError, "pressure"):
            await self.service.paint_path(
                points=[{"x": 1, "y": 2}, {"x": 3, "y": 4, "pressure": 1.1}]
            )

    async def test_document_open_validates_existing_input(self) -> None:
        target = self.root / "drawing.kra"
        target.write_bytes(b"test")
        await self.service.document(action="open", path=str(target))
        operation, arguments = self.client.calls[-1]
        self.assertEqual(operation, "document")
        self.assertEqual(arguments["path"], str(target.resolve()))

    async def test_document_create_rejects_pixel_limit(self) -> None:
        with self.assertRaisesRegex(InputError, "pixel limit"):
            await self.service.document(action="create", width=16_384, height=16_384)

    async def test_history_normalizes_bounded_steps(self) -> None:
        await self.service.history(action="undo", steps=3)
        self.assertEqual(self.client.calls[-1], ("history", {"action": "undo", "steps": 3}))

    async def test_brush_set_normalizes_hex_and_values(self) -> None:
        await self.service.brush(action="set", foreground="#aabbcc", opacity=0.5, eraser_mode=False)
        operation, arguments = self.client.calls[-1]
        self.assertEqual(operation, "brush")
        self.assertEqual(arguments["foreground"], "#AABBCC")
        self.assertEqual(arguments["opacity"], 0.5)
        self.assertFalse(arguments["eraser_mode"])

    async def test_brush_set_requires_a_property(self) -> None:
        with self.assertRaisesRegex(InputError, "at least one"):
            await self.service.brush(action="set")

    async def test_save_export_sends_only_validated_paths(self) -> None:
        target = self.root / "export.png"
        await self.service.save_export(kra_path=None, png_path=str(target), overwrite=False)
        operation, arguments = self.client.calls[-1]
        self.assertEqual(operation, "save_export")
        self.assertEqual(arguments["png_path"], str(target.resolve()))

    async def test_save_export_rejects_outside_path_before_bridge_call(self) -> None:
        outside = self.root.parent / "outside.png"
        with self.assertRaisesRegex(InputError, "outside"):
            await self.service.save_export(kra_path=None, png_path=str(outside), overwrite=False)
        self.assertEqual(self.client.calls, [])


if __name__ == "__main__":
    unittest.main()
