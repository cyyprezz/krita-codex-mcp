from __future__ import annotations

import json
from typing import Any

from .validation import InputError, bounded_int, finite_number, hex_color, required_text

COLOR_ACTIONS = {
    "read",
    "list_palettes",
    "read_palette",
    "set_project_palette",
    "apply_swatch",
    "replace_color",
    "adjust",
}
SAFE_FILTERS = {
    "autocontrast",
    "brightnesscontrast",
    "desaturate",
    "hsvadjustment",
    "invert",
    "levels",
    "normalize",
}
PROJECT_PALETTE_MAX_BYTES = 65_536


class ColorServiceMixin:
    async def color(
        self,
        *,
        action: str,
        document_id: str | None = None,
        layer_id: str | None = None,
        palette_name: str | None = None,
        swatch_index: int | None = None,
        target: str = "foreground",
        project_name: str | None = None,
        entries: list[dict[str, Any]] | None = None,
        from_color: str | None = None,
        to_color: str | None = None,
        tolerance: int = 0,
        filter_name: str | None = None,
        settings: dict[str, Any] | None = None,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if action not in COLOR_ACTIONS:
            raise InputError(
                "action must be read, list_palettes, read_palette, set_project_palette, "
                "apply_swatch, replace_color, or adjust"
            )
        arguments: dict[str, Any] = {"action": action}
        if document_id is not None:
            arguments["document_id"] = required_text(
                document_id, "document_id", maximum=80
            )
        if action in {"replace_color", "adjust"}:
            arguments["layer_id"] = required_text(layer_id, "layer_id", maximum=80)
        if action == "list_palettes":
            arguments["limit"] = bounded_int(limit, "limit", 1, 500)
        elif action == "read_palette":
            arguments["palette_name"] = required_text(
                palette_name, "palette_name", maximum=300
            )
            arguments["limit"] = bounded_int(limit, "limit", 1, 500)
        elif action == "set_project_palette":
            normalized_name = required_text(
                project_name or "Project Palette", "project_name", maximum=200
            )
            normalized_entries = self._palette_entries(entries)
            encoded = json.dumps(
                {"name": normalized_name, "entries": normalized_entries},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > PROJECT_PALETTE_MAX_BYTES:
                raise InputError("project palette exceeds the 65536-byte storage limit")
            arguments["project_name"] = normalized_name
            arguments["entries"] = normalized_entries
        elif action == "apply_swatch":
            arguments["palette_name"] = required_text(
                palette_name, "palette_name", maximum=300
            )
            arguments["swatch_index"] = bounded_int(
                swatch_index, "swatch_index", 0, 4999
            )
            if target not in {"foreground", "background"}:
                raise InputError("target must be foreground or background")
            arguments["target"] = target
        elif action == "replace_color":
            arguments.update(
                {
                    "from_color": hex_color(from_color),
                    "to_color": hex_color(to_color),
                    "tolerance": bounded_int(tolerance, "tolerance", 0, 255),
                }
            )
        elif action == "adjust":
            normalized_filter = required_text(filter_name, "filter_name", maximum=100)
            if normalized_filter not in SAFE_FILTERS:
                raise InputError(
                    "filter_name must be one of: " + ", ".join(sorted(SAFE_FILTERS))
                )
            arguments["filter_name"] = normalized_filter
            arguments["settings"] = self._filter_settings(settings or {})
            region = (x, y, width, height)
            if any(value is not None for value in region):
                if not all(value is not None for value in region):
                    raise InputError("adjust region requires x, y, width, and height")
                arguments["rect"] = {
                    "x": bounded_int(x, "x", -1_000_000, 1_000_000),
                    "y": bounded_int(y, "y", -1_000_000, 1_000_000),
                    "width": bounded_int(width, "width", 1, 32768),
                    "height": bounded_int(height, "height", 1, 32768),
                }
        return await self.client.execute("color", arguments)

    @staticmethod
    def _palette_entries(entries: list[dict[str, Any]] | None) -> list[dict[str, str]]:
        if not isinstance(entries, list) or not 1 <= len(entries) <= 256:
            raise InputError("entries must contain between 1 and 256 colors")
        result: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, raw in enumerate(entries):
            if not isinstance(raw, dict):
                raise InputError(f"entries[{index}] must be an object")
            name = required_text(raw.get("name"), f"entries[{index}].name", maximum=100)
            key = name.casefold()
            if key in seen:
                raise InputError("project palette entry names must be unique")
            seen.add(key)
            result.append({"name": name, "color": hex_color(raw.get("color"))})
        return result

    @staticmethod
    def _filter_settings(settings: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(settings, dict) or len(settings) > 32:
            raise InputError("settings must be an object with at most 32 entries")
        result: dict[str, Any] = {}
        for raw_key, value in settings.items():
            key = required_text(raw_key, "settings key", maximum=100)
            if isinstance(value, bool):
                result[key] = value
            elif isinstance(value, int):
                result[key] = bounded_int(value, f"settings.{key}", -1_000_000, 1_000_000)
            elif isinstance(value, float):
                result[key] = finite_number(
                    value, f"settings.{key}", -1_000_000.0, 1_000_000.0
                )
            elif isinstance(value, str):
                result[key] = required_text(value, f"settings.{key}", maximum=500)
            else:
                raise InputError(f"settings.{key} must be a scalar value")
        return result
