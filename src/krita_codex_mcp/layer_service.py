from __future__ import annotations

from typing import Any

from .transform_checkpoint import checkpoint_reopen_transform
from .validation import InputError, bounded_int, finite_number, required_text

SAFE_LAYER_BLENDING_MODES = {
    "add",
    "color",
    "darken",
    "erase",
    "hard_light",
    "hue",
    "lighten",
    "luminize",
    "multiply",
    "normal",
    "overlay",
    "saturation",
    "screen",
    "soft_light",
    "subtract",
}


class LayerServiceMixin:
    async def layers(
        self,
        *,
        action: str,
        document_id: str | None = None,
        layer_id: str | None = None,
        name: str | None = None,
        kind: str = "paintlayer",
        parent_id: str | None = None,
        above_id: str | None = None,
        visible: bool | None = None,
        opacity: float | None = None,
        locked: bool | None = None,
        alpha_locked: bool | None = None,
        inherit_alpha: bool | None = None,
        blending_mode: str | None = None,
        offset_x: int | None = None,
        offset_y: int | None = None,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        strategy: str = "NearestNeighbor",
        angle_degrees: float | None = None,
        shear_x_degrees: float | None = None,
        shear_y_degrees: float | None = None,
        direction: str | None = None,
        transform_mode: str = "mask",
        response_mode: str = "compact",
        checkpoint_reopen: bool = False,
    ) -> dict[str, Any]:
        actions = {
            "read",
            "create",
            "activate",
            "rename",
            "set_properties",
            "duplicate",
            "delete",
            "reorder",
            "translate",
            "scale",
            "rotate",
            "shear",
            "crop",
            "flip",
            "merge_down",
        }
        if action not in actions:
            raise InputError(
                "action must be read, create, activate, rename, set_properties, duplicate, "
                "delete, reorder, translate, scale, rotate, shear, crop, flip, or merge_down"
            )
        arguments: dict[str, Any] = {"action": action}
        if response_mode not in {"compact", "full"}:
            raise InputError("response_mode must be compact or full")
        if action != "read" and response_mode == "full":
            arguments["response_mode"] = "full"
        if not isinstance(checkpoint_reopen, bool):
            raise InputError("checkpoint_reopen must be a boolean")
        if checkpoint_reopen:
            if action not in {"scale", "rotate", "shear", "crop"}:
                raise InputError(
                    "checkpoint_reopen is only supported for destructive transforms"
                )
            if action in {"scale", "rotate", "shear"} and transform_mode != "direct":
                raise InputError("checkpoint_reopen requires transform_mode=direct")
            if document_id is None:
                raise InputError("checkpoint_reopen requires document_id")
        if document_id is not None:
            arguments["document_id"] = required_text(
                document_id, "document_id", maximum=80
            )
        if action in {
            "activate",
            "rename",
            "set_properties",
            "duplicate",
            "delete",
            "reorder",
            "translate",
            "scale",
            "rotate",
            "shear",
            "crop",
            "flip",
            "merge_down",
        }:
            arguments["layer_id"] = required_text(layer_id, "layer_id", maximum=80)
        if action == "create":
            arguments["name"] = required_text(name, "name", maximum=200)
            if kind not in {"paintlayer", "grouplayer"}:
                raise InputError("kind must be paintlayer or grouplayer")
            arguments["kind"] = kind
            if parent_id is not None:
                arguments["parent_id"] = required_text(
                    parent_id, "parent_id", maximum=80
                )
        elif action == "rename":
            arguments["name"] = required_text(name, "name", maximum=200)
        elif action == "duplicate":
            if name is not None:
                arguments["name"] = required_text(name, "name", maximum=200)
        elif action == "reorder":
            if parent_id is not None:
                arguments["parent_id"] = required_text(
                    parent_id, "parent_id", maximum=80
                )
            if above_id is not None:
                arguments["above_id"] = required_text(above_id, "above_id", maximum=80)
        elif action == "translate":
            if offset_x is None and offset_y is None:
                raise InputError("translate requires offset_x and/or offset_y")
            normalized_x = bounded_int(
                offset_x or 0, "offset_x", -1_000_000, 1_000_000
            )
            normalized_y = bounded_int(
                offset_y or 0, "offset_y", -1_000_000, 1_000_000
            )
            if normalized_x == 0 and normalized_y == 0:
                raise InputError("translate requires a non-zero offset")
            arguments["offset_x"] = normalized_x
            arguments["offset_y"] = normalized_y
        elif action == "scale":
            normalized_width = bounded_int(width, "width", 1, 32768)
            normalized_height = bounded_int(height, "height", 1, 32768)
            arguments.update(
                {
                    "width": normalized_width,
                    "height": normalized_height,
                    "strategy": required_text(strategy, "strategy", maximum=100),
                }
            )
            if x is not None:
                arguments["x"] = bounded_int(x, "x", -1_000_000, 1_000_000)
            if y is not None:
                arguments["y"] = bounded_int(y, "y", -1_000_000, 1_000_000)
            arguments["transform_mode"] = self._normalize_transform_mode(transform_mode)
        elif action == "rotate":
            angle = finite_number(angle_degrees, "angle_degrees", -360.0, 360.0)
            if angle == 0:
                raise InputError("angle_degrees must be non-zero")
            arguments["angle_degrees"] = angle
            arguments["transform_mode"] = self._normalize_transform_mode(transform_mode)
        elif action == "shear":
            angle_x = finite_number(
                shear_x_degrees if shear_x_degrees is not None else 0.0,
                "shear_x_degrees",
                -89.0,
                89.0,
            )
            angle_y = finite_number(
                shear_y_degrees if shear_y_degrees is not None else 0.0,
                "shear_y_degrees",
                -89.0,
                89.0,
            )
            if angle_x == 0 and angle_y == 0:
                raise InputError("shear requires a non-zero angle")
            arguments.update(
                {"shear_x_degrees": angle_x, "shear_y_degrees": angle_y}
            )
            arguments["transform_mode"] = self._normalize_transform_mode(transform_mode)
        elif action == "crop":
            crop_width = bounded_int(width, "width", 1, self.config.document_max_dimension)
            crop_height = bounded_int(
                height, "height", 1, self.config.document_max_dimension
            )
            if crop_width * crop_height > self.config.document_max_pixels:
                raise InputError("cropped layer exceeds the configured pixel limit")
            arguments.update(
                {
                    "x": bounded_int(x, "x", -1_000_000, 1_000_000),
                    "y": bounded_int(y, "y", -1_000_000, 1_000_000),
                    "width": crop_width,
                    "height": crop_height,
                }
            )
        elif action == "flip":
            if direction not in {"horizontal", "vertical"}:
                raise InputError("direction must be horizontal or vertical")
            arguments["direction"] = direction
        elif action == "set_properties":
            properties = {
                "visible": visible,
                "opacity": opacity,
                "locked": locked,
                "alpha_locked": alpha_locked,
                "inherit_alpha": inherit_alpha,
                "blending_mode": blending_mode,
            }
            if all(value is None for value in properties.values()):
                raise InputError("set_properties requires at least one layer property")
            if visible is not None:
                if not isinstance(visible, bool):
                    raise InputError("visible must be a boolean")
                arguments["visible"] = visible
            if opacity is not None:
                if isinstance(opacity, bool) or not isinstance(opacity, (int, float)):
                    raise InputError("opacity must be a number between 0 and 1")
                if not 0.0 <= float(opacity) <= 1.0:
                    raise InputError("opacity must be a number between 0 and 1")
                arguments["opacity"] = float(opacity)
            for field, value in (
                ("locked", locked),
                ("alpha_locked", alpha_locked),
                ("inherit_alpha", inherit_alpha),
            ):
                if value is not None:
                    if not isinstance(value, bool):
                        raise InputError(f"{field} must be a boolean")
                    arguments[field] = value
            if blending_mode is not None:
                normalized_mode = required_text(
                    blending_mode, "blending_mode", maximum=100
                )
                if normalized_mode not in SAFE_LAYER_BLENDING_MODES:
                    raise InputError(
                        "blending_mode must be one of: "
                        + ", ".join(sorted(SAFE_LAYER_BLENDING_MODES))
                    )
                arguments["blending_mode"] = normalized_mode
        if checkpoint_reopen:
            return await checkpoint_reopen_transform(self.client, self.paths, arguments)
        return await self.client.execute("layers", arguments)

    @staticmethod
    def _normalize_transform_mode(value: str) -> str:
        if value not in {"mask", "direct"}:
            raise InputError("transform_mode must be mask or direct")
        return value
