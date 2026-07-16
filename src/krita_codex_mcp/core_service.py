from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from krita_codex_protocol import (
    KRITA_DOCUMENT_EXTENSIONS,
    BridgeConfig,
    PathPolicy,
    PathPolicyError,
)

from .asset_service import AssetServiceMixin
from .bridge_client import BridgeCallError
from .color_service import ColorServiceMixin
from .drawing_service import DrawingServiceMixin
from .editing_service import EditingServiceMixin
from .image_validation import validate_png_base64
from .layer_service import LayerServiceMixin
from .validation import (
    InputError,
    bounded_int,
    finite_number,
    hex_color,
    optional_text,
    required_text,
)


class BridgeExecutor(Protocol):
    async def execute(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]: ...


class KritaService(
    EditingServiceMixin,
    LayerServiceMixin,
    DrawingServiceMixin,
    ColorServiceMixin,
    AssetServiceMixin,
):
    def __init__(self, config: BridgeConfig, client: BridgeExecutor) -> None:
        self.config = config
        self.client = client
        self.paths = PathPolicy(config.allowed_roots)

    async def state(self, *, document_id: str | None = None) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        if document_id is not None:
            arguments["document_id"] = required_text(
                document_id, "document_id", maximum=80
            )
        return await self.client.execute("state", arguments)

    async def preview(
        self,
        max_width: int,
        max_height: int,
        *,
        document_id: str | None = None,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        scale: int = 1,
    ) -> tuple[dict[str, Any], str]:
        output_width = bounded_int(
            max_width, "max_width", 64, self.config.preview_max_dimension
        )
        output_height = bounded_int(
            max_height, "max_height", 64, self.config.preview_max_dimension
        )
        arguments: dict[str, Any] = {
            "max_width": output_width,
            "max_height": output_height,
        }
        region_values = (x, y, width, height)
        if any(value is not None for value in region_values):
            if not all(value is not None for value in region_values):
                raise InputError("preview region requires x, y, width, and height")
            region_width = bounded_int(
                width, "width", 1, self.config.document_max_dimension
            )
            region_height = bounded_int(
                height, "height", 1, self.config.document_max_dimension
            )
            if region_width * region_height > self.config.document_max_pixels:
                raise InputError("preview region exceeds the configured pixel limit")
            arguments["region"] = {
                "x": bounded_int(x, "x", 0, 1_000_000),
                "y": bounded_int(y, "y", 0, 1_000_000),
                "width": region_width,
                "height": region_height,
            }
        elif scale != 1:
            raise InputError("preview scale greater than 1 requires an explicit region")
        arguments["scale"] = bounded_int(scale, "scale", 1, 16)
        if document_id is not None:
            arguments["document_id"] = required_text(
                document_id, "document_id", maximum=80
            )
        result = await self.client.execute("preview", arguments)
        png_base64 = result.pop("png_base64", None)
        png_size = validate_png_base64(
            png_base64,
            missing_message="bridge response contains no PNG image",
            malformed_message="bridge returned malformed PNG base64",
        )
        if png_size > self.config.preview_max_bytes:
            raise BridgeCallError(
                "PREVIEW_TOO_LARGE", "preview exceeds configured byte limit", {}
            )
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
        if action not in {"list", "create", "open", "activate", "save", "close"}:
            raise InputError("action must be list, create, open, activate, save, or close")
        arguments: dict[str, Any] = {"action": action}
        if action == "create":
            normalized_width = bounded_int(
                width, "width", 1, self.config.document_max_dimension
            )
            normalized_height = bounded_int(
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
                    "name": required_text(name, "name", maximum=200),
                    "color_model": required_text(
                        color_model, "color_model", maximum=32
                    ),
                    "color_depth": required_text(
                        color_depth, "color_depth", maximum=32
                    ),
                    "profile": optional_text(profile, "profile", maximum=300),
                    "resolution": finite_number(
                        resolution, "resolution", 1.0, 2400.0
                    ),
                }
            )
        elif action == "open":
            try:
                validated = self.paths.validate_input(
                    required_text(path, "path", maximum=32_767),
                    extensions=set(KRITA_DOCUMENT_EXTENSIONS),
                )
            except PathPolicyError as exc:
                raise InputError(str(exc)) from exc
            arguments["path"] = str(validated)
        elif action in {"activate", "save", "close"}:
            arguments["document_id"] = required_text(
                document_id, "document_id", maximum=80
            )
            if action == "close":
                if not isinstance(discard_changes, bool):
                    raise InputError("discard_changes must be a boolean")
                arguments["discard_changes"] = discard_changes
        return await self.client.execute("document", arguments)

    async def history(
        self, *, action: str, steps: int = 1, document_id: str | None = None
    ) -> dict[str, Any]:
        if action not in {"status", "undo", "redo"}:
            raise InputError("action must be status, undo, or redo")
        arguments: dict[str, Any] = {"action": action}
        if document_id is not None:
            arguments["document_id"] = required_text(
                document_id, "document_id", maximum=80
            )
        if action != "status":
            arguments["steps"] = bounded_int(steps, "steps", 1, 100)
        return await self.client.execute("history", arguments)

    async def brush(
        self,
        *,
        action: str,
        document_id: str | None = None,
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
        if document_id is not None:
            arguments["document_id"] = required_text(
                document_id, "document_id", maximum=80
            )
        if action == "list_presets":
            if query is not None:
                arguments["query"] = optional_text(query, "query", maximum=200)
            arguments["limit"] = bounded_int(limit, "limit", 1, 500)
        elif action == "set":
            properties = (
                preset_name,
                brush_size,
                opacity,
                flow,
                rotation,
                eraser_mode,
                disable_pressure,
                foreground,
            )
            if all(value is None for value in properties):
                raise InputError("set requires at least one brush property")
            if preset_name is not None:
                arguments["preset_name"] = required_text(
                    preset_name, "preset_name", maximum=300
                )
            if brush_size is not None:
                arguments["brush_size"] = finite_number(
                    brush_size, "brush_size", 0.1, 1000.0
                )
            if opacity is not None:
                arguments["opacity"] = finite_number(opacity, "opacity", 0.0, 1.0)
            if flow is not None:
                arguments["flow"] = finite_number(flow, "flow", 0.0, 1.0)
            if rotation is not None:
                arguments["rotation"] = finite_number(
                    rotation, "rotation", -3600.0, 3600.0
                )
            for field, value in (
                ("eraser_mode", eraser_mode),
                ("disable_pressure", disable_pressure),
            ):
                if value is not None:
                    if not isinstance(value, bool):
                        raise InputError(f"{field} must be a boolean")
                    arguments[field] = value
            if foreground is not None:
                arguments["foreground"] = hex_color(foreground)
        return await self.client.execute("brush", arguments)

    async def paint_path(
        self,
        *,
        points: list[dict[str, Any]],
        document_id: str | None = None,
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
            normalized.append(
                {
                    "x": finite_number(
                        point.get("x"), f"points[{index}].x", -1_000_000, 1_000_000
                    ),
                    "y": finite_number(
                        point.get("y"), f"points[{index}].y", -1_000_000, 1_000_000
                    ),
                    "pressure": finite_number(
                        point.get("pressure", 1.0),
                        f"points[{index}].pressure",
                        0,
                        1,
                    ),
                }
            )
        arguments: dict[str, Any] = {"points": normalized}
        if document_id is not None:
            arguments["document_id"] = required_text(
                document_id, "document_id", maximum=80
            )
        if layer_id is not None:
            arguments["layer_id"] = required_text(layer_id, "layer_id", maximum=80)
        if preset_name is not None:
            arguments["preset_name"] = required_text(
                preset_name, "preset_name", maximum=300
            )
        if brush_size is not None:
            arguments["brush_size"] = finite_number(
                brush_size, "brush_size", 0.1, 1000
            )
        return await self.client.execute("paint_path", arguments)

    async def save_export(
        self,
        *,
        kra_path: str | None,
        png_path: str | None,
        overwrite: bool,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        if kra_path is None and png_path is None:
            raise InputError("provide kra_path and/or png_path")
        arguments: dict[str, Any] = {"overwrite": overwrite}
        if document_id is not None:
            arguments["document_id"] = required_text(
                document_id, "document_id", maximum=80
            )
        try:
            if kra_path is not None:
                arguments["kra_path"] = str(
                    self.paths.validate_output(
                        kra_path, extensions={".kra"}, overwrite=overwrite
                    )
                )
            if png_path is not None:
                arguments["png_path"] = str(
                    self.paths.validate_output(
                        png_path, extensions={".png"}, overwrite=overwrite
                    )
                )
        except PathPolicyError as exc:
            raise InputError(str(exc)) from exc
        return await self.client.execute("save_export", arguments)
