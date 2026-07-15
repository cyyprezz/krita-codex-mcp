from __future__ import annotations

from pathlib import Path
from typing import Any

from krita_codex_protocol import PathPolicyError

from .validation import InputError, bounded_int, finite_number, hex_color, required_text

RASTER_INPUT_EXTENSIONS = {
    ".bmp",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

CANVAS_ACTIONS = {
    "read",
    "sample_color",
    "resize",
    "scale",
    "crop",
    "rotate",
    "flip",
    "import_layer",
}
EDIT_ACTIONS = {
    "selection_status",
    "select_rect",
    "select_all",
    "select_opaque",
    "deselect",
    "invert",
    "grow",
    "shrink",
    "fill",
    "clear",
}
SELECTION_COMBINE_MODES = {"replace", "add", "subtract", "intersect"}
CANVAS_ANCHORS = {
    "top_left",
    "top",
    "top_right",
    "left",
    "center",
    "right",
    "bottom_left",
    "bottom",
    "bottom_right",
}


class EditingServiceMixin:
    async def canvas(
        self,
        *,
        action: str,
        document_id: str | None = None,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        anchor: str = "center",
        strategy: str = "NearestNeighbor",
        angle_degrees: float | None = None,
        direction: str | None = None,
        path: str | None = None,
        name: str | None = None,
        parent_id: str | None = None,
        above_id: str | None = None,
    ) -> dict[str, Any]:
        if action not in CANVAS_ACTIONS:
            raise self._input_error(
                "action must be read, sample_color, resize, scale, crop, rotate, flip, "
                "or import_layer"
            )
        arguments: dict[str, Any] = {"action": action}
        self._add_document_id(arguments, document_id)
        if action == "sample_color":
            arguments["x"] = self._required_coordinate(x, "x", minimum=0)
            arguments["y"] = self._required_coordinate(y, "y", minimum=0)
        elif action in {"resize", "scale"}:
            normalized_width, normalized_height = self._bounded_size(width, height)
            arguments.update({"width": normalized_width, "height": normalized_height})
            if action == "resize":
                if anchor not in CANVAS_ANCHORS:
                    raise self._input_error("anchor is not a supported canvas anchor")
                arguments["anchor"] = anchor
            else:
                arguments["strategy"] = self._required_text_value(
                    strategy, "strategy", maximum=100
                )
        elif action == "crop":
            normalized_width, normalized_height = self._bounded_size(width, height)
            arguments.update(
                {
                    "x": self._required_coordinate(x, "x", minimum=0),
                    "y": self._required_coordinate(y, "y", minimum=0),
                    "width": normalized_width,
                    "height": normalized_height,
                }
            )
        elif action == "rotate":
            angle = self._finite_value(angle_degrees, "angle_degrees", -360.0, 360.0)
            if angle == 0:
                raise self._input_error("angle_degrees must be non-zero")
            arguments["angle_degrees"] = angle
        elif action == "flip":
            if direction not in {"horizontal", "vertical"}:
                raise self._input_error("direction must be horizontal or vertical")
            arguments["direction"] = direction
        elif action == "import_layer":
            raw_path = self._required_text_value(path, "path", maximum=32_767)
            try:
                validated = self.paths.validate_input(
                    raw_path, extensions=RASTER_INPUT_EXTENSIONS
                )
            except PathPolicyError as exc:
                raise self._input_error(str(exc)) from exc
            file_size = validated.stat().st_size
            if file_size > self.config.asset_max_bytes:
                raise self._input_error(
                    f"input image exceeds the configured {self.config.asset_max_bytes} byte limit"
                )
            arguments.update(
                {
                    "path": str(validated),
                    "name": self._required_text_value(
                        name if name is not None else Path(validated).stem,
                        "name",
                        maximum=200,
                    ),
                    "x": self._required_coordinate(x if x is not None else 0, "x"),
                    "y": self._required_coordinate(y if y is not None else 0, "y"),
                }
            )
            if parent_id is not None:
                arguments["parent_id"] = self._required_text_value(
                    parent_id, "parent_id", maximum=80
                )
            if above_id is not None:
                arguments["above_id"] = self._required_text_value(
                    above_id, "above_id", maximum=80
                )
        return await self.client.execute("canvas", arguments)

    async def edit(
        self,
        *,
        action: str,
        document_id: str | None = None,
        layer_id: str | None = None,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        combine: str = "replace",
        amount: int | None = None,
        color: str | None = None,
    ) -> dict[str, Any]:
        if action not in EDIT_ACTIONS:
            raise self._input_error(
                "action must be selection_status, select_rect, select_all, select_opaque, "
                "deselect, invert, grow, shrink, fill, or clear"
            )
        arguments: dict[str, Any] = {"action": action}
        self._add_document_id(arguments, document_id)
        if action == "select_rect":
            normalized_width, normalized_height = self._bounded_size(width, height)
            arguments.update(
                {
                    "x": self._required_coordinate(x, "x"),
                    "y": self._required_coordinate(y, "y"),
                    "width": normalized_width,
                    "height": normalized_height,
                    "combine": self._combine_mode(combine),
                }
            )
        elif action == "select_all":
            arguments["combine"] = self._combine_mode(combine)
        elif action == "select_opaque":
            arguments["layer_id"] = self._required_text_value(
                layer_id, "layer_id", maximum=80
            )
            arguments["combine"] = self._combine_mode(combine)
        elif action in {"grow", "shrink"}:
            arguments["amount"] = self._bounded_integer(amount, "amount", 1, 4096)
        elif action in {"fill", "clear"}:
            arguments["layer_id"] = self._required_text_value(
                layer_id, "layer_id", maximum=80
            )
            if action == "fill" and color is not None:
                arguments["color"] = self._hex_value(color, "color")
        return await self.client.execute("edit", arguments)

    def _add_document_id(
        self, arguments: dict[str, Any], document_id: str | None
    ) -> None:
        if document_id is not None:
            arguments["document_id"] = self._required_text_value(
                document_id, "document_id", maximum=80
            )

    def _bounded_size(self, width: int | None, height: int | None) -> tuple[int, int]:
        normalized_width = self._bounded_integer(
            width, "width", 1, self.config.document_max_dimension
        )
        normalized_height = self._bounded_integer(
            height, "height", 1, self.config.document_max_dimension
        )
        if normalized_width * normalized_height > self.config.document_max_pixels:
            raise self._input_error(
                f"dimensions exceed the configured {self.config.document_max_pixels} pixel limit"
            )
        return normalized_width, normalized_height

    def _combine_mode(self, combine: str) -> str:
        if combine not in SELECTION_COMBINE_MODES:
            raise self._input_error("combine must be replace, add, subtract, or intersect")
        return combine

    def _required_coordinate(
        self, value: int | None, field: str, *, minimum: int = -1_000_000
    ) -> int:
        return self._bounded_integer(value, field, minimum, 1_000_000)

    @staticmethod
    def _input_error(message: str) -> ValueError:
        return InputError(message)

    @staticmethod
    def _bounded_integer(value: Any, field: str, minimum: int, maximum: int) -> int:
        return bounded_int(value, field, minimum, maximum)

    @staticmethod
    def _finite_value(value: Any, field: str, minimum: float, maximum: float) -> float:
        return finite_number(value, field, minimum, maximum)

    @staticmethod
    def _required_text_value(value: Any, field: str, *, maximum: int) -> str:
        return required_text(value, field, maximum=maximum)

    @staticmethod
    def _hex_value(value: Any, field: str) -> str:
        try:
            return hex_color(value)
        except ValueError as exc:
            if field != "foreground":
                message = str(exc).replace("foreground", field)
                raise EditingServiceMixin._input_error(message) from exc
            raise
