from __future__ import annotations

from typing import Any

from krita import InfoObject, Krita, ManagedColor, Selection
from PyQt5.QtCore import QRect

from ._service_helpers import _bounded_int, _parse_qcolor, _required_text, _safe_call
from .errors import KritaOperationError


class ColorAdjustmentOperationsMixin:
    def _replace_color(self, arguments, doc) -> dict[str, Any]:
        layer_id = _required_text(arguments.get("layer_id"), "layer_id", 80)
        node = self._editable_paint_node(doc, layer_id)
        if doc.colorModel() != "RGBA" or doc.colorDepth() != "U8":
            raise KritaOperationError(
                "UNSUPPORTED_COLOR_SPACE",
                "Color replacement currently requires an RGBA/U8 document",
                {"color_model": doc.colorModel(), "color_depth": doc.colorDepth()},
            )
        bounds = node.bounds()
        width, height = int(bounds.width()), int(bounds.height())
        if width <= 0 or height <= 0:
            raise KritaOperationError("EMPTY_LAYER", "Target paint layer has no pixels")
        if width * height > self._REPLACE_MAX_PIXELS:
            raise KritaOperationError(
                "COLOR_REPLACE_TOO_LARGE",
                "Color replacement exceeds the bounded pixel limit",
                {"pixels": width * height, "limit": self._REPLACE_MAX_PIXELS},
            )
        source_text = _required_text(arguments.get("from_color"), "from_color", 9)
        target_text = _required_text(arguments.get("to_color"), "to_color", 9)
        source = _parse_qcolor(source_text)
        target = _parse_qcolor(target_text)
        tolerance = _bounded_int(arguments.get("tolerance", 0), "tolerance", 0, 255)
        payload = bytearray(node.pixelData(bounds.x(), bounds.y(), width, height))
        if len(payload) != width * height * 4:
            raise KritaOperationError(
                "UNSUPPORTED_PIXEL_LAYOUT",
                "Krita returned an unexpected RGBA/U8 pixel layout",
                {"bytes": len(payload), "expected": width * height * 4},
            )
        source_rgba = (source.red(), source.green(), source.blue(), source.alpha())
        match_alpha = len(source_text) == 9
        replaced = 0
        mask = bytearray(width * height)
        for offset in range(0, len(payload), 4):
            blue, green, red, alpha = payload[offset : offset + 4]
            if (
                abs(red - source_rgba[0]) <= tolerance
                and abs(green - source_rgba[1]) <= tolerance
                and abs(blue - source_rgba[2]) <= tolerance
                and (not match_alpha or abs(alpha - source_rgba[3]) <= tolerance)
            ):
                mask[offset // 4] = 255
                replaced += 1
        if replaced == 0:
            return {
                "action": "replace_color",
                "document_id": self._document_id(doc),
                "layer_id": self._node_id(node),
                "changed": False,
                "replaced_pixels": 0,
                "undo_steps": 0,
            }

        view = self._active_view()
        managed = ManagedColor.fromQColor(target, view.canvas())
        if managed is None:
            raise KritaOperationError(
                "COLOR_CONVERSION_FAILED", "Krita could not convert the replacement color"
            )
        previous_selection = doc.selection()
        saved_selection = previous_selection.duplicate() if previous_selection is not None else None
        previous_node = doc.activeNode()
        previous_foreground = view.foregroundColor()
        replacement_selection = Selection(doc)
        replacement_selection.setPixelData(mask, bounds.x(), bounds.y(), width, height)
        completed = False
        try:
            doc.setSelection(replacement_selection)
            doc.setActiveNode(node)
            view.setForeGroundColor(managed)
            self._trigger_action("fill_selection_foreground_color", doc, scope="color")
            doc.refreshProjection()
            completed = True
        finally:
            if not completed:
                doc.setSelection(saved_selection)
            if previous_node is not None:
                doc.setActiveNode(previous_node)
            if previous_foreground is not None:
                view.setForeGroundColor(previous_foreground)
        self.bridge_revision += 1
        return {
            "action": "replace_color",
            "document_id": self._document_id(doc),
            "layer_id": self._node_id(node),
            "changed": True,
            "from_color": self._qcolor_hex(source),
            "to_color": self._qcolor_hex(target),
            "tolerance": tolerance,
            "replaced_pixels": replaced,
            "undo_steps": 1,
            "undo_behavior": "one native Krita fill-selection undo step",
            "native_fill": True,
            "selection_replaced": True,
            "selection_behavior": "matching pixels remain selected; call deselect when finished",
            "previous_selection_existed": previous_selection is not None,
        }

    def _adjust(self, arguments, doc) -> dict[str, Any]:
        layer_id = _required_text(arguments.get("layer_id"), "layer_id", 80)
        node = self._editable_paint_node(doc, layer_id)
        name = _required_text(arguments.get("filter_name"), "filter_name", 100)
        if name not in self._SAFE_FILTERS:
            raise KritaOperationError("INVALID_ARGUMENT", "Filter is not in the safe allowlist")
        filter_object = Krita.instance().filter(name)
        if filter_object is None:
            raise KritaOperationError("FILTER_NOT_FOUND", "Krita filter is not available")
        configuration = filter_object.configuration()
        previous_properties = dict(configuration.properties()) if configuration else {}
        settings = arguments.get("settings", {})
        if not isinstance(settings, dict) or len(settings) > 32:
            raise KritaOperationError("INVALID_ARGUMENT", "settings must be a bounded object")
        unknown = sorted(set(settings) - set(previous_properties))
        if unknown:
            raise KritaOperationError(
                "INVALID_FILTER_SETTING",
                "One or more filter settings are not exposed by this Krita build",
                {"unknown": unknown, "available": sorted(previous_properties)},
            )
        if configuration is None and settings:
            raise KritaOperationError(
                "INVALID_FILTER_SETTING", "This filter does not expose configurable settings"
            )
        for key, value in settings.items():
            if not isinstance(value, (bool, int, float, str)):
                raise KritaOperationError(
                    "INVALID_FILTER_SETTING", f"Filter setting {key} must be scalar"
                )
            configuration.setProperty(key, value)
        if configuration is not None:
            filter_object.setConfiguration(configuration)
        rect = self._filter_rect(arguments, doc, node)
        previous_node = doc.activeNode()
        doc.setActiveNode(node)
        try:
            started = filter_object.startFilter(
                node, rect.x(), rect.y(), rect.width(), rect.height()
            )
            if not started:
                raise KritaOperationError(
                    "FILTER_APPLY_FAILED", "Krita rejected the filter stroke"
                )
            doc.waitForDone()
            doc.refreshProjection()
        except Exception as exc:
            raise KritaOperationError(
                "FILTER_APPLY_FAILED",
                "Krita could not apply the requested color correction",
                {"filter_name": name, "reason": str(exc)},
            ) from exc
        finally:
            if configuration is not None:
                restored = InfoObject()
                restored.setProperties(previous_properties)
                filter_object.setConfiguration(restored)
            if previous_node is not None:
                doc.setActiveNode(previous_node)
        self.bridge_revision += 1
        return {
            "action": "adjust",
            "document_id": self._document_id(doc),
            "layer_id": self._node_id(node),
            "filter_name": name,
            "settings": settings,
            "rect": {
                "x": rect.x(),
                "y": rect.y(),
                "width": rect.width(),
                "height": rect.height(),
            },
            "undo_steps": 1,
            "native_filter": True,
        }

    def _editable_paint_node(self, doc, layer_id: str):
        node = self._find_node(doc, layer_id)
        if node is None or node.type().lower() != "paintlayer":
            raise KritaOperationError("LAYER_NOT_PAINTABLE", "Target layer is not a paint layer")
        if bool(_safe_call(node, "locked", default=False)):
            raise KritaOperationError("LAYER_LOCKED", "Target paint layer is locked")
        return node

    @staticmethod
    def _filter_rect(arguments, doc, node):
        raw = arguments.get("rect")
        if raw is not None:
            if not isinstance(raw, dict):
                raise KritaOperationError("INVALID_ARGUMENT", "rect must be an object")
            rect = QRect(
                _bounded_int(raw.get("x"), "rect.x", -1_000_000, 1_000_000),
                _bounded_int(raw.get("y"), "rect.y", -1_000_000, 1_000_000),
                _bounded_int(raw.get("width"), "rect.width", 1, 32768),
                _bounded_int(raw.get("height"), "rect.height", 1, 32768),
            )
        else:
            selection = doc.selection()
            rect = (
                QRect(selection.x(), selection.y(), selection.width(), selection.height())
                if selection is not None and selection.width() > 0 and selection.height() > 0
                else node.bounds()
            )
        clipped = rect.intersected(QRect(0, 0, int(doc.width()), int(doc.height())))
        if clipped.isEmpty():
            raise KritaOperationError("EMPTY_REGION", "Filter region does not intersect the canvas")
        return clipped
