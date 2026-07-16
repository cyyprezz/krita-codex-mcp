from __future__ import annotations

import base64
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from krita import Krita
from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QImage, QImageReader

from krita_codex_protocol import PathPolicyError

from ._service_helpers import (
    _bounded_float,
    _bounded_int,
    _fit_size,
    _required_text,
)
from .errors import KritaOperationError

RASTER_INPUT_EXTENSIONS = {
    ".bmp",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
CANVAS_ANCHOR_FACTORS = {
    "top_left": (0.0, 0.0),
    "top": (0.5, 0.0),
    "top_right": (1.0, 0.0),
    "left": (0.0, 0.5),
    "center": (0.5, 0.5),
    "right": (1.0, 0.5),
    "bottom_left": (0.0, 1.0),
    "bottom": (0.5, 1.0),
    "bottom_right": (1.0, 1.0),
}


class CanvasOperationsMixin:
    def preview(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        doc = self._target_document(arguments)
        max_width = _bounded_int(
            arguments.get("max_width"), "max_width", 64, self.config.preview_max_dimension
        )
        max_height = _bounded_int(
            arguments.get("max_height"), "max_height", 64, self.config.preview_max_dimension
        )
        scale = _bounded_int(arguments.get("scale", 1), "scale", 1, 16)
        raw_region = arguments.get("region")
        if raw_region is None:
            if scale != 1:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "preview scale greater than 1 requires a region"
                )
            if int(doc.width()) * int(doc.height()) > self.config.document_max_pixels:
                raise KritaOperationError(
                    "PREVIEW_TOO_LARGE",
                    "Whole-canvas preview exceeds the configured source pixel limit; "
                    "request a bounded region instead",
                    {
                        "pixels": int(doc.width()) * int(doc.height()),
                        "limit": self.config.document_max_pixels,
                    },
                )
            width, height = _fit_size(doc.width(), doc.height(), max_width, max_height)
            source_rect = QRect(0, 0, int(doc.width()), int(doc.height()))
            self._synchronize_projection(doc)
            image = doc.projection(
                source_rect.x(),
                source_rect.y(),
                source_rect.width(),
                source_rect.height(),
            )
            if image is not None and not image.isNull() and (
                image.width() != width or image.height() != height
            ):
                image = image.scaled(
                    width,
                    height,
                    Qt.IgnoreAspectRatio,
                    Qt.SmoothTransformation,
                )
            preview_mode = "thumbnail"
        else:
            if not isinstance(raw_region, Mapping):
                raise KritaOperationError("INVALID_ARGUMENT", "region must be an object")
            source_rect = self._bounded_canvas_rect(doc, raw_region, field="region")
            if source_rect.width() * source_rect.height() > self.config.document_max_pixels:
                raise KritaOperationError(
                    "PREVIEW_TOO_LARGE", "Preview region exceeds the configured pixel limit"
                )
            self._synchronize_projection(doc)
            image = doc.projection(
                source_rect.x(),
                source_rect.y(),
                source_rect.width(),
                source_rect.height(),
            )
            if image is not None and not image.isNull():
                target_scale = min(
                    float(scale),
                    max_width / source_rect.width(),
                    max_height / source_rect.height(),
                )
                target_width = max(1, round(source_rect.width() * target_scale))
                target_height = max(1, round(source_rect.height() * target_scale))
                if target_width != image.width() or target_height != image.height():
                    image = image.scaled(
                        target_width,
                        target_height,
                        Qt.IgnoreAspectRatio,
                        Qt.FastTransformation,
                    )
            preview_mode = "projection_region"
        if image is None or image.isNull():
            raise KritaOperationError(
                "PREVIEW_FAILED", "Krita returned an empty document preview"
            )
        png = self._encode_png_preview(image)
        active = doc.activeNode()
        return {
            "mime_type": "image/png",
            "mode": preview_mode,
            "width": int(image.width()),
            "height": int(image.height()),
            "source_width": int(doc.width()),
            "source_height": int(doc.height()),
            "source_rect": self._rect_mapping(source_rect),
            "requested_scale": scale,
            "document_name": doc.name(),
            "document_id": self._document_id(doc),
            "document_file_name": doc.fileName(),
            "active_layer_id": self._node_id(active) if active else None,
            "png_base64": base64.b64encode(png).decode("ascii"),
        }

    def canvas(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        action = arguments.get("action")
        supported = {
            "read",
            "sample_color",
            "resize",
            "scale",
            "crop",
            "rotate",
            "flip",
            "import_layer",
        }
        if action not in supported:
            raise KritaOperationError("INVALID_ARGUMENT", f"Unsupported canvas action: {action}")
        doc = self._target_document(
            arguments, activate=action not in {"read", "sample_color"}
        )
        if action == "read":
            return self._canvas_result(doc, action)
        if action == "sample_color":
            x = _bounded_int(arguments.get("x"), "x", 0, int(doc.width()) - 1)
            y = _bounded_int(arguments.get("y"), "y", 0, int(doc.height()) - 1)
            self._synchronize_projection(doc)
            image = doc.projection(x, y, 1, 1)
            if image is None or image.isNull():
                raise KritaOperationError(
                    "COLOR_SAMPLE_FAILED", "Krita returned no projection pixel"
                )
            color = image.pixelColor(0, 0)
            result = self._canvas_result(doc, action)
            result["sample"] = {
                "x": x,
                "y": y,
                "rgba": [color.red(), color.green(), color.blue(), color.alpha()],
                "hex": (
                    f"#{color.red():02X}{color.green():02X}"
                    f"{color.blue():02X}{color.alpha():02X}"
                ),
                "source": "composited_projection",
            }
            return result
        if action == "resize":
            width, height = self._canvas_dimensions(arguments)
            anchor = arguments.get("anchor", "center")
            factors = CANVAS_ANCHOR_FACTORS.get(anchor)
            if factors is None:
                raise KritaOperationError("INVALID_ARGUMENT", "Unsupported canvas anchor")
            old_width, old_height = int(doc.width()), int(doc.height())
            if (width, height) == (old_width, old_height):
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "resize must change at least one canvas dimension"
                )
            left = math.floor((old_width - width) * factors[0])
            top = math.floor((old_height - height) * factors[1])
            doc.resizeImage(left, top, width, height)
            details = {"anchor": anchor, "left": left, "top": top}
        elif action == "scale":
            width, height = self._canvas_dimensions(arguments)
            if (width, height) == (int(doc.width()), int(doc.height())):
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "scale must change at least one image dimension"
                )
            requested = _required_text(arguments.get("strategy"), "strategy", 100)
            available = [str(value) for value in Krita.instance().filterStrategies()]
            strategy = next(
                (value for value in available if value.casefold() == requested.casefold()), None
            )
            if strategy is None:
                raise KritaOperationError(
                    "UNSUPPORTED_SCALE_STRATEGY",
                    "Krita does not provide the requested scale strategy",
                    {"requested": requested, "available": available},
                )
            doc.scaleImage(
                width,
                height,
                max(1, round(float(doc.xRes()))),
                max(1, round(float(doc.yRes()))),
                strategy,
            )
            details = {"strategy": strategy}
        elif action == "crop":
            rect = self._bounded_canvas_rect(doc, arguments, field="crop")
            if rect == QRect(0, 0, int(doc.width()), int(doc.height())):
                raise KritaOperationError("INVALID_ARGUMENT", "crop must reduce the canvas")
            doc.crop(rect.x(), rect.y(), rect.width(), rect.height())
            details = {"source_rect": self._rect_mapping(rect)}
        elif action == "rotate":
            angle = _bounded_float(
                arguments.get("angle_degrees"), "angle_degrees", -360.0, 360.0
            )
            if angle == 0:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "angle_degrees must be non-zero"
                )
            doc.rotateImage(math.radians(angle))
            details = {"angle_degrees": angle}
        elif action == "flip":
            direction = arguments.get("direction")
            action_id = {
                "horizontal": "mirrorImageHorizontal",
                "vertical": "mirrorImageVertical",
            }.get(direction)
            if action_id is None:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "direction must be horizontal or vertical"
                )
            self._trigger_action(action_id, doc, scope="canvas")
            details = {"direction": direction, "native_action": action_id}
        else:
            return self._import_layer(doc, arguments)
        self._finish_canvas_mutation(doc)
        result = self._canvas_result(doc, action)
        result.update(details)
        return result

    def _import_layer(self, doc, arguments: Mapping[str, Any]) -> dict[str, Any]:
        try:
            path = self.path_policy.validate_input(
                arguments.get("path"), extensions=RASTER_INPUT_EXTENSIONS
            )
        except PathPolicyError as exc:
            raise KritaOperationError("PATH_NOT_ALLOWED", str(exc)) from exc
        file_size = path.stat().st_size
        if file_size > self.config.asset_max_bytes:
            raise KritaOperationError(
                "IMPORT_TOO_LARGE",
                "Input image exceeds the configured byte limit",
                {"bytes": file_size, "limit": self.config.asset_max_bytes},
            )
        if doc.colorModel() != "RGBA" or doc.colorDepth() != "U8":
            raise KritaOperationError(
                "UNSUPPORTED_COLOR_SPACE",
                "Editable raster import currently requires an RGBA/U8 document",
                {"color_model": doc.colorModel(), "color_depth": doc.colorDepth()},
            )
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        declared_size = reader.size()
        if declared_size.isValid():
            self._validate_import_dimensions(declared_size.width(), declared_size.height())
        image = reader.read()
        if image.isNull():
            raise KritaOperationError(
                "IMPORT_DECODE_FAILED",
                "Krita's Qt runtime could not decode the input image",
                {"path": str(path), "reason": reader.errorString()},
            )
        self._validate_import_dimensions(image.width(), image.height())
        image = image.convertToFormat(QImage.Format_ARGB32)
        pointer = image.constBits()
        pointer.setsize(image.byteCount())
        payload = bytes(pointer)

        parent_id = arguments.get("parent_id")
        parent = self._find_node(doc, parent_id) if parent_id else doc.rootNode()
        if parent is None:
            raise KritaOperationError("LAYER_NOT_FOUND", "Parent layer UUID was not found")
        if not self._same_node(parent, doc.rootNode()) and parent.type().lower() != "grouplayer":
            raise KritaOperationError(
                "INVALID_PARENT", "Imported layers require the root or a group layer"
            )
        above = None
        above_id = arguments.get("above_id")
        if above_id is not None:
            above = self._find_node(doc, above_id)
            if above is None or not self._same_node(above.parentNode(), parent):
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "above_id must be a direct child of the destination"
                )
        name = _required_text(arguments.get("name", Path(path).stem), "name", 200)
        x = _bounded_int(arguments.get("x", 0), "x", -1_000_000, 1_000_000)
        y = _bounded_int(arguments.get("y", 0), "y", -1_000_000, 1_000_000)
        node = doc.createNode(name, "paintlayer")
        if node is None or not parent.addChildNode(node, above):
            raise KritaOperationError(
                "LAYER_IMPORT_FAILED", "Krita could not create the imported paint layer"
            )
        try:
            if not node.setPixelData(payload, x, y, image.width(), image.height()):
                raise KritaOperationError(
                    "LAYER_IMPORT_FAILED", "Krita rejected the imported pixel data"
                )
            doc.setActiveNode(node)
            self._finish_canvas_mutation(doc)
        except Exception:
            node.remove()
            raise
        result = self._canvas_result(doc, "import_layer")
        result.update(
            {
                "layer_id": self._node_id(node),
                "layer_name": node.name(),
                "source_path": str(path),
                "source_bytes": file_size,
                "source_width": int(image.width()),
                "source_height": int(image.height()),
                "position": {"x": x, "y": y},
                "embedded_editable": True,
            }
        )
        return result

    def _bounded_canvas_rect(
        self, doc, raw: Mapping[str, Any], *, field: str
    ) -> QRect:
        x = _bounded_int(raw.get("x"), f"{field}.x", 0, int(doc.width()) - 1)
        y = _bounded_int(raw.get("y"), f"{field}.y", 0, int(doc.height()) - 1)
        width = _bounded_int(raw.get("width"), f"{field}.width", 1, int(doc.width()))
        height = _bounded_int(raw.get("height"), f"{field}.height", 1, int(doc.height()))
        if x + width > doc.width() or y + height > doc.height():
            raise KritaOperationError(
                "INVALID_ARGUMENT", f"{field} rectangle must stay inside the canvas"
            )
        return QRect(x, y, width, height)

    def _canvas_dimensions(self, arguments: Mapping[str, Any]) -> tuple[int, int]:
        width = _bounded_int(
            arguments.get("width"), "width", 1, self.config.document_max_dimension
        )
        height = _bounded_int(
            arguments.get("height"), "height", 1, self.config.document_max_dimension
        )
        if width * height > self.config.document_max_pixels:
            raise KritaOperationError(
                "DOCUMENT_TOO_LARGE", "Requested dimensions exceed the configured pixel limit"
            )
        return width, height

    def _validate_import_dimensions(self, width: int, height: int) -> None:
        if (
            width < 1
            or height < 1
            or width > self.config.document_max_dimension
            or height > self.config.document_max_dimension
            or width * height > self.config.document_max_pixels
        ):
            raise KritaOperationError(
                "IMPORT_TOO_LARGE",
                "Input image dimensions exceed the configured document limits",
                {"width": width, "height": height},
            )

    def _finish_canvas_mutation(self, doc) -> None:
        doc.waitForDone()
        doc.refreshProjection()
        self.bridge_revision += 1

    def _canvas_result(self, doc, action: str) -> dict[str, Any]:
        return {
            "action": action,
            "document_id": self._document_id(doc),
            "width": int(doc.width()),
            "height": int(doc.height()),
            "resolution": int(doc.resolution()),
            "document_modified": bool(doc.modified()),
            "filter_strategies": [
                str(value) for value in Krita.instance().filterStrategies()
            ],
        }

    @staticmethod
    def _rect_mapping(rect: QRect) -> dict[str, int]:
        return {
            "x": int(rect.x()),
            "y": int(rect.y()),
            "width": int(rect.width()),
            "height": int(rect.height()),
        }
