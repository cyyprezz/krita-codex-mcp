from __future__ import annotations

from typing import Any

from .validation import InputError, finite_number, hex_color, required_text

DRAW_SHAPES = {"line", "path", "rectangle", "ellipse", "polygon"}
STROKE_STYLES = {"None", "ForegroundColor", "BackgroundColor"}
FILL_STYLES = {"None", "ForegroundColor", "BackgroundColor"}


class DrawingServiceMixin:
    async def draw(
        self,
        *,
        shape: str,
        document_id: str | None = None,
        layer_id: str | None = None,
        points: list[dict[str, Any]] | None = None,
        x: float | None = None,
        y: float | None = None,
        width: float | None = None,
        height: float | None = None,
        closed: bool = False,
        stroke_style: str = "ForegroundColor",
        fill_style: str = "None",
        preset_name: str | None = None,
        brush_size: float | None = None,
        foreground: str | None = None,
        background: str | None = None,
    ) -> dict[str, Any]:
        if shape not in DRAW_SHAPES:
            raise InputError("shape must be line, path, rectangle, ellipse, or polygon")
        if stroke_style not in STROKE_STYLES:
            raise InputError("stroke_style must be None, ForegroundColor, or BackgroundColor")
        if fill_style not in FILL_STYLES:
            raise InputError("fill_style must be None, ForegroundColor, or BackgroundColor")
        if stroke_style == "None" and fill_style == "None":
            raise InputError("stroke_style and fill_style cannot both be None")
        if not isinstance(closed, bool):
            raise InputError("closed must be a boolean")

        arguments: dict[str, Any] = {
            "shape": shape,
            "stroke_style": stroke_style,
            "fill_style": fill_style,
        }
        if document_id is not None:
            arguments["document_id"] = required_text(
                document_id, "document_id", maximum=80
            )
        if layer_id is not None:
            arguments["layer_id"] = required_text(layer_id, "layer_id", maximum=80)
        if shape in {"line", "path", "polygon"}:
            normalized = self._draw_points(points, shape)
            arguments["points"] = normalized
            if shape == "path":
                arguments["closed"] = closed
        else:
            arguments["rect"] = {
                "x": finite_number(x, "x", -1_000_000.0, 1_000_000.0),
                "y": finite_number(y, "y", -1_000_000.0, 1_000_000.0),
                "width": finite_number(width, "width", 0.1, 32768.0),
                "height": finite_number(height, "height", 0.1, 32768.0),
            }
        if preset_name is not None:
            arguments["preset_name"] = required_text(
                preset_name, "preset_name", maximum=300
            )
        if brush_size is not None:
            arguments["brush_size"] = finite_number(
                brush_size, "brush_size", 0.1, 1000.0
            )
        if foreground is not None:
            arguments["foreground"] = hex_color(foreground)
        if background is not None:
            arguments["background"] = hex_color(background)
        return await self.client.execute("draw", arguments)

    @staticmethod
    def _draw_points(
        points: list[dict[str, Any]] | None, shape: str
    ) -> list[dict[str, float]]:
        minimum = 3 if shape == "polygon" else 2
        maximum = 512
        if not isinstance(points, list) or not minimum <= len(points) <= maximum:
            raise InputError(f"points must contain between {minimum} and {maximum} entries")
        if shape == "line" and len(points) != 2:
            raise InputError("line requires exactly two points")
        normalized: list[dict[str, float]] = []
        for index, raw in enumerate(points):
            if not isinstance(raw, dict):
                raise InputError(f"points[{index}] must be an object")
            point = {
                "x": finite_number(
                    raw.get("x"), f"points[{index}].x", -1_000_000.0, 1_000_000.0
                ),
                "y": finite_number(
                    raw.get("y"), f"points[{index}].y", -1_000_000.0, 1_000_000.0
                ),
            }
            if shape == "line":
                point["pressure"] = finite_number(
                    raw.get("pressure", 1.0),
                    f"points[{index}].pressure",
                    0.0,
                    1.0,
                )
            normalized.append(point)
        return normalized
