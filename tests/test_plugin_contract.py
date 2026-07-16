from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "krita-plugin" / "kritacodexbridge"


def plugin_source(*names: str) -> str:
    return "\n".join((PLUGIN_ROOT / name).read_text(encoding="utf-8") for name in names)


class PluginContractTests(unittest.TestCase):
    def test_plugin_python_sources_compile(self) -> None:
        for path in PLUGIN_ROOT.glob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_painting_uses_native_api_and_no_pixel_writer(self) -> None:
        source = plugin_source("painting_service.py")
        self.assertIn("node.paintLine", source)
        self.assertIn("QPoint(round(", source)
        self.assertNotIn("QPointF", source)
        self.assertNotIn("setPixelData", source)

    def test_drawing_uses_native_brush_shapes(self) -> None:
        source = plugin_source("drawing_service.py")
        for call in (
            "node.paintLine(",
            "node.paintPath(",
            "node.paintPolygon(",
            "node.paintRectangle(",
            "node.paintEllipse(",
        ):
            self.assertIn(call, source)
        self.assertIn("QPoint(round(", source)
        self.assertNotIn("setPixelData", source)

    def test_color_surface_uses_selection_fill_and_undoable_filter_strokes(self) -> None:
        source = plugin_source("color_service.py", "color_adjustment_service.py")
        self.assertIn("Krita.instance().resources(\"palette\")", source)
        self.assertIn("doc.setAnnotation(", source)
        self.assertIn("_PROJECT_PALETTE_MAX_BYTES = 65_536", source)
        self.assertIn("len(raw) > self._PROJECT_PALETTE_MAX_BYTES", source)
        self.assertIn("len(encoded) > self._PROJECT_PALETTE_MAX_BYTES", source)
        self.assertIn("replacement_selection.setPixelData", source)
        self.assertIn('"fill_selection_foreground_color"', source)
        self.assertIn("filter_object.startFilter(", source)
        self.assertNotIn("filter_object.apply(", source)
        self.assertNotIn("node.setPixelData", source)

    def test_no_arbitrary_python_execution_surface(self) -> None:
        for path in PLUGIN_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            called_names = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            self.assertFalse({"eval", "exec", "compile"} & called_names, path.name)

    def test_http_server_binds_to_validated_config_host(self) -> None:
        source = (PLUGIN_ROOT / "http_server.py").read_text(encoding="utf-8")
        self.assertIn("(config.host, config.port)", source)
        self.assertNotIn("0.0.0.0", source)

    def test_auth_rejection_drains_bounded_body_without_dispatch(self) -> None:
        source = (PLUGIN_ROOT / "http_server.py").read_text(encoding="utf-8")
        self.assertIn("self._discard_announced_body()", source)
        self.assertIn("length <= self.server.config.max_request_bytes", source)

    def test_qt_dispatch_is_queued_and_timeout_aware(self) -> None:
        source = (PLUGIN_ROOT / "http_bridge.py").read_text(encoding="utf-8")
        self.assertIn("Qt.QueuedConnection", source)
        self.assertIn("envelope.done.wait(timeout_seconds)", source)
        self.assertNotIn("BlockingQueuedConnection", source)

    def test_plugin_has_persistent_log_and_status_action(self) -> None:
        extension = (PLUGIN_ROOT / "extension.py").read_text(encoding="utf-8")
        logging_source = (PLUGIN_ROOT / "logging_utils.py").read_text(encoding="utf-8")
        self.assertIn("krita_codex_bridge_status", extension)
        self.assertIn("RotatingFileHandler", logging_source)
        self.assertNotIn("config.token", extension)

    def test_expanded_surface_uses_native_krita_apis(self) -> None:
        source = plugin_source(
            "document_service.py",
            "layer_service.py",
            "painting_service.py",
            "service_context.py",
        )
        self.assertIn("app.createDocument(", source)
        self.assertIn("app.openDocument(", source)
        self.assertIn('"undo": "edit_undo"', source)
        self.assertIn('"redo": "edit_redo"', source)
        self.assertIn("root = doc.rootNode()", source)
        self.assertIn("return self._node_id(root)", source)
        self.assertIn("view.setCurrentBrushPreset", source)
        self.assertIn("ManagedColor.fromQColor", source)

    def test_canvas_and_selection_surface_uses_bounded_native_apis(self) -> None:
        canvas = plugin_source("canvas_service.py")
        editing = plugin_source("editing_service.py")
        self.assertIn("doc.resizeImage(", canvas)
        self.assertIn("doc.scaleImage(", canvas)
        self.assertIn("doc.crop(", canvas)
        self.assertIn("doc.rotateImage(", canvas)
        self.assertIn("doc.projection(", canvas)
        self.assertNotIn("doc.thumbnail(", canvas)
        self.assertIn(
            "int(doc.width()) * int(doc.height()) > self.config.document_max_pixels",
            canvas,
        )
        self.assertIn("Qt.SmoothTransformation", canvas)
        self.assertIn('"mirrorImageHorizontal"', canvas)
        self.assertIn('"mirrorImageVertical"', canvas)
        self.assertNotIn("mirror_canvas", canvas)
        self.assertIn("Selection(doc)", editing)
        self.assertIn("doc.setSelection(", editing)
        self.assertIn('"fill_selection_foreground_color"', editing)
        self.assertIn('else "clear"', editing)
        self.assertIn('"edit_paste"', editing)
        self.assertIn('clipboard_modified=action in {"copy", "cut"}', editing)
        self.assertIn("node.setPixelData", canvas)

    def test_layer_asset_workflow_uses_native_node_operations(self) -> None:
        source = plugin_source("layer_service.py", "layer_transform_service.py")
        self.assertIn("duplicate = node.duplicate()", source)
        self.assertIn("parent.addChildNode(duplicate, node)", source)
        self.assertIn("if not node.remove()", source)
        self.assertIn("not source_parent.removeChildNode(node)", source)
        self.assertIn("if not parent.addChildNode(node, above)", source)
        self.assertIn("source_parent.addChildNode(node, source_anchor)", source)
        self.assertIn("node.move(int(position.x()) + offset_x", source)
        self.assertIn("node.setAlphaLocked", source)
        self.assertIn("node.setBlendingMode", source)
        self.assertIn("node.mergeDown()", source)
        self.assertIn("node.scaleNode", source)
        self.assertIn("node.rotateNode", source)
        self.assertIn("node.shearNode", source)
        self.assertIn("node.cropNode", source)
        self.assertIn("width * height > self.config.document_max_pixels", source)
        self.assertIn('"mirrorNodeX"', source)
        self.assertIn('"mirrorNodeY"', source)
        self.assertIn(
            "doc.setActiveNode(node)\n"
            '            self._trigger_action(action_id, doc, scope="layer")',
            source,
        )
        self.assertIn("backup = node.duplicate()", source)
        self.assertIn('backup.setName(f"{node.name()} [before {action}]")', source)
        self.assertIn('"backup_layer_id": self._node_id(backup)', source)
        self.assertIn('"UNSAFE_TRANSFORM_SEQUENCE"', source)
        self.assertIn("self._destructive_transform_documents.get(document_id)", source)
        self.assertIn("doc.createTransformMask", source)
        self.assertIn("mask.toXML()", source)
        self.assertIn("mask.fromXML(configured_xml)", source)
        self.assertIn("mask.finalAffineTransform()", source)
        self.assertIn('"transform_mode": "mask"', source)
        self.assertIn("_MAX_TRANSFORM_MASKS = 3", source)
        self.assertIn('"UNSAFE_TRANSFORM_MASK_SEQUENCE"', source)
        self.assertIn("self._transform_mask_creation_counts.get(document_id, 0)", source)
        self.assertIn('"UNSAFE_TRANSFORM_MASK_MUTATION"', source)
        self.assertIn('"UNSAFE_TRANSFORM_BACKEND_MIX"', source)
        self.assertIn("self._ensure_layer_transform_backend", source)
        self.assertIn("self._created_transform_mask_ids.get", source)
        self.assertIn('"metadata_dropped"] = True', source)

    def test_file_export_temporarily_suppresses_krita_dialogs(self) -> None:
        source = plugin_source("document_service.py")
        self.assertIn("doc.setBatchmode(True)", source)
        self.assertIn("doc.setBatchmode(previous_batchmode)", source)

    def test_asset_export_uses_isolated_native_projection_and_atomic_output(self) -> None:
        source = plugin_source("asset_service.py")
        self.assertIn("clone = doc.clone()", source)
        self.assertIn("image = clone.projection(", source)
        self.assertIn("preview_png = self._encode_png_preview(preview_image)", source)
        self.assertIn("temp_path.replace(png_path)", source)
        self.assertIn("os.link(temp_path, png_path)", source)
        self.assertIn("self.config.asset_max_bytes", source)
        preview_index = source.index("preview_png = self._encode_png_preview(preview_image)")
        replace_index = source.index("temp_path.replace(png_path)")
        link_index = source.index("os.link(temp_path, png_path)")
        self.assertLess(preview_index, replace_index)
        self.assertLess(preview_index, link_index)

    def test_animation_package_has_rendered_union_unity_manifest_and_safe_commit(self) -> None:
        source = plugin_source(
            "animation_service.py", "asset_service.py", "package_service.py"
        )
        animation_start = source.index("def _export_animation_asset")
        preview_index = source.index(
            "preview_png = self._encode_png_preview(preview_image)", animation_start
        )
        commit_index = source.index("self._commit_asset_directory(", animation_start)
        self.assertLess(preview_index, commit_index)
        self.assertIn("self._rendered_union_rect(doc, frames, canvas_rect)", source)
        self.assertIn("QImage.Format_RGBA8888", source)
        self.assertIn('"dev.desens.krita-codex.unity-animation"', source)
        self.assertIn('"sheet_rect_png_top_left"', source)
        self.assertIn('"sprite_rect_unity_bottom_left"', source)
        self.assertIn("Qt.FastTransformation", source)
        self.assertIn("os.replace(source, destination)", source)
        self.assertIn("UNMANAGED_OUTPUT_DIRECTORY", source)
        sheet_stage_index = source.index('staged_paths = [stage_dir / "spritesheet.png"]')
        frame_stage_index = source.index(
            "staged_paths.extend(sorted((stage_dir / item_directory)", sheet_stage_index
        )
        manifest_stage_index = source.index(
            'staged_paths.append(stage_dir / "manifest.json")', frame_stage_index
        )
        self.assertLess(sheet_stage_index, frame_stage_index)
        self.assertLess(frame_stage_index, manifest_stage_index)

    def test_region_package_has_grid_explicit_unity_rects_and_fast_preview(self) -> None:
        source = plugin_source("region_service.py", "package_service.py")
        self.assertIn('REGION_PACKAGE_SCHEMA = "dev.desens.krita-codex.unity-regions"', source)
        self.assertIn('if action == "export_tileset":', source)
        self.assertIn("self._explicit_region_specs", source)
        self.assertIn('"sheet_rect_png_top_left"', source)
        self.assertIn('"sprite_rect_unity_bottom_left"', source)
        self.assertIn("canvas_height - spec[\"y\"] - spec[\"height\"]", source)
        self.assertIn("Qt.FastTransformation", source)
        self.assertIn("self._commit_asset_directory(", source)
        self.assertIn('item_directory="regions"', source)
        self.assertIn("os.replace(source, destination)", source)

    def test_document_targeting_and_kra_checkpoint_use_native_api(self) -> None:
        source = plugin_source("document_service.py", "service_context.py")
        self.assertIn("if activate:", source)
        self.assertIn("self._activate_document_view(doc)", source)
        self.assertIn("self._find_document(app, document_id)", source)
        self.assertIn("window.activate()", source)
        self.assertIn("window.showView(view)", source)
        self.assertIn("if not doc.save():", source)
        self.assertIn("active is not None and id(active) in live_keys", source)
        self.assertIn("doc.waitForDone()", source)
        self.assertIn('"UNSAVED_DOCUMENT"', source)
        self.assertIn("self._transform_mask_creation_counts.pop(document_id, None)", source)
        self.assertIn("self._created_transform_mask_ids.pop(document_id, None)", source)

    def test_service_entrypoint_stays_small_and_operations_are_split(self) -> None:
        service_lines = (PLUGIN_ROOT / "service.py").read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(service_lines), 100)
        for name in (
            "animation_service.py",
            "asset_service.py",
            "canvas_service.py",
            "color_adjustment_service.py",
            "color_service.py",
            "document_service.py",
            "drawing_service.py",
            "editing_service.py",
            "layer_service.py",
            "layer_transform_service.py",
            "painting_service.py",
            "package_service.py",
            "region_service.py",
            "service_context.py",
        ):
            path = PLUGIN_ROOT / name
            self.assertTrue(path.is_file(), name)
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 550, name)

        context = plugin_source("service_context.py")
        self.assertIn("def _trigger_action(", context)
        self.assertEqual(
            sum(
                plugin_source(path.name).count("Krita.instance().action(action_id)")
                for path in PLUGIN_ROOT.glob("*.py")
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
