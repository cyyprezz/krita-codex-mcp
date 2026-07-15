from __future__ import annotations

from typing import Any

from .validation import InputError, bounded_int, required_text

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
            "merge_down",
        }
        if action not in actions:
            raise InputError(
                "action must be read, create, activate, rename, set_properties, duplicate, "
                "delete, reorder, translate, or merge_down"
            )
        arguments: dict[str, Any] = {"action": action}
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
        return await self.client.execute("layers", arguments)
