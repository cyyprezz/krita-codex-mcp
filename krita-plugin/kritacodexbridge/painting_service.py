from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from krita import Krita, ManagedColor
from PyQt5.QtCore import QPoint

from ._service_helpers import (
    _bounded_float,
    _bounded_int,
    _optional_bool,
    _optional_text,
    _parse_qcolor,
    _required_text,
    _safe_call,
)
from .errors import KritaOperationError


class PaintingOperationsMixin:
    def history(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        doc = self._target_document(arguments, activate=True)
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
        self._target_document(arguments, activate=True)
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
        doc = self._target_document(arguments, activate=True)
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
            "document_id": self._document_id(doc),
            "layer_id": self._node_id(node),
            "segments": completed,
            "undo_steps": completed,
            "undo_behavior": "one Krita undo step per line segment",
            "preset": preset.name() if preset else None,
            "brush_size": float(view.brushSize()),
            "native_brush_engine": True,
        }

    def _history_result(
        self, doc, action: str, *, requested: int, completed: int
    ) -> dict[str, Any]:
        app = Krita.instance()
        undo = app.action("edit_undo")
        redo = app.action("edit_redo")
        return {
            "document_id": self._document_id(doc),
            "document_file_name": doc.fileName(),
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
