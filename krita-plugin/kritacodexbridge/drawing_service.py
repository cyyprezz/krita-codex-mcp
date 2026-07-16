from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any

from krita import Krita, ManagedColor
from PyQt5.QtCore import QPoint, QPointF, QRect, QRectF
from PyQt5.QtGui import QPainterPath

from ._service_helpers import (
    _bounded_float,
    _parse_qcolor,
    _required_text,
    _safe_call,
)
from .errors import KritaOperationError


class DrawingOperationsMixin:
    _DRAW_VERIFY_MAX_PIXELS = 4_194_304
    _DRAW_SHAPES = {"line", "path", "rectangle", "ellipse", "polygon"}
    _STROKE_STYLES = {"None", "ForegroundColor", "BackgroundColor"}
    _FILL_STYLES = {"None", "ForegroundColor", "BackgroundColor"}

    def draw(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        doc = self._target_document(arguments, activate=True)
        view = self._active_view()
        shape = arguments.get("shape")
        if shape not in self._DRAW_SHAPES:
            raise KritaOperationError("INVALID_ARGUMENT", "Unsupported draw shape")
        stroke_style = arguments.get("stroke_style", "ForegroundColor")
        fill_style = arguments.get("fill_style", "None")
        if stroke_style not in self._STROKE_STYLES or fill_style not in self._FILL_STYLES:
            raise KritaOperationError("INVALID_ARGUMENT", "Unsupported stroke or fill style")
        if stroke_style == "None" and fill_style == "None":
            raise KritaOperationError(
                "INVALID_ARGUMENT", "stroke_style and fill_style cannot both be None"
            )

        layer_id = arguments.get("layer_id")
        node = self._find_node(doc, layer_id) if layer_id else doc.activeNode()
        if node is None or node.type().lower() != "paintlayer":
            raise KritaOperationError("LAYER_NOT_PAINTABLE", "Target layer is not a paint layer")
        if bool(_safe_call(node, "locked", default=False)):
            raise KritaOperationError("LAYER_LOCKED", "Target paint layer is locked")
        method_name = {
            "line": "paintLine",
            "path": "paintPath",
            "polygon": "paintPolygon",
            "rectangle": "paintRectangle",
            "ellipse": "paintEllipse",
        }[shape]
        if not hasattr(node, method_name):
            raise KritaOperationError(
                "UNSUPPORTED_FEATURE",
                f"This Krita build does not expose Node.{method_name} to Python",
                {"krita_version": _safe_call(Krita.instance(), "version")},
            )
        paint_ability = _safe_call(node, "paintAbility")
        if paint_ability not in {None, "PAINT"}:
            raise KritaOperationError(
                "LAYER_NOT_PAINTABLE",
                "The current preset cannot paint on this layer",
                {"paint_ability": paint_ability},
            )

        points = self._shape_points(arguments, doc, shape)
        rect = self._shape_rect(arguments, doc) if shape in {"rectangle", "ellipse"} else None
        preset = self._requested_preset(arguments)
        brush_size = (
            _bounded_float(arguments["brush_size"], "brush_size", 0.1, 1000.0)
            if "brush_size" in arguments
            else None
        )
        foreground = self._requested_managed_color(arguments, "foreground", view)
        background = self._requested_managed_color(arguments, "background", view)

        previous_node = doc.activeNode()
        previous = {
            "preset": view.currentBrushPreset(),
            "brush_size": float(view.brushSize()),
            "foreground": view.foregroundColor(),
            "background": view.backgroundColor(),
        }
        completed = 0
        doc.setActiveNode(node)
        try:
            if preset is not None:
                view.setCurrentBrushPreset(preset)
            if brush_size is not None:
                view.setBrushSize(brush_size)
            if foreground is not None:
                view.setForeGroundColor(foreground)
            if background is not None:
                view.setBackGroundColor(background)
            self._synchronize_projection(doc)
            verification_rect = self._draw_verification_rect(
                doc,
                node,
                shape,
                points,
                rect,
                brush_size=float(view.brushSize()),
                include_stroke=stroke_style != "None",
            )
            before = self._draw_content_snapshot(
                node, verification_rect, capture_pixels=None
            )
            self._paint_shape(
                node,
                shape,
                points,
                rect,
                stroke_style,
                fill_style,
                bool(arguments.get("closed", False)),
            )
            completed = 1
            self._synchronize_projection(doc)
            after = self._draw_content_snapshot(
                node,
                verification_rect,
                capture_pixels=bool(before["has_extents"]),
            )
            if not self._draw_snapshot_changed(before, after):
                self._restore_draw_state(view, previous, doc, previous_node)
                raise KritaOperationError(
                    "DRAW_NO_CHANGE",
                    "Krita completed the native draw call without changing target pixels or bounds",
                    {
                        "document_id": self._document_id(doc),
                        "layer_id": self._node_id(node),
                        "layer_type": node.type(),
                        "paint_ability": paint_ability,
                        "locked": bool(_safe_call(node, "locked", default=False)),
                        "writable": paint_ability in {None, "PAINT"},
                        "verification_rect": self._draw_rect_mapping(verification_rect),
                        "before_bounds": before["bounds"],
                        "after_bounds": after["bounds"],
                        "before_has_extents": before["has_extents"],
                        "after_has_extents": after["has_extents"],
                        "undo_attempted": False,
                        "reason": "native painting API produced no observable layer-content change",
                    },
                )
        except Exception as exc:
            if isinstance(exc, KritaOperationError) and exc.code == "DRAW_NO_CHANGE":
                raise
            if isinstance(exc, KritaOperationError) and completed == 0:
                self._restore_draw_state(view, previous, doc, previous_node)
                raise
            rolled_back = self._undo_segments(completed, doc)
            self._restore_draw_state(view, previous, doc, previous_node)
            if rolled_back != completed:
                self.bridge_revision += 1
            raise KritaOperationError(
                "DRAW_FAILED",
                "Krita failed while drawing the native shape",
                {"shape": shape, "rollback_steps": rolled_back, "reason": str(exc)},
            ) from exc

        self.bridge_revision += 1
        active_preset = view.currentBrushPreset()
        return {
            "document_id": self._document_id(doc),
            "layer_id": self._node_id(node),
            "shape": shape,
            "stroke_style": stroke_style,
            "fill_style": fill_style,
            "closed": bool(arguments.get("closed", False)) if shape == "path" else None,
            "preset": active_preset.name() if active_preset else None,
            "brush_size": float(view.brushSize()),
            "undo_steps": 1,
            "undo_behavior": "one Krita undo step for the native shape",
            "native_brush_engine": True,
            "verified_content_change": True,
        }

    def _draw_content_snapshot(self, node, rect, *, capture_pixels):
        bounds = node.bounds()
        bounds_mapping = self._draw_rect_mapping(bounds)
        has_extents = bool(
            _safe_call(
                node,
                "hasExtents",
                default=bounds_mapping["width"] > 0 and bounds_mapping["height"] > 0,
            )
        )
        if capture_pixels is None:
            capture_pixels = has_extents
        digest = None
        if capture_pixels:
            pixels = int(rect.width()) * int(rect.height())
            if pixels > self._DRAW_VERIFY_MAX_PIXELS:
                raise KritaOperationError(
                    "DRAW_VERIFICATION_TOO_LARGE",
                    "The affected area is too large to verify safely on a non-empty layer",
                    {
                        "pixels": pixels,
                        "limit": self._DRAW_VERIFY_MAX_PIXELS,
                        "recovery": (
                            "split the shape into smaller operations or draw on an empty layer"
                        ),
                    },
                )
            payload = bytes(
                node.pixelData(
                    int(rect.x()),
                    int(rect.y()),
                    int(rect.width()),
                    int(rect.height()),
                )
            )
            digest = hashlib.sha256(payload).hexdigest()
        return {
            "has_extents": has_extents,
            "bounds": bounds_mapping,
            "pixel_digest": digest,
        }

    @staticmethod
    def _draw_snapshot_changed(before, after) -> bool:
        if before["bounds"] != after["bounds"]:
            return True
        if not before["has_extents"]:
            return bool(after["has_extents"])
        return before["pixel_digest"] != after["pixel_digest"]

    @staticmethod
    def _draw_rect_mapping(rect) -> dict[str, int]:
        return {
            "x": int(rect.x()),
            "y": int(rect.y()),
            "width": int(rect.width()),
            "height": int(rect.height()),
        }

    def _draw_verification_rect(
        self,
        doc,
        node,
        shape,
        points,
        rect,
        *,
        brush_size: float,
        include_stroke: bool,
    ):
        if shape in {"rectangle", "ellipse"}:
            left = float(rect.x())
            top = float(rect.y())
            right = left + float(rect.width())
            bottom = top + float(rect.height())
        else:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            left, right = min(xs), max(xs)
            top, bottom = min(ys), max(ys)
        margin = max(2, math.ceil(brush_size)) if include_stroke else 1
        left_i = math.floor(left) - margin
        top_i = math.floor(top) - margin
        right_i = math.ceil(right) + margin
        bottom_i = math.ceil(bottom) + margin
        canvas_rect = QRect(0, 0, int(doc.width()), int(doc.height()))
        affected = QRect(
            left_i,
            top_i,
            max(1, right_i - left_i + 1),
            max(1, bottom_i - top_i + 1),
        ).intersected(canvas_rect)
        if affected.isEmpty():
            raise KritaOperationError(
                "INVALID_ARGUMENT", "The requested shape does not intersect the canvas"
            )
        existing_bounds = node.bounds().intersected(canvas_rect)
        if not existing_bounds.isEmpty():
            affected = affected.united(existing_bounds)
        return affected

    def _shape_points(self, arguments, doc, shape: str):
        if shape not in {"line", "path", "polygon"}:
            return []
        raw_points = arguments.get("points")
        minimum = 3 if shape == "polygon" else 2
        if not isinstance(raw_points, list) or not minimum <= len(raw_points) <= 512:
            raise KritaOperationError(
                "INVALID_ARGUMENT", f"points must contain between {minimum} and 512 entries"
            )
        if shape == "line" and len(raw_points) != 2:
            raise KritaOperationError("INVALID_ARGUMENT", "line requires exactly two points")
        points = []
        for index, raw in enumerate(raw_points):
            if not isinstance(raw, dict):
                raise KritaOperationError("INVALID_ARGUMENT", f"points[{index}] must be an object")
            x = _bounded_float(
                raw.get("x"), f"points[{index}].x", -1_000_000.0, 1_000_000.0
            )
            y = _bounded_float(
                raw.get("y"), f"points[{index}].y", -1_000_000.0, 1_000_000.0
            )
            pressure = (
                _bounded_float(
                    raw.get("pressure", 1.0),
                    f"points[{index}].pressure",
                    0.0,
                    1.0,
                )
                if shape == "line"
                else 1.0
            )
            points.append((x, y, pressure))
        return points

    def _shape_rect(self, arguments, doc):
        raw = arguments.get("rect")
        if not isinstance(raw, dict):
            raise KritaOperationError("INVALID_ARGUMENT", "rect must be an object")
        return QRectF(
            _bounded_float(raw.get("x"), "rect.x", -1_000_000.0, 1_000_000.0),
            _bounded_float(raw.get("y"), "rect.y", -1_000_000.0, 1_000_000.0),
            _bounded_float(raw.get("width"), "rect.width", 0.1, 32768.0),
            _bounded_float(raw.get("height"), "rect.height", 0.1, 32768.0),
        )

    def _requested_preset(self, arguments):
        if "preset_name" not in arguments:
            return None
        name = _required_text(arguments["preset_name"], "preset_name", 300)
        preset = self._find_preset(name)
        if preset is None:
            raise KritaOperationError(
                "PRESET_NOT_FOUND", "Krita brush preset was not found", {"name": name}
            )
        return preset

    @staticmethod
    def _requested_managed_color(arguments, field: str, view):
        if field not in arguments:
            return None
        qcolor = _parse_qcolor(arguments[field])
        managed = ManagedColor.fromQColor(qcolor, view.canvas())
        if managed is None:
            raise KritaOperationError(
                "COLOR_CONVERSION_FAILED", f"Krita could not convert the requested {field} color"
            )
        return managed

    @staticmethod
    def _paint_shape(node, shape, points, rect, stroke_style, fill_style, closed):
        if shape == "line":
            start, end = points
            node.paintLine(
                QPoint(round(start[0]), round(start[1])),
                QPoint(round(end[0]), round(end[1])),
                start[2],
                end[2],
                stroke_style,
            )
        elif shape == "path":
            path = QPainterPath(QPointF(points[0][0], points[0][1]))
            for point in points[1:]:
                path.lineTo(QPointF(point[0], point[1]))
            if closed:
                path.closeSubpath()
            node.paintPath(path, stroke_style, fill_style)
        elif shape == "polygon":
            node.paintPolygon(
                [QPointF(point[0], point[1]) for point in points],
                stroke_style,
                fill_style,
            )
        elif shape == "rectangle":
            node.paintRectangle(rect, stroke_style, fill_style)
        else:
            node.paintEllipse(rect, stroke_style, fill_style)

    @staticmethod
    def _restore_draw_state(view, previous, doc, previous_node) -> None:
        try:
            if previous["preset"] is not None:
                view.setCurrentBrushPreset(previous["preset"])
            view.setBrushSize(previous["brush_size"])
            if previous["foreground"] is not None:
                view.setForeGroundColor(previous["foreground"])
            if previous["background"] is not None:
                view.setBackGroundColor(previous["background"])
            if previous_node is not None:
                doc.setActiveNode(previous_node)
        except Exception:
            pass
