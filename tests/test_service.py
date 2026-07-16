from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from krita_codex_mcp.bridge_client import BridgeCallError
from krita_codex_mcp.service import InputError, KritaService
from krita_codex_protocol import BridgeConfig

PNG_BASE64 = "iVBORw0KGgpwYXlsb2Fk"


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append((operation, dict(arguments)))
        return {"ok": True}


def _asset_execute(client: _FakeClient):
    async def execute(operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        client.calls.append((operation, dict(arguments)))
        return {"png_base64": PNG_BASE64, "output": "ok"}

    return execute


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

    async def test_preview_normalizes_explicit_region_and_scale(self) -> None:
        self.client.execute = _asset_execute(self.client)
        metadata, png_base64 = await self.service.preview(
            512,
            512,
            document_id="document-1",
            x=10,
            y=20,
            width=32,
            height=16,
            scale=8,
        )
        self.assertEqual(png_base64, PNG_BASE64)
        self.assertEqual(metadata, {"output": "ok"})
        self.assertEqual(
            self.client.calls[-1],
            (
                "preview",
                {
                    "max_width": 512,
                    "max_height": 512,
                    "region": {"x": 10, "y": 20, "width": 32, "height": 16},
                    "scale": 8,
                    "document_id": "document-1",
                },
            ),
        )

    async def test_preview_rejects_partial_region(self) -> None:
        with self.assertRaisesRegex(InputError, "requires x, y, width, and height"):
            await self.service.preview(256, 256, x=0, y=0, width=16)
        self.assertEqual(self.client.calls, [])

    async def test_preview_rejects_base64_that_is_not_png(self) -> None:
        async def invalid_preview(
            operation: str, arguments: Mapping[str, Any]
        ) -> dict[str, Any]:
            self.client.calls.append((operation, dict(arguments)))
            return {"png_base64": "aGVsbG8="}

        self.client.execute = invalid_preview
        with self.assertRaises(BridgeCallError) as raised:
            await self.service.preview(64, 64)
        self.assertEqual(raised.exception.code, "INVALID_PREVIEW")
        self.assertIn("not a PNG", raised.exception.message)

    async def test_canvas_resize_normalizes_anchor_and_limits(self) -> None:
        await self.service.canvas(action="resize", width=320, height=180, anchor="bottom_right")
        self.assertEqual(
            self.client.calls[-1],
            (
                "canvas",
                {
                    "action": "resize",
                    "width": 320,
                    "height": 180,
                    "anchor": "bottom_right",
                },
            ),
        )

    async def test_canvas_scale_normalizes_strategy(self) -> None:
        await self.service.canvas(
            action="scale", width=128, height=64, strategy="NearestNeighbor"
        )
        self.assertEqual(self.client.calls[-1][1]["strategy"], "NearestNeighbor")

    async def test_canvas_import_validates_input_path_and_embedded_options(self) -> None:
        source = self.root / "reference.png"
        source.write_bytes(b"png")
        await self.service.canvas(
            action="import_layer",
            document_id="document-1",
            path=str(source),
            name="Reference",
            x=4,
            y=-3,
            parent_id="group-1",
        )
        self.assertEqual(
            self.client.calls[-1],
            (
                "canvas",
                {
                    "action": "import_layer",
                    "document_id": "document-1",
                    "path": str(source.resolve()),
                    "name": "Reference",
                    "x": 4,
                    "y": -3,
                    "parent_id": "group-1",
                },
            ),
        )

    async def test_canvas_import_rejects_outside_path(self) -> None:
        outside = self.root.parent / "outside.png"
        outside.write_bytes(b"png")
        try:
            with self.assertRaisesRegex(InputError, "outside"):
                await self.service.canvas(action="import_layer", path=str(outside))
        finally:
            outside.unlink(missing_ok=True)
        self.assertEqual(self.client.calls, [])

    async def test_edit_rect_fill_and_clear_normalize_stable_layer_target(self) -> None:
        await self.service.edit(
            action="select_rect",
            document_id="document-1",
            x=1,
            y=2,
            width=10,
            height=12,
            combine="replace",
        )
        self.assertEqual(self.client.calls[-1][0], "edit")
        self.assertEqual(self.client.calls[-1][1]["width"], 10)
        await self.service.edit(
            action="fill", layer_id="layer-1", color="#aabbcc80"
        )
        self.assertEqual(self.client.calls[-1][1]["color"], "#AABBCC80")
        await self.service.edit(action="clear", layer_id="layer-1")
        self.assertEqual(
            self.client.calls[-1],
            ("edit", {"action": "clear", "layer_id": "layer-1"}),
        )

    async def test_edit_grow_requires_bounded_amount(self) -> None:
        with self.assertRaisesRegex(InputError, "amount"):
            await self.service.edit(action="grow", amount=0)
        self.assertEqual(self.client.calls, [])

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

    async def test_document_save_requires_and_forwards_document_id(self) -> None:
        with self.assertRaisesRegex(InputError, "document_id"):
            await self.service.document(action="save")
        self.assertEqual(self.client.calls, [])

        await self.service.document(action="save", document_id="document-1")
        self.assertEqual(
            self.client.calls[-1],
            ("document", {"action": "save", "document_id": "document-1"}),
        )

    async def test_state_and_layer_operations_forward_document_target(self) -> None:
        await self.service.state(document_id="document-1")
        self.assertEqual(
            self.client.calls[-1],
            ("state", {"document_id": "document-1"}),
        )
        await self.service.layers(
            action="rename",
            document_id="document-1",
            layer_id="layer-1",
            name="Idle 001",
        )
        self.assertEqual(
            self.client.calls[-1],
            (
                "layers",
                {
                    "action": "rename",
                    "document_id": "document-1",
                    "layer_id": "layer-1",
                    "name": "Idle 001",
                },
            ),
        )

    async def test_history_normalizes_bounded_steps(self) -> None:
        await self.service.history(action="undo", steps=3)
        self.assertEqual(self.client.calls[-1], ("history", {"action": "undo", "steps": 3}))

    async def test_layer_duplicate_accepts_an_optional_name(self) -> None:
        await self.service.layers(action="duplicate", layer_id="layer-1", name="Run 02")
        self.assertEqual(
            self.client.calls[-1],
            (
                "layers",
                {"action": "duplicate", "layer_id": "layer-1", "name": "Run 02"},
            ),
        )

    async def test_layer_reorder_normalizes_destination_and_anchor(self) -> None:
        await self.service.layers(
            action="reorder",
            layer_id="layer-1",
            parent_id="group-1",
            above_id="layer-2",
        )
        self.assertEqual(
            self.client.calls[-1],
            (
                "layers",
                {
                    "action": "reorder",
                    "layer_id": "layer-1",
                    "parent_id": "group-1",
                    "above_id": "layer-2",
                },
            ),
        )

    async def test_layer_translate_fills_missing_axis(self) -> None:
        await self.service.layers(action="translate", layer_id="layer-1", offset_x=12)
        self.assertEqual(
            self.client.calls[-1],
            (
                "layers",
                {
                    "action": "translate",
                    "layer_id": "layer-1",
                    "offset_x": 12,
                    "offset_y": 0,
                },
            ),
        )

    async def test_layer_translate_rejects_zero_offset(self) -> None:
        with self.assertRaisesRegex(InputError, "non-zero"):
            await self.service.layers(
                action="translate", layer_id="layer-1", offset_x=0, offset_y=0
            )
        self.assertEqual(self.client.calls, [])

    async def test_layer_properties_and_merge_down_are_normalized(self) -> None:
        await self.service.layers(
            action="set_properties",
            layer_id="layer-1",
            locked=False,
            alpha_locked=True,
            inherit_alpha=True,
            blending_mode="multiply",
        )
        self.assertEqual(
            self.client.calls[-1],
            (
                "layers",
                {
                    "action": "set_properties",
                    "layer_id": "layer-1",
                    "locked": False,
                    "alpha_locked": True,
                    "inherit_alpha": True,
                    "blending_mode": "multiply",
                },
            ),
        )
        await self.service.layers(action="merge_down", layer_id="layer-1")
        self.assertEqual(
            self.client.calls[-1],
            ("layers", {"action": "merge_down", "layer_id": "layer-1"}),
        )

    async def test_layer_properties_reject_unknown_blending_mode_before_bridge(self) -> None:
        with self.assertRaisesRegex(InputError, "blending_mode"):
            await self.service.layers(
                action="set_properties",
                layer_id="layer-1",
                blending_mode="totally_unknown",
            )
        self.assertEqual(self.client.calls, [])

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
        await self.service.save_export(
            kra_path=None,
            png_path=str(target),
            overwrite=False,
            document_id="document-1",
        )
        operation, arguments = self.client.calls[-1]
        self.assertEqual(operation, "save_export")
        self.assertEqual(arguments["png_path"], str(target.resolve()))
        self.assertEqual(arguments["document_id"], "document-1")

    async def test_asset_export_validates_path_and_returns_preview(self) -> None:
        target = self.root / "idle-000.png"
        self.client.execute = _asset_execute(self.client)
        metadata, png_base64 = await self.service.asset(
            action="export_layer",
            document_id="document-1",
            layer_id="layer-1",
            output_path=str(target),
            crop="canvas",
            overwrite=False,
            preview_max_width=256,
            preview_max_height=128,
        )
        self.assertEqual(png_base64, PNG_BASE64)
        self.assertEqual(metadata, {"output": "ok"})
        operation, arguments = self.client.calls[-1]
        self.assertEqual(operation, "asset")
        self.assertEqual(arguments["output_path"], str(target.resolve()))
        self.assertEqual(arguments["document_id"], "document-1")

    async def test_asset_export_rejects_outside_path(self) -> None:
        outside = self.root.parent / "outside.png"
        with self.assertRaisesRegex(InputError, "outside"):
            await self.service.asset(
                action="export_layer",
                layer_id="layer-1",
                output_path=str(outside),
            )
        self.assertEqual(self.client.calls, [])

    async def test_asset_export_rejects_preview_that_is_not_png(self) -> None:
        async def invalid_asset(
            operation: str, arguments: Mapping[str, Any]
        ) -> dict[str, Any]:
            self.client.calls.append((operation, dict(arguments)))
            return {"png_base64": "aGVsbG8="}

        self.client.execute = invalid_asset
        with self.assertRaises(BridgeCallError) as raised:
            await self.service.asset(
                action="export_layer",
                layer_id="layer-1",
                output_path=str(self.root / "invalid-preview.png"),
            )
        self.assertEqual(raised.exception.code, "INVALID_PREVIEW")
        self.assertIn("not a PNG", raised.exception.message)

    async def test_animation_export_normalizes_unity_package_options(self) -> None:
        target = self.root / "hero_idle"
        self.client.execute = _asset_execute(self.client)
        metadata, png_base64 = await self.service.asset(
            action="export_animation",
            document_id="document-1",
            layer_id="group-1",
            output_path=str(target),
            crop="union",
            frame_ids=["frame-2", "frame-1"],
            animation_name="Hero Idle",
            columns=2,
            padding=3,
            frame_duration_ms=125,
            pivot_x=0.5,
            pivot_y=0.1,
            pixels_per_unit=32,
            loop=True,
        )
        self.assertEqual(png_base64, PNG_BASE64)
        self.assertEqual(metadata, {"output": "ok"})
        operation, arguments = self.client.calls[-1]
        self.assertEqual(operation, "asset")
        self.assertEqual(arguments["output_path"], str(target.resolve()))
        self.assertEqual(arguments["frame_ids"], ["frame-2", "frame-1"])
        self.assertEqual(arguments["frame_duration_ms"], 125)
        self.assertEqual(arguments["pixels_per_unit"], 32.0)
        self.assertTrue(arguments["loop"])

    async def test_animation_export_rejects_duplicate_frame_ids(self) -> None:
        with self.assertRaisesRegex(InputError, "duplicates"):
            await self.service.asset(
                action="export_animation",
                layer_id="group-1",
                output_path=str(self.root / "hero_idle"),
                frame_ids=["frame-1", "frame-1"],
            )
        self.assertEqual(self.client.calls, [])

    async def test_animation_export_rejects_case_variant_duplicate_frame_ids(self) -> None:
        with self.assertRaisesRegex(InputError, "duplicates"):
            await self.service.asset(
                action="export_animation",
                layer_id="group-1",
                output_path=str(self.root / "hero_idle"),
                frame_ids=["FRAME-1", "frame-1"],
            )
        self.assertEqual(self.client.calls, [])

    async def test_tileset_export_normalizes_grid_package_options(self) -> None:
        target = self.root / "dungeon_tiles"
        self.client.execute = _asset_execute(self.client)
        metadata, png_base64 = await self.service.asset(
            action="export_tileset",
            document_id="document-1",
            layer_id="layer-1",
            output_path=str(target),
            tile_width=16,
            tile_height=8,
            origin_x=2,
            origin_y=3,
            grid_columns=4,
            grid_rows=2,
            spacing_x=1,
            spacing_y=2,
            skip_empty=True,
            name_prefix="dungeon",
            package_name="Dungeon Tiles",
            pixels_per_unit=16,
        )
        self.assertEqual(png_base64, PNG_BASE64)
        self.assertEqual(metadata, {"output": "ok"})
        operation, arguments = self.client.calls[-1]
        self.assertEqual(operation, "asset")
        self.assertEqual(arguments["action"], "export_tileset")
        self.assertEqual(arguments["grid_columns"], 4)
        self.assertEqual(arguments["grid_rows"], 2)
        self.assertEqual(arguments["pixels_per_unit"], 16.0)
        self.assertNotIn("crop", arguments)

    async def test_region_export_normalizes_named_rectangles(self) -> None:
        target = self.root / "hero_regions"
        self.client.execute = _asset_execute(self.client)
        await self.service.asset(
            action="export_regions",
            layer_id="layer-1",
            output_path=str(target),
            regions=[
                {"name": "door_closed", "x": 1, "y": 2, "width": 16, "height": 24},
                {"name": "door_open", "x": 20, "y": 2, "width": 16, "height": 24},
            ],
        )
        arguments = self.client.calls[-1][1]
        self.assertEqual(arguments["regions"][1]["name"], "door_open")
        self.assertEqual(arguments["regions"][1]["x"], 20)

    async def test_region_export_rejects_duplicate_names_before_bridge(self) -> None:
        with self.assertRaisesRegex(InputError, "unique"):
            await self.service.asset(
                action="export_regions",
                layer_id="layer-1",
                output_path=str(self.root / "hero_regions"),
                regions=[
                    {"name": "Door", "x": 0, "y": 0, "width": 8, "height": 8},
                    {"name": "door", "x": 8, "y": 0, "width": 8, "height": 8},
                ],
            )
        self.assertEqual(self.client.calls, [])

    async def test_edit_clipboard_and_selection_transform_payloads(self) -> None:
        await self.service.edit(
            action="move_selection", document_id="document-1", x=4, y=5
        )
        self.assertEqual(
            self.client.calls[-1],
            ("edit", {"action": "move_selection", "document_id": "document-1", "x": 4, "y": 5}),
        )
        await self.service.edit(action="copy_to_layer", layer_id="layer-1")
        self.assertEqual(
            self.client.calls[-1],
            ("edit", {"action": "copy_to_layer", "layer_id": "layer-1"}),
        )

    async def test_layer_native_transform_payloads(self) -> None:
        await self.service.layers(
            action="scale",
            layer_id="layer-1",
            width=32,
            height=48,
            x=2,
            y=3,
            strategy="NearestNeighbor",
        )
        self.assertEqual(
            self.client.calls[-1],
            (
                "layers",
                {
                    "action": "scale",
                    "layer_id": "layer-1",
                    "width": 32,
                    "height": 48,
                    "strategy": "NearestNeighbor",
                    "x": 2,
                    "y": 3,
                    "transform_mode": "mask",
                },
            ),
        )

        await self.service.layers(
            action="rotate",
            layer_id="layer-1",
            angle_degrees=15,
            transform_mode="direct",
        )
        self.assertEqual(self.client.calls[-1][1]["transform_mode"], "direct")

    async def test_layer_transform_rejects_unknown_mode_before_bridge(self) -> None:
        with self.assertRaisesRegex(InputError, "transform_mode"):
            await self.service.layers(
                action="rotate",
                layer_id="layer-1",
                angle_degrees=15,
                transform_mode="unsafe",
            )
        self.assertEqual(self.client.calls, [])
        await self.service.layers(
            action="flip", layer_id="layer-1", direction="horizontal"
        )
        self.assertEqual(self.client.calls[-1][1]["direction"], "horizontal")

        call_count = len(self.client.calls)
        with self.assertRaisesRegex(InputError, "pixel limit"):
            await self.service.layers(
                action="crop",
                layer_id="layer-1",
                x=0,
                y=0,
                width=16384,
                height=16384,
            )
        self.assertEqual(len(self.client.calls), call_count)

    async def test_draw_normalizes_native_shape_resources(self) -> None:
        await self.service.draw(
            shape="polygon",
            layer_id="layer-1",
            points=[{"x": 1, "y": 2}, {"x": 8, "y": 2}, {"x": 4, "y": 9}],
            fill_style="ForegroundColor",
            foreground="#aabbcc",
        )
        operation, arguments = self.client.calls[-1]
        self.assertEqual(operation, "draw")
        self.assertEqual(arguments["foreground"], "#AABBCC")
        self.assertEqual(arguments["fill_style"], "ForegroundColor")
        self.assertEqual(len(arguments["points"]), 3)

    async def test_draw_rejects_invisible_shape(self) -> None:
        with self.assertRaisesRegex(InputError, "cannot both"):
            await self.service.draw(
                shape="rectangle",
                x=0,
                y=0,
                width=8,
                height=8,
                stroke_style="None",
                fill_style="None",
            )
        self.assertEqual(self.client.calls, [])

    async def test_project_palette_and_color_adjustment_are_bounded(self) -> None:
        await self.service.color(
            action="set_project_palette",
            project_name="Hero",
            entries=[{"name": "Outline", "color": "#112233"}],
        )
        self.assertEqual(
            self.client.calls[-1],
            (
                "color",
                {
                    "action": "set_project_palette",
                    "project_name": "Hero",
                    "entries": [{"name": "Outline", "color": "#112233"}],
                },
            ),
        )
        await self.service.color(
            action="adjust",
            layer_id="layer-1",
            filter_name="hsvadjustment",
            settings={"h": 15, "s": -10},
        )
        self.assertEqual(self.client.calls[-1][1]["settings"], {"h": 15, "s": -10})

        call_count = len(self.client.calls)
        with self.assertRaisesRegex(InputError, "65536-byte"):
            await self.service.color(
                action="set_project_palette",
                project_name="Hero",
                entries=[
                    {"name": "\U0001f3a8" * 97 + f"{index:03d}", "color": "#112233"}
                    for index in range(256)
                ],
            )
        self.assertEqual(len(self.client.calls), call_count)

    async def test_color_rejects_unknown_filter_before_bridge(self) -> None:
        with self.assertRaisesRegex(InputError, "filter_name"):
            await self.service.color(
                action="adjust", layer_id="layer-1", filter_name="oilpaint"
            )
        self.assertEqual(self.client.calls, [])

    async def test_save_export_rejects_outside_path_before_bridge_call(self) -> None:
        outside = self.root.parent / "outside.png"
        with self.assertRaisesRegex(InputError, "outside"):
            await self.service.save_export(kra_path=None, png_path=str(outside), overwrite=False)
        self.assertEqual(self.client.calls, [])


if __name__ == "__main__":
    unittest.main()
