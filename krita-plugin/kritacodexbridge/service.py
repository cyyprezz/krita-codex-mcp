from __future__ import annotations

import base64
import math
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import uuid4

from krita import InfoObject, Krita, ManagedColor
from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, QPoint
from PyQt5.QtGui import QColor

from krita_codex_protocol import (
    KRITA_DOCUMENT_EXTENSIONS,
    BridgeConfig,
    PathPolicy,
    PathPolicyError,
)

from .errors import KritaOperationError


class KritaService:
    """All methods are called on Krita's Qt main thread by MainThreadDispatcher."""

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.path_policy = PathPolicy(config.allowed_roots)
        self.bridge_revision = 0
        self._document_ids: dict[int, str] = {}

    def execute(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        handlers = {
            "state": self.state,
            "preview": self.preview,
            "document": self.document,
            "layers": self.layers,
            "history": self.history,
            "brush": self.brush,
            "paint_path": self.paint_path,
            "save_export": self.save_export,
        }
        handler = handlers.get(operation)
        if handler is None:
            raise KritaOperationError(
                "UNSUPPORTED_OPERATION", f"Unsupported operation: {operation}"
            )
        result = handler(arguments)
        result["bridge_revision"] = self.bridge_revision
        return result

    def state(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        app = Krita.instance()
        doc = self._active_document()
        active = doc.activeNode()
        view = self._optional_view()
        return {
            "krita_version": _safe_call(app, "version"),
            "document": {
                "name": doc.name(),
                "file_name": doc.fileName(),
                "modified": bool(doc.modified()),
                "width": int(doc.width()),
                "height": int(doc.height()),
                "resolution": int(doc.resolution()),
                "color_model": doc.colorModel(),
                "color_depth": doc.colorDepth(),
                "color_profile": doc.colorProfile(),
            },
            "active_layer_id": self._node_id(active) if active else None,
            "layer_stack_order": "bottom_to_top",
            "layers": self._layer_forest(doc),
            "painting": self._painting_state(view),
            "capabilities": {
                "native_paint_line": bool(active and hasattr(active, "paintLine")),
                "native_paint_path": bool(active and hasattr(active, "paintPath")),
            },
        }

    def preview(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        doc = self._active_document()
        max_width = _bounded_int(
            arguments.get("max_width"), "max_width", 64, self.config.preview_max_dimension
        )
        max_height = _bounded_int(
            arguments.get("max_height"), "max_height", 64, self.config.preview_max_dimension
        )
        width, height = _fit_size(doc.width(), doc.height(), max_width, max_height)
        image = doc.thumbnail(width, height)
        if image is None or image.isNull():
            raise KritaOperationError(
                "PREVIEW_FAILED", "Krita returned an empty document thumbnail"
            )
        payload = QByteArray()
        buffer = QBuffer(payload)
        if not buffer.open(QIODevice.WriteOnly):
            raise KritaOperationError("PREVIEW_FAILED", "Could not allocate the PNG buffer")
        try:
            if not image.save(buffer, "PNG"):
                raise KritaOperationError(
                    "PREVIEW_FAILED", "Krita could not encode the preview as PNG"
                )
        finally:
            buffer.close()
        png = bytes(payload)
        if len(png) > self.config.preview_max_bytes:
            raise KritaOperationError(
                "PREVIEW_TOO_LARGE",
                "Encoded preview exceeds the configured byte limit",
                {"bytes": len(png), "limit": self.config.preview_max_bytes},
            )
        active = doc.activeNode()
        return {
            "mime_type": "image/png",
            "width": int(image.width()),
            "height": int(image.height()),
            "source_width": int(doc.width()),
            "source_height": int(doc.height()),
            "document_name": doc.name(),
            "active_layer_id": self._node_id(active) if active else None,
            "png_base64": base64.b64encode(png).decode("ascii"),
        }

    def document(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        app = Krita.instance()
        action = arguments.get("action")
        if action not in {"list", "create", "open", "activate", "close"}:
            raise KritaOperationError("INVALID_ARGUMENT", f"Unsupported document action: {action}")
        if action == "list":
            return self._document_result(app, action)
        if action == "create":
            width = _bounded_int(
                arguments.get("width"), "width", 1, self.config.document_max_dimension
            )
            height = _bounded_int(
                arguments.get("height"), "height", 1, self.config.document_max_dimension
            )
            if width * height > self.config.document_max_pixels:
                raise KritaOperationError(
                    "DOCUMENT_TOO_LARGE",
                    "Requested document exceeds the configured pixel limit",
                    {
                        "pixels": width * height,
                        "limit": self.config.document_max_pixels,
                    },
                )
            name = _required_text(arguments.get("name"), "name", 200)
            color_model = _required_text(arguments.get("color_model"), "color_model", 32)
            color_depth = _required_text(arguments.get("color_depth"), "color_depth", 32)
            profile = _optional_text(arguments.get("profile", ""), "profile", 300)
            resolution = _bounded_float(arguments.get("resolution"), "resolution", 1.0, 2400.0)
            models = [str(value) for value in app.colorModels()]
            if color_model not in models:
                raise KritaOperationError(
                    "UNSUPPORTED_COLOR_MODEL",
                    "Krita does not provide the requested color model",
                    {"requested": color_model, "available": models},
                )
            depths = [str(value) for value in app.colorDepths(color_model)]
            if color_depth not in depths:
                raise KritaOperationError(
                    "UNSUPPORTED_COLOR_DEPTH",
                    "Krita does not provide the requested depth for this color model",
                    {"requested": color_depth, "available": depths},
                )
            profiles = [str(value) for value in app.profiles(color_model, color_depth)]
            if profile and profile not in profiles:
                raise KritaOperationError(
                    "UNSUPPORTED_COLOR_PROFILE",
                    "Krita does not provide the requested profile for this color space",
                    {"requested": profile, "available": profiles},
                )
            try:
                doc = app.createDocument(
                    width,
                    height,
                    name,
                    color_model,
                    color_depth,
                    profile,
                    resolution,
                )
            except Exception as exc:
                raise KritaOperationError(
                    "DOCUMENT_CREATE_FAILED",
                    "Krita could not create the document",
                    {"reason": str(exc)},
                ) from exc
            if doc is None:
                raise KritaOperationError(
                    "DOCUMENT_CREATE_FAILED", "Krita could not create the document"
                )
            self._show_document(app, doc)
            self.bridge_revision += 1
            return self._document_result(app, action, affected=doc)
        if action == "open":
            try:
                path = self.path_policy.validate_input(
                    arguments.get("path"), extensions=set(KRITA_DOCUMENT_EXTENSIONS)
                )
            except PathPolicyError as exc:
                raise KritaOperationError("PATH_NOT_ALLOWED", str(exc)) from exc
            previous_batchmode = bool(app.batchmode())
            app.setBatchmode(True)
            try:
                try:
                    doc = app.openDocument(str(path))
                except Exception as exc:
                    raise KritaOperationError(
                        "DOCUMENT_OPEN_FAILED",
                        "Krita could not open the document",
                        {"path": str(path), "reason": str(exc)},
                    ) from exc
            finally:
                app.setBatchmode(previous_batchmode)
            if doc is None:
                raise KritaOperationError(
                    "DOCUMENT_OPEN_FAILED",
                    "Krita could not open the document",
                    {"path": str(path)},
                )
            self._show_document(app, doc)
            doc.waitForDone()
            self.bridge_revision += 1
            return self._document_result(app, action, affected=doc)

        document_id = _required_text(arguments.get("document_id"), "document_id", 80)
        doc = self._find_document(app, document_id)
        if doc is None:
            raise KritaOperationError("DOCUMENT_NOT_FOUND", "Document ID was not found")
        if action == "activate":
            app.setActiveDocument(doc)
            self.bridge_revision += 1
            return self._document_result(app, action, affected=doc)
        if action == "close":
            discard_changes = arguments.get("discard_changes", False)
            if not isinstance(discard_changes, bool):
                raise KritaOperationError("INVALID_ARGUMENT", "discard_changes must be a boolean")
            was_modified = bool(doc.modified())
            if was_modified and not discard_changes:
                raise KritaOperationError(
                    "UNSAVED_CHANGES",
                    "Document has unsaved changes; save it or pass discard_changes=true",
                    {"document_id": document_id},
                )
            if was_modified:
                doc.setModified(False)
            if not doc.close():
                if was_modified:
                    doc.setModified(True)
                raise KritaOperationError(
                    "DOCUMENT_CLOSE_FAILED", "Krita could not close the document"
                )
            self._document_ids.pop(id(doc), None)
            self.bridge_revision += 1
            result = self._document_result(app, action)
            result["closed_document_id"] = document_id
            return result

    def layers(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        doc = self._active_document()
        action = arguments.get("action")
        if action == "read":
            return self._layer_result(doc, action)
        if action == "create":
            name = _required_text(arguments.get("name"), "name", 200)
            kind = arguments.get("kind")
            if kind not in {"paintlayer", "grouplayer"}:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "kind must be paintlayer or grouplayer"
                )
            parent_id = arguments.get("parent_id")
            parent = self._find_node(doc, parent_id) if parent_id else doc.rootNode()
            if parent is None:
                raise KritaOperationError("LAYER_NOT_FOUND", "Parent layer UUID was not found")
            parent_is_root = self._same_node(parent, doc.rootNode())
            if not parent_is_root and parent.type().lower() != "grouplayer":
                raise KritaOperationError(
                    "INVALID_PARENT", "New layers require the root or a group layer"
                )
            node = (
                doc.createGroupLayer(name)
                if kind == "grouplayer"
                else doc.createNode(name, "paintlayer")
            )
            if node is None or not parent.addChildNode(node, None):
                raise KritaOperationError("LAYER_CREATE_FAILED", "Krita could not create the layer")
            doc.setActiveNode(node)
            self._finish_mutation(doc)
            return self._layer_result(doc, action, affected=node)

        layer_id = _required_text(arguments.get("layer_id"), "layer_id", 80)
        node = self._find_node(doc, layer_id)
        if node is None or self._same_node(node, doc.rootNode()):
            raise KritaOperationError("LAYER_NOT_FOUND", "Layer UUID was not found")
        if action == "activate":
            doc.setActiveNode(node)
        elif action == "rename":
            node.setName(_required_text(arguments.get("name"), "name", 200))
        elif action == "set_properties":
            visible_value = None
            opacity_value = None
            if "visible" in arguments:
                visible_value = arguments["visible"]
                if not isinstance(visible_value, bool):
                    raise KritaOperationError("INVALID_ARGUMENT", "visible must be a boolean")
            if "opacity" in arguments:
                opacity_value = _bounded_float(arguments["opacity"], "opacity", 0.0, 1.0)
            if visible_value is None and opacity_value is None:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "set_properties requires visible and/or opacity"
                )
            if visible_value is not None:
                node.setVisible(visible_value)
            if opacity_value is not None:
                node.setOpacity(round(opacity_value * 255))
        else:
            raise KritaOperationError("INVALID_ARGUMENT", f"Unsupported layer action: {action}")
        self._finish_mutation(doc)
        return self._layer_result(doc, str(action), affected=node)

    def history(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        doc = self._active_document()
        action = arguments.get("action")
        if action == "status":
            return self._history_result(doc, action, requested=0, completed=0)
        if action not in {"undo", "redo"}:
            raise KritaOperationError("INVALID_ARGUMENT", f"Unsupported history action: {action}")
        steps = _bounded_int(arguments.get("steps"), "steps", 1, 100)
        action_id = {"undo": "edit_undo", "redo": "edit_redo"}[action]
        qt_action = Krita.instance().action(action_id)
        if qt_action is None:
            raise KritaOperationError(
                "UNSUPPORTED_FEATURE", f"Krita does not expose the {action} action"
            )
        completed = 0
        for _ in range(steps):
            if not qt_action.isEnabled():
                break
            qt_action.trigger()
            doc.waitForDone()
            completed += 1
        if completed:
            doc.refreshProjection()
            self.bridge_revision += 1
        return self._history_result(doc, action, requested=steps, completed=completed)

    def brush(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        view = self._active_view()
        action = arguments.get("action")
        if action == "read":
            return {"action": action, "painting": self._painting_state(view)}
        if action == "list_presets":
            query = _optional_text(arguments.get("query", ""), "query", 200).casefold()
            limit = _bounded_int(arguments.get("limit", 100), "limit", 1, 500)
            names = {
                resource.name()
                for resource in Krita.instance().resources("preset").values()
                if not query or query in resource.name().casefold()
            }
            ordered = sorted(names, key=str.casefold)
            return {
                "action": action,
                "query": query,
                "presets": ordered[:limit],
                "total": len(ordered),
                "truncated": len(ordered) > limit,
            }
        if action != "set":
            raise KritaOperationError("INVALID_ARGUMENT", f"Unsupported brush action: {action}")

        fields = {
            "preset_name",
            "brush_size",
            "opacity",
            "flow",
            "rotation",
            "eraser_mode",
            "disable_pressure",
            "foreground",
        }
        if not fields.intersection(arguments):
            raise KritaOperationError(
                "INVALID_ARGUMENT", "set requires at least one brush property"
            )
        preset = None
        if "preset_name" in arguments:
            preset_name = _required_text(arguments["preset_name"], "preset_name", 300)
            preset = self._find_preset(preset_name)
            if preset is None:
                raise KritaOperationError(
                    "PRESET_NOT_FOUND",
                    "Krita brush preset was not found",
                    {"name": preset_name},
                )
        brush_size = (
            _bounded_float(arguments["brush_size"], "brush_size", 0.1, 1000.0)
            if "brush_size" in arguments
            else None
        )
        opacity = (
            _bounded_float(arguments["opacity"], "opacity", 0.0, 1.0)
            if "opacity" in arguments
            else None
        )
        flow = _bounded_float(arguments["flow"], "flow", 0.0, 1.0) if "flow" in arguments else None
        rotation = (
            _bounded_float(arguments["rotation"], "rotation", -3600.0, 3600.0) % 360.0
            if "rotation" in arguments
            else None
        )
        eraser_mode = _optional_bool(arguments, "eraser_mode")
        disable_pressure = _optional_bool(arguments, "disable_pressure")
        foreground = _parse_qcolor(arguments["foreground"]) if "foreground" in arguments else None
        managed_foreground = (
            ManagedColor.fromQColor(foreground, view.canvas()) if foreground is not None else None
        )
        if foreground is not None and managed_foreground is None:
            raise KritaOperationError(
                "COLOR_CONVERSION_FAILED", "Krita could not convert the foreground color"
            )

        previous = {
            "preset": view.currentBrushPreset(),
            "brush_size": float(view.brushSize()),
            "opacity": float(view.paintingOpacity()),
            "flow": float(view.paintingFlow()),
            "rotation": float(view.brushRotation()),
            "eraser_mode": bool(view.eraserMode()),
            "disable_pressure": bool(view.disablePressure()),
            "foreground": view.foregroundColor(),
        }
        try:
            if preset is not None:
                view.setCurrentBrushPreset(preset)
            if brush_size is not None:
                view.setBrushSize(brush_size)
            if opacity is not None:
                view.setPaintingOpacity(opacity)
            if flow is not None:
                view.setPaintingFlow(flow)
            if rotation is not None:
                view.setBrushRotation(rotation)
            if eraser_mode is not None:
                view.setEraserMode(eraser_mode)
            if disable_pressure is not None:
                view.setDisablePressure(disable_pressure)
            if managed_foreground is not None:
                view.setForeGroundColor(managed_foreground)
        except Exception as exc:
            self._restore_brush(view, previous)
            raise KritaOperationError(
                "BRUSH_SET_FAILED",
                "Krita could not apply the requested brush settings",
                {"reason": str(exc)},
            ) from exc
        self.bridge_revision += 1
        return {"action": action, "painting": self._painting_state(view)}

    def paint_path(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        doc = self._active_document()
        view = self._active_view()
        raw_points = arguments.get("points")
        if not isinstance(raw_points, list) or not 2 <= len(raw_points) <= 512:
            raise KritaOperationError(
                "INVALID_ARGUMENT", "points must contain between 2 and 512 entries"
            )
        points: list[tuple[float, float, float]] = []
        for index, raw in enumerate(raw_points):
            if not isinstance(raw, dict):
                raise KritaOperationError("INVALID_ARGUMENT", f"points[{index}] must be an object")
            x = _bounded_float(raw.get("x"), f"points[{index}].x", 0.0, float(doc.width()))
            y = _bounded_float(raw.get("y"), f"points[{index}].y", 0.0, float(doc.height()))
            pressure = _bounded_float(
                raw.get("pressure", 1.0), f"points[{index}].pressure", 0.0, 1.0
            )
            points.append((x, y, pressure))

        layer_id = arguments.get("layer_id")
        node = self._find_node(doc, layer_id) if layer_id else doc.activeNode()
        if node is None:
            raise KritaOperationError("LAYER_NOT_FOUND", "No target paint layer is active")
        if node.type().lower() != "paintlayer":
            raise KritaOperationError("LAYER_NOT_PAINTABLE", "Target layer is not a paint layer")
        if bool(_safe_call(node, "locked", default=False)):
            raise KritaOperationError("LAYER_LOCKED", "Target paint layer is locked")
        if not hasattr(node, "paintLine"):
            raise KritaOperationError(
                "UNSUPPORTED_FEATURE",
                "This Krita build does not expose Node.paintLine to Python",
                {"krita_version": _safe_call(Krita.instance(), "version")},
            )
        paint_ability = _safe_call(node, "paintAbility")
        if paint_ability not in {None, "PAINT"}:
            raise KritaOperationError(
                "LAYER_NOT_PAINTABLE",
                "The current preset cannot paint on this layer",
                {"paint_ability": paint_ability},
            )

        preset_to_apply = None
        requested_preset_name = arguments.get("preset_name")
        if requested_preset_name is not None:
            requested_preset_name = _required_text(requested_preset_name, "preset_name", 300)
            preset_to_apply = self._find_preset(requested_preset_name)
            if preset_to_apply is None:
                raise KritaOperationError(
                    "PRESET_NOT_FOUND",
                    "Krita brush preset was not found",
                    {"name": requested_preset_name},
                )
        requested_brush_size = (
            _bounded_float(arguments["brush_size"], "brush_size", 0.1, 1000.0)
            if "brush_size" in arguments
            else None
        )

        previous_active_node = doc.activeNode()
        doc.setActiveNode(node)
        completed = 0
        previous_preset = view.currentBrushPreset()
        previous_brush_size = float(view.brushSize())
        try:
            if preset_to_apply is not None:
                view.setCurrentBrushPreset(preset_to_apply)
            if requested_brush_size is not None:
                view.setBrushSize(requested_brush_size)
            for start, end in zip(points, points[1:], strict=False):
                node.paintLine(
                    QPoint(round(start[0]), round(start[1])),
                    QPoint(round(end[0]), round(end[1])),
                    start[2],
                    end[2],
                )
                completed += 1
            doc.waitForDone()
            doc.refreshProjection()
        except Exception as exc:
            rolled_back = self._undo_segments(completed, doc)
            try:
                if preset_to_apply is not None and previous_preset is not None:
                    view.setCurrentBrushPreset(previous_preset)
                if requested_brush_size is not None:
                    view.setBrushSize(previous_brush_size)
                if previous_active_node is not None:
                    doc.setActiveNode(previous_active_node)
            except Exception:
                pass
            if rolled_back != completed:
                self.bridge_revision += 1
            raise KritaOperationError(
                "PAINT_FAILED",
                "Krita failed while painting the native brush path",
                {
                    "completed_segments": completed,
                    "rollback_steps": rolled_back,
                    "reason": str(exc),
                },
            ) from exc

        self.bridge_revision += 1
        preset = view.currentBrushPreset()
        return {
            "layer_id": self._node_id(node),
            "segments": completed,
            "undo_steps": completed,
            "undo_behavior": "one Krita undo step per line segment",
            "preset": preset.name() if preset else None,
            "brush_size": float(view.brushSize()),
            "native_brush_engine": True,
        }

    def save_export(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        doc = self._active_document()
        overwrite = arguments.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise KritaOperationError("INVALID_ARGUMENT", "overwrite must be a boolean")
        kra_raw = arguments.get("kra_path")
        png_raw = arguments.get("png_path")
        if kra_raw is None and png_raw is None:
            raise KritaOperationError("INVALID_ARGUMENT", "Provide kra_path and/or png_path")
        try:
            kra_path = (
                self.path_policy.validate_output(kra_raw, extensions={".kra"}, overwrite=overwrite)
                if kra_raw is not None
                else None
            )
            png_path = (
                self.path_policy.validate_output(png_raw, extensions={".png"}, overwrite=overwrite)
                if png_raw is not None
                else None
            )
        except PathPolicyError as exc:
            raise KritaOperationError("PATH_NOT_ALLOWED", str(exc)) from exc

        completed: list[dict[str, str]] = []
        previous_batchmode = bool(doc.batchmode())
        doc.setBatchmode(True)
        try:
            if kra_path is not None:
                if not doc.saveAs(str(kra_path)):
                    raise KritaOperationError(
                        "SAVE_FAILED",
                        "Krita could not save the KRA file",
                        {"path": str(kra_path)},
                    )
                doc.waitForDone()
                completed.append({"kind": "kra", "path": str(kra_path)})
            if png_path is not None:
                options = InfoObject()
                options.setProperty("alpha", True)
                options.setProperty("compression", 6)
                options.setProperty("forceSRGB", False)
                options.setProperty("indexed", False)
                options.setProperty("interlaced", False)
                options.setProperty("saveSRGBProfile", True)
                if not doc.exportImage(str(png_path), options):
                    raise KritaOperationError(
                        "EXPORT_FAILED",
                        "Krita could not export the PNG file",
                        {"path": str(png_path), "completed": completed},
                    )
                doc.waitForDone()
                completed.append({"kind": "png", "path": str(png_path)})
        finally:
            doc.setBatchmode(previous_batchmode)
        self.bridge_revision += 1
        return {"outputs": completed, "document_file_name": doc.fileName()}

    def _active_document(self):
        doc = Krita.instance().activeDocument()
        if doc is None:
            raise KritaOperationError("NO_ACTIVE_DOCUMENT", "Krita has no active document")
        return doc

    def _active_view(self):
        view = self._optional_view()
        if view is None:
            raise KritaOperationError("NO_ACTIVE_VIEW", "Krita has no active canvas view")
        return view

    def _optional_view(self):
        window = Krita.instance().activeWindow()
        return window.activeView() if window else None

    def _show_document(self, app, doc) -> None:
        window = app.activeWindow()
        if window is None:
            doc.setModified(False)
            doc.close()
            raise KritaOperationError(
                "NO_ACTIVE_WINDOW", "Krita has no window in which to show the document"
            )
        try:
            window.addView(doc)
            app.setActiveDocument(doc)
        except Exception as exc:
            doc.setModified(False)
            doc.close()
            raise KritaOperationError(
                "DOCUMENT_VIEW_FAILED",
                "Krita created the document but could not show it in a view",
                {"reason": str(exc)},
            ) from exc

    def _document_id(self, doc) -> str:
        try:
            root = doc.rootNode()
            if root is not None:
                return self._node_id(root)
        except Exception:
            pass
        key = id(doc)
        value = self._document_ids.get(key)
        if value is None:
            value = str(uuid4())
            self._document_ids[key] = value
        return value

    def _find_document(self, app, document_id: str):
        expected = document_id.strip().lower()
        for doc in app.documents():
            if self._document_id(doc) == expected:
                return doc
        return None

    def _document_result(self, app, action: str, affected=None) -> dict[str, Any]:
        documents = list(app.documents())
        live_keys = {id(doc) for doc in documents}
        self._document_ids = {
            key: value for key, value in self._document_ids.items() if key in live_keys
        }
        active = app.activeDocument()
        active_id = self._document_id(active) if active is not None else None
        result = {
            "action": action,
            "active_document_id": active_id,
            "documents": [
                {
                    "document_id": self._document_id(doc),
                    "name": doc.name(),
                    "file_name": doc.fileName(),
                    "modified": bool(doc.modified()),
                    "width": int(doc.width()),
                    "height": int(doc.height()),
                    "color_model": doc.colorModel(),
                    "color_depth": doc.colorDepth(),
                    "active": self._document_id(doc) == active_id,
                }
                for doc in documents
            ],
        }
        if affected is not None:
            result["affected_document_id"] = self._document_id(affected)
        if action == "list":
            result["create_options"] = {
                str(model): [str(depth) for depth in app.colorDepths(str(model))]
                for model in app.colorModels()
            }
        return result

    def _history_result(
        self, doc, action: str, *, requested: int, completed: int
    ) -> dict[str, Any]:
        app = Krita.instance()
        undo = app.action("edit_undo")
        redo = app.action("edit_redo")
        return {
            "action": action,
            "requested_steps": requested,
            "completed_steps": completed,
            "can_undo": bool(undo and undo.isEnabled()),
            "can_redo": bool(redo and redo.isEnabled()),
            "undo_text": undo.text() if undo else None,
            "redo_text": redo.text() if redo else None,
            "document_modified": bool(doc.modified()),
        }

    def _restore_brush(self, view, previous: Mapping[str, Any]) -> None:
        try:
            if previous["preset"] is not None:
                view.setCurrentBrushPreset(previous["preset"])
            view.setBrushSize(previous["brush_size"])
            view.setPaintingOpacity(previous["opacity"])
            view.setPaintingFlow(previous["flow"])
            view.setBrushRotation(previous["rotation"])
            view.setEraserMode(previous["eraser_mode"])
            view.setDisablePressure(previous["disable_pressure"])
            if previous["foreground"] is not None:
                view.setForeGroundColor(previous["foreground"])
        except Exception:
            pass

    def _node_id(self, node) -> str:
        return str(node.uniqueId().toString()).strip("{}").lower()

    def _find_node(self, doc, layer_id: Any):
        if not isinstance(layer_id, str):
            return None
        expected = layer_id.strip().strip("{}").lower()
        for node in self._walk_nodes(doc.rootNode()):
            if self._node_id(node) == expected:
                return node
        return None

    def _same_node(self, left, right) -> bool:
        return (
            left is not None and right is not None and self._node_id(left) == self._node_id(right)
        )

    def _walk_nodes(self, root) -> Iterable[Any]:
        yield root
        for child in root.childNodes():
            yield from self._walk_nodes(child)

    def _layer_forest(self, doc) -> list[dict[str, Any]]:
        count = [0]
        return [
            self._serialize_node(child, depth=0, count=count)
            for child in doc.rootNode().childNodes()
        ]

    def _serialize_node(self, node, *, depth: int, count: list[int]) -> dict[str, Any]:
        if depth > 64:
            raise KritaOperationError("LAYER_TREE_TOO_DEEP", "Layer tree exceeds 64 levels")
        count[0] += 1
        if count[0] > 2000:
            raise KritaOperationError("LAYER_TREE_TOO_LARGE", "Layer tree exceeds 2000 nodes")
        opacity_raw = int(node.opacity())
        return {
            "id": self._node_id(node),
            "name": node.name(),
            "type": node.type(),
            "visible": bool(node.visible()),
            "opacity": round(opacity_raw / 255.0, 4),
            "opacity_raw": opacity_raw,
            "locked": bool(_safe_call(node, "locked", default=False)),
            "blending_mode": _safe_call(node, "blendingMode"),
            "inherit_alpha": bool(_safe_call(node, "inheritAlpha", default=False)),
            "children": [
                self._serialize_node(child, depth=depth + 1, count=count)
                for child in node.childNodes()
            ],
        }

    def _painting_state(self, view) -> dict[str, Any] | None:
        if view is None:
            return None
        preset = _safe_call(view, "currentBrushPreset")
        foreground = _safe_call(view, "foregroundColor") or _safe_call(view, "foreGroundColor")
        return {
            "preset": preset.name() if preset else None,
            "brush_size": _safe_call(view, "brushSize"),
            "opacity": _safe_call(view, "paintingOpacity"),
            "flow": _safe_call(view, "paintingFlow"),
            "rotation": _safe_call(view, "brushRotation"),
            "eraser_mode": _safe_call(view, "eraserMode"),
            "disable_pressure": _safe_call(view, "disablePressure"),
            "foreground": self._managed_color(foreground, view),
        }

    def _managed_color(self, color, view) -> dict[str, Any] | None:
        if color is None:
            return None
        result: dict[str, Any] = {
            "model": _safe_call(color, "colorModel"),
            "depth": _safe_call(color, "colorDepth"),
            "profile": _safe_call(color, "colorProfile"),
            "components": _safe_call(color, "components"),
        }
        try:
            qcolor = color.colorForCanvas(view.canvas())
            result["rgba"] = [qcolor.red(), qcolor.green(), qcolor.blue(), qcolor.alpha()]
            result["hex"] = qcolor.name()
        except Exception:
            pass
        return result

    def _find_preset(self, name: str):
        resources = Krita.instance().resources("preset")
        direct = resources.get(name)
        if direct is not None:
            return direct
        target = name.casefold()
        for resource in resources.values():
            if resource.name().casefold() == target:
                return resource
        return None

    def _layer_result(self, doc, action: str, affected=None) -> dict[str, Any]:
        active = doc.activeNode()
        result = {
            "action": action,
            "active_layer_id": self._node_id(active) if active else None,
            "layers": self._layer_forest(doc),
        }
        if affected is not None:
            result["affected_layer_id"] = self._node_id(affected)
        return result

    def _finish_mutation(self, doc) -> None:
        doc.waitForDone()
        doc.refreshProjection()
        self.bridge_revision += 1

    def _undo_segments(self, count: int, doc) -> int:
        undo_action = Krita.instance().action("edit_undo")
        if undo_action is None:
            return 0
        completed = 0
        try:
            for _ in range(count):
                undo_action.trigger()
                completed += 1
            doc.waitForDone()
            doc.refreshProjection()
        except Exception:
            pass
        return completed


def _safe_call(target, method: str, default=None):
    if target is None:
        return default
    function = getattr(target, method, None)
    if function is None:
        return default
    try:
        return function()
    except Exception:
        return default


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KritaOperationError("INVALID_ARGUMENT", f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise KritaOperationError(
            "INVALID_ARGUMENT", f"{field} must not exceed {maximum} characters"
        )
    return result


def _optional_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise KritaOperationError("INVALID_ARGUMENT", f"{field} must be a string")
    result = value.strip()
    if len(result) > maximum:
        raise KritaOperationError(
            "INVALID_ARGUMENT", f"{field} must not exceed {maximum} characters"
        )
    return result


def _optional_bool(arguments: Mapping[str, Any], field: str) -> bool | None:
    if field not in arguments:
        return None
    value = arguments[field]
    if not isinstance(value, bool):
        raise KritaOperationError("INVALID_ARGUMENT", f"{field} must be a boolean")
    return value


def _parse_qcolor(value: Any) -> QColor:
    text = _required_text(value, "foreground", 9)
    if len(text) not in {7, 9} or not text.startswith("#"):
        raise KritaOperationError("INVALID_ARGUMENT", "foreground must be #RRGGBB or #RRGGBBAA")
    try:
        int(text[1:], 16)
    except ValueError as exc:
        raise KritaOperationError(
            "INVALID_ARGUMENT", "foreground must be #RRGGBB or #RRGGBBAA"
        ) from exc
    color = QColor(text[:7])
    if len(text) == 9:
        color.setAlpha(int(text[7:9], 16))
    if not color.isValid():
        raise KritaOperationError("INVALID_ARGUMENT", "foreground color is invalid")
    return color


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise KritaOperationError(
            "INVALID_ARGUMENT", f"{field} must be an integer between {minimum} and {maximum}"
        )
    return value


def _bounded_float(value: Any, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KritaOperationError(
            "INVALID_ARGUMENT", f"{field} must be a number between {minimum} and {maximum}"
        )
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise KritaOperationError(
            "INVALID_ARGUMENT", f"{field} must be a number between {minimum} and {maximum}"
        )
    return result


def _fit_size(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    scale = min(max_width / width, max_height / height, 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))
