from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from krita import Krita, ManagedColor, Palette
from PyQt5.QtCore import QByteArray

from ._service_helpers import (
    _bounded_int,
    _parse_qcolor,
    _required_text,
)
from .errors import KritaOperationError

_SAFE_FILTERS = {
    "autocontrast",
    "brightnesscontrast",
    "desaturate",
    "hsvadjustment",
    "invert",
    "levels",
    "normalize",
}
_REPLACE_MAX_PIXELS = 4_194_304


class ColorOperationsMixin:
    _PROJECT_PALETTE_ANNOTATION = "krita-codex-project-palette"
    _PROJECT_PALETTE_MAX_BYTES = 65_536

    def color(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        action = arguments.get("action")
        supported = {
            "read",
            "list_palettes",
            "read_palette",
            "set_project_palette",
            "apply_swatch",
            "replace_color",
            "adjust",
        }
        if action not in supported:
            raise KritaOperationError("INVALID_ARGUMENT", f"Unsupported color action: {action}")
        doc = self._target_document(arguments, activate=action != "list_palettes")
        if action == "read":
            return self._color_state(doc)
        if action == "list_palettes":
            return self._list_palettes(arguments, doc)
        if action == "read_palette":
            return self._read_palette(arguments, doc)
        if action == "set_project_palette":
            return self._set_project_palette(arguments, doc)
        if action == "apply_swatch":
            return self._apply_swatch(arguments, doc)
        if action == "replace_color":
            return self._replace_color(arguments, doc)
        return self._adjust(arguments, doc)

    def _color_state(self, doc) -> dict[str, Any]:
        view = self._active_view()
        configurations = {}
        app = Krita.instance()
        for name in sorted(_SAFE_FILTERS):
            filter_object = app.filter(name)
            if filter_object is None:
                continue
            configuration = filter_object.configuration()
            configurations[name] = self._json_properties(
                configuration.properties() if configuration is not None else {}
            )
        return {
            "action": "read",
            "document_id": self._document_id(doc),
            "project_palette": self._project_palette(doc),
            "foreground": self._managed_color(view.foregroundColor(), view),
            "background": self._managed_color(view.backgroundColor(), view),
            "safe_filters": configurations,
        }

    def _list_palettes(self, arguments, doc) -> dict[str, Any]:
        limit = _bounded_int(arguments.get("limit", 100), "limit", 1, 500)
        resources = Krita.instance().resources("palette")
        names = sorted(
            {resource.name() for resource in resources.values()}, key=str.casefold
        )
        return {
            "action": "list_palettes",
            "document_id": self._document_id(doc),
            "palettes": names[:limit],
            "total": len(names),
            "truncated": len(names) > limit,
            "project_palette_available": self._project_palette(doc) is not None,
        }

    def _read_palette(self, arguments, doc) -> dict[str, Any]:
        name = _required_text(arguments.get("palette_name"), "palette_name", 300)
        limit = _bounded_int(arguments.get("limit", 100), "limit", 1, 500)
        if name.casefold() == "project":
            project = self._project_palette(doc)
            if project is None:
                raise KritaOperationError("PALETTE_NOT_FOUND", "No project palette is stored")
            entries = project["entries"]
            return {
                "action": "read_palette",
                "document_id": self._document_id(doc),
                "palette": {**project, "entries": entries[:limit]},
                "total": len(entries),
                "truncated": len(entries) > limit,
                "source": "document_annotation",
            }
        resource = self._palette_resource(name)
        palette = Palette(resource)
        count = int(palette.numberOfEntries())
        view = self._active_view()
        entries = []
        for index in range(min(count, limit)):
            swatch = self._palette_entry(palette, index)
            if swatch is None or not swatch.isValid():
                continue
            entries.append(
                {
                    "index": index,
                    "name": swatch.name(),
                    "id": swatch.id(),
                    "spot_color": bool(swatch.spotColor()),
                    "color": self._managed_color(swatch.color(), view),
                }
            )
        return {
            "action": "read_palette",
            "document_id": self._document_id(doc),
            "palette": {
                "name": resource.name(),
                "comment": palette.comment(),
                "columns": int(palette.columnCount()),
                "groups": list(palette.groupNames()),
                "entries": entries,
            },
            "total": count,
            "truncated": count > limit,
            "source": "krita_resource",
        }

    def _set_project_palette(self, arguments, doc) -> dict[str, Any]:
        name = _required_text(arguments.get("project_name"), "project_name", 200)
        raw_entries = arguments.get("entries")
        if not isinstance(raw_entries, list) or not 1 <= len(raw_entries) <= 256:
            raise KritaOperationError(
                "INVALID_ARGUMENT", "entries must contain between 1 and 256 colors"
            )
        entries = []
        seen = set()
        for index, raw in enumerate(raw_entries):
            if not isinstance(raw, dict):
                raise KritaOperationError("INVALID_ARGUMENT", f"entries[{index}] must be an object")
            entry_name = _required_text(raw.get("name"), f"entries[{index}].name", 100)
            key = entry_name.casefold()
            if key in seen:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "Project palette entry names must be unique"
                )
            seen.add(key)
            color_text = _required_text(raw.get("color"), f"entries[{index}].color", 9)
            color = _parse_qcolor(color_text)
            entries.append({"name": entry_name, "color": self._qcolor_hex(color)})
        palette = {"name": name, "entries": entries}
        encoded = json.dumps(palette, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self._PROJECT_PALETTE_MAX_BYTES:
            raise KritaOperationError(
                "PROJECT_PALETTE_TOO_LARGE",
                "Project palette exceeds the bounded annotation size",
                {"bytes": len(encoded), "limit": self._PROJECT_PALETTE_MAX_BYTES},
            )
        doc.setAnnotation(
            self._PROJECT_PALETTE_ANNOTATION,
            "Krita Codex project palette",
            QByteArray(encoded),
        )
        doc.setModified(True)
        self.bridge_revision += 1
        return {
            "action": "set_project_palette",
            "document_id": self._document_id(doc),
            "project_palette": palette,
            "stored_in_kra": True,
        }

    def _apply_swatch(self, arguments, doc) -> dict[str, Any]:
        name = _required_text(arguments.get("palette_name"), "palette_name", 300)
        index = _bounded_int(arguments.get("swatch_index"), "swatch_index", 0, 4999)
        target = arguments.get("target", "foreground")
        if target not in {"foreground", "background"}:
            raise KritaOperationError("INVALID_ARGUMENT", "target must be foreground or background")
        view = self._active_view()
        if name.casefold() == "project":
            project = self._project_palette(doc)
            if project is None or index >= len(project["entries"]):
                raise KritaOperationError("SWATCH_NOT_FOUND", "Project swatch index was not found")
            entry = project["entries"][index]
            managed = ManagedColor.fromQColor(_parse_qcolor(entry["color"]), view.canvas())
            entry_name = entry["name"]
            source = "document_annotation"
        else:
            palette = Palette(self._palette_resource(name))
            if index >= int(palette.numberOfEntries()):
                raise KritaOperationError("SWATCH_NOT_FOUND", "Krita swatch index was not found")
            swatch = self._palette_entry(palette, index)
            if swatch is None or not swatch.isValid():
                raise KritaOperationError("SWATCH_NOT_FOUND", "Krita swatch index is empty")
            managed = swatch.color()
            entry_name = swatch.name()
            source = "krita_resource"
        if managed is None:
            raise KritaOperationError("COLOR_CONVERSION_FAILED", "Swatch has no usable color")
        if target == "foreground":
            view.setForeGroundColor(managed)
        else:
            view.setBackGroundColor(managed)
        self.bridge_revision += 1
        return {
            "action": "apply_swatch",
            "document_id": self._document_id(doc),
            "palette_name": name,
            "swatch_index": index,
            "swatch_name": entry_name,
            "target": target,
            "color": self._managed_color(managed, view),
            "source": source,
        }

    def _palette_resource(self, name: str):
        resources = Krita.instance().resources("palette")
        direct = resources.get(name)
        if direct is not None:
            return direct
        target = name.casefold()
        for resource in resources.values():
            if resource.name().casefold() == target:
                return resource
        raise KritaOperationError(
            "PALETTE_NOT_FOUND", "Krita palette was not found", {"name": name}
        )

    @staticmethod
    def _palette_entry(palette, index: int):
        modern = getattr(palette, "entryByIndex", None)
        if modern is not None:
            entry = modern(index)
            if entry is not None:
                return entry
        return palette.colorSetEntryByIndex(index)

    def _project_palette(self, doc):
        try:
            raw = bytes(doc.annotation(self._PROJECT_PALETTE_ANNOTATION))
            if not raw or len(raw) > self._PROJECT_PALETTE_MAX_BYTES:
                return None
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                return None
            name = parsed.get("name")
            raw_entries = parsed.get("entries")
            if (
                not isinstance(name, str)
                or not name.strip()
                or len(name.strip()) > 200
                or not isinstance(raw_entries, list)
                or not 1 <= len(raw_entries) <= 256
            ):
                return None
            entries = []
            seen = set()
            for raw_entry in raw_entries:
                if not isinstance(raw_entry, dict):
                    return None
                entry_name = raw_entry.get("name")
                color_text = raw_entry.get("color")
                if (
                    not isinstance(entry_name, str)
                    or not entry_name.strip()
                    or len(entry_name.strip()) > 100
                    or entry_name.strip().casefold() in seen
                ):
                    return None
                color = _parse_qcolor(color_text)
                normalized_name = entry_name.strip()
                seen.add(normalized_name.casefold())
                entries.append(
                    {"name": normalized_name, "color": self._qcolor_hex(color)}
                )
            return {"name": name.strip(), "entries": entries}
        except Exception:
            return None

    @staticmethod
    def _qcolor_hex(color) -> str:
        return f"#{color.red():02X}{color.green():02X}{color.blue():02X}{color.alpha():02X}"

    @staticmethod
    def _json_properties(properties) -> dict[str, Any]:
        result = {}
        for key, value in properties.items():
            if isinstance(value, (bool, int, float, str)) or value is None:
                result[str(key)] = value
            else:
                result[str(key)] = {"type": type(value).__name__}
        return result
