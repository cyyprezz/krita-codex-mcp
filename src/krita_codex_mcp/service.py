from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from krita_codex_protocol import (
    KRITA_DOCUMENT_EXTENSIONS,
    BridgeConfig,
    PathPolicy,
    PathPolicyError,
)

from .bridge_client import BridgeCallError


class BridgeExecutor(Protocol):
    async def execute(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]: ...


class InputError(ValueError):
    pass


class KritaService:
    def __init__(self, config: BridgeConfig, client: BridgeExecutor) -> None:
        self.config = config
        self.client = client
        self.paths = PathPolicy(config.allowed_roots)

    async def state(self) -> dict[str, Any]:
        return await self.client.execute("state", {})

    async def preview(self, max_width: int, max_height: int) -> tuple[dict[str, Any], str]:
        width = _bounded_int(max_width, "max_width", 64, self.config.preview_max_dimension)
        height = _bounded_int(max_height, "max_height", 64, self.config.preview_max_dimension)
        result = await self.client.execute("preview", {"max_width": width, "max_height": height})
        png_base64 = result.pop("png_base64", None)
        if not isinstance(png_base64, str):
            raise BridgeCallError("INVALID_PREVIEW", "bridge response contains no PNG image", {})
        try:
            png_size = len(base64.b64decode(png_base64, validate=True))
        except ValueError as exc:
            raise BridgeCallError(
                "INVALID_PREVIEW", "bridge returned malformed PNG base64", {}
            ) from exc
        if png_size > self.config.preview_max_bytes:
            raise BridgeCallError("PREVIEW_TOO_LARGE", "preview exceeds configured byte limit", {})
        return result, png_base64

    async def document(
        self,
        *,
        action: str,
        path: str | None = None,
        document_id: str | None = None,
        width: int = 1920,
        height: int = 1080,
        name: str = "Codex Document",
        color_model: str = "RGBA",
        color_depth: str = "U8",
        profile: str = "",
        resolution: float = 300.0,
        discard_changes: bool = False,
    ) -> dict[str, Any]:
        if action not in {"list", "create", "open", "activate", "close"}:
            raise InputError("action must be list, create, open, activate, or close")
        arguments: dict[str, Any] = {"action": action}
        if action == "create":
            normalized_width = _bounded_int(width, "width", 1, self.config.document_max_dimension)
            normalized_height = _bounded_int(
                height, "height", 1, self.config.document_max_dimension
            )
            if normalized_width * normalized_height > self.config.document_max_pixels:
                raise InputError(
                    f"document exceeds the configured {self.config.document_max_pixels} pixel limit"
                )
            arguments.update(
                {
                    "width": normalized_width,
                    "height": normalized_height,
                    "name": _required_text(name, "name", maximum=200),
                    "color_model": _required_text(color_model, "color_model", maximum=32),
                    "color_depth": _required_text(color_depth, "color_depth", maximum=32),
                    "profile": _optional_text(profile, "profile", maximum=300),
                    "resolution": _finite_number(resolution, "resolution", 1.0, 2400.0),
                }
            )
        elif action == "open":
            try:
                validated = self.paths.validate_input(
                    _required_text(path, "path", maximum=32_767),
                    extensions=set(KRITA_DOCUMENT_EXTENSIONS),
                )
            except PathPolicyError as exc:
                raise InputError(str(exc)) from exc
            arguments["path"] = str(validated)
        elif action in {"activate", "close"}:
            arguments["document_id"] = _required_text(document_id, "document_id", maximum=80)
            if action == "close":
                if not isinstance(discard_changes, bool):
                    raise InputError("discard_changes must be a boolean")
                arguments["discard_changes"] = discard_changes
        return await self.client.execute("document", arguments)

    async def layers(
        self,
        *,
        action: str,
        layer_id: str | None = None,
        name: str | None = None,
        kind: str = "paintlayer",
        parent_id: str | None = None,
        visible: bool | None = None,
        opacity: float | None = None,
    ) -> dict[str, Any]:
        if action not in {"read", "create", "activate", "rename", "set_properties"}:
            raise InputError("action must be read, create, activate, rename, or set_properties")
        arguments: dict[str, Any] = {"action": action}
        if action in {"activate", "rename", "set_properties"}:
            arguments["layer_id"] = _required_text(layer_id, "layer_id", maximum=80)
        if action == "create":
            arguments["name"] = _required_text(name, "name", maximum=200)
            if kind not in {"paintlayer", "grouplayer"}:
                raise InputError("kind must be paintlayer or grouplayer")
            arguments["kind"] = kind
            if parent_id is not None:
                arguments["parent_id"] = _required_text(parent_id, "parent_id", maximum=80)
        elif action == "rename":
            arguments["name"] = _required_text(name, "name", maximum=200)
        elif action == "set_properties":
            if visible is None and opacity is None:
                raise InputError("set_properties requires visible and/or opacity")
            if visible is not None:
                arguments["visible"] = visible
            if opacity is not None:
                if isinstance(opacity, bool) or not isinstance(opacity, (int, float)):
                    raise InputError("opacity must be a number between 0 and 1")
                if not 0.0 <= float(opacity) <= 1.0:
                    raise InputError("opacity must be a number between 0 and 1")
                arguments["opacity"] = float(opacity)
        return await self.client.execute("layers", arguments)

    async def history(self, *, action: str, steps: int = 1) -> dict[str, Any]:
        if action not in {"status", "undo", "redo"}:
            raise InputError("action must be status, undo, or redo")
        arguments: dict[str, Any] = {"action": action}
        if action != "status":
            arguments["steps"] = _bounded_int(steps, "steps", 1, 100)
        return await self.client.execute("history", arguments)

    async def brush(
        self,
        *,
        action: str,
        query: str | None = None,
        limit: int = 100,
        preset_name: str | None = None,
        brush_size: float | None = None,
        opacity: float | None = None,
        flow: float | None = None,
        rotation: float | None = None,
        eraser_mode: bool | None = None,
        disable_pressure: bool | None = None,
        foreground: str | None = None,
    ) -> dict[str, Any]:
        if action not in {"read", "list_presets", "set"}:
            raise InputError("action must be read, list_presets, or set")
        arguments: dict[str, Any] = {"action": action}
        if action == "list_presets":
            if query is not None:
                arguments["query"] = _optional_text(query, "query", maximum=200)
            arguments["limit"] = _bounded_int(limit, "limit", 1, 500)
        elif action == "set":
            if all(
                value is None
                for value in (
                    preset_name,
                    brush_size,
                    opacity,
                    flow,
                    rotation,
                    eraser_mode,
                    disable_pressure,
                    foreground,
                )
            ):
                raise InputError("set requires at least one brush property")
            if preset_name is not None:
                arguments["preset_name"] = _required_text(preset_name, "preset_name", maximum=300)
            if brush_size is not None:
                arguments["brush_size"] = _finite_number(brush_size, "brush_size", 0.1, 1000.0)
            if opacity is not None:
                arguments["opacity"] = _finite_number(opacity, "opacity", 0.0, 1.0)
            if flow is not None:
                arguments["flow"] = _finite_number(flow, "flow", 0.0, 1.0)
            if rotation is not None:
                arguments["rotation"] = _finite_number(rotation, "rotation", -3600.0, 3600.0)
            if eraser_mode is not None:
                if not isinstance(eraser_mode, bool):
                    raise InputError("eraser_mode must be a boolean")
                arguments["eraser_mode"] = eraser_mode
            if disable_pressure is not None:
                if not isinstance(disable_pressure, bool):
                    raise InputError("disable_pressure must be a boolean")
                arguments["disable_pressure"] = disable_pressure
            if foreground is not None:
                arguments["foreground"] = _hex_color(foreground)
        return await self.client.execute("brush", arguments)

    async def paint_path(
        self,
        *,
        points: list[dict[str, Any]],
        layer_id: str | None = None,
        preset_name: str | None = None,
        brush_size: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(points, list) or not 2 <= len(points) <= 512:
            raise InputError("points must contain between 2 and 512 entries")
        normalized: list[dict[str, float]] = []
        for index, point in enumerate(points):
            if not isinstance(point, dict):
                raise InputError(f"points[{index}] must be an object")
            x = _finite_number(point.get("x"), f"points[{index}].x", -1_000_000, 1_000_000)
            y = _finite_number(point.get("y"), f"points[{index}].y", -1_000_000, 1_000_000)
            pressure = _finite_number(point.get("pressure", 1.0), f"points[{index}].pressure", 0, 1)
            normalized.append({"x": x, "y": y, "pressure": pressure})
        arguments: dict[str, Any] = {"points": normalized}
        if layer_id is not None:
            arguments["layer_id"] = _required_text(layer_id, "layer_id", maximum=80)
        if preset_name is not None:
            arguments["preset_name"] = _required_text(preset_name, "preset_name", maximum=300)
        if brush_size is not None:
            arguments["brush_size"] = _finite_number(brush_size, "brush_size", 0.1, 1000)
        return await self.client.execute("paint_path", arguments)

    async def save_export(
        self,
        *,
        kra_path: str | None,
        png_path: str | None,
        overwrite: bool,
    ) -> dict[str, Any]:
        if kra_path is None and png_path is None:
            raise InputError("provide kra_path and/or png_path")
        arguments: dict[str, Any] = {"overwrite": overwrite}
        try:
            if kra_path is not None:
                arguments["kra_path"] = str(
                    self.paths.validate_output(kra_path, extensions={".kra"}, overwrite=overwrite)
                )
            if png_path is not None:
                arguments["png_path"] = str(
                    self.paths.validate_output(png_path, extensions={".png"}, overwrite=overwrite)
                )
        except PathPolicyError as exc:
            raise InputError(str(exc)) from exc
        return await self.client.execute("save_export", arguments)


def _required_text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise InputError(f"{field} must not exceed {maximum} characters")
    return result


def _optional_text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise InputError(f"{field} must be a string")
    result = value.strip()
    if len(result) > maximum:
        raise InputError(f"{field} must not exceed {maximum} characters")
    return result


def _hex_color(value: Any) -> str:
    if not isinstance(value, str) or len(value) not in {7, 9} or not value.startswith("#"):
        raise InputError("foreground must be #RRGGBB or #RRGGBBAA")
    try:
        int(value[1:], 16)
    except ValueError as exc:
        raise InputError("foreground must be #RRGGBB or #RRGGBBAA") from exc
    return value.upper()


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InputError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _finite_number(value: Any, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{field} must be a number between {minimum} and {maximum}")
    result = float(value)
    if result != result or result in {float("inf"), float("-inf")}:
        raise InputError(f"{field} must be finite")
    if not minimum <= result <= maximum:
        raise InputError(f"{field} must be a number between {minimum} and {maximum}")
    return result


def display_path(path: Path) -> str:
    return str(path)
