from __future__ import annotations

import json
import sys
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent
from pydantic import Field

from krita_codex_protocol import BridgeConfig, ConfigError

from .bridge_client import BridgeCallError, BridgeClient
from .service import InputError, KritaService

SERVER_INSTRUCTIONS = (
    "Control the currently active Krita document through a local authenticated bridge. "
    "Call krita_state before mutating when layer IDs or canvas bounds may be stale. "
    "Use krita_preview after visual changes. File writes are restricted to configured roots. "
    "bridge_revision counts bridge mutations only, not manual edits in Krita."
)


def build_server(config: BridgeConfig, client: BridgeClient | None = None) -> FastMCP:
    service = KritaService(config, client or BridgeClient(config))
    mcp = FastMCP("krita-codex-mcp", instructions=SERVER_INSTRUCTIONS)

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def krita_state() -> dict[str, Any]:
        """Read active document, canvas, recursive layer tree, active layer, color and brush."""
        return await _call(service.state)

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def krita_preview(
        max_width: Annotated[int, Field(ge=64, le=4096)] = 1024,
        max_height: Annotated[int, Field(ge=64, le=4096)] = 1024,
    ) -> list[TextContent | ImageContent]:
        """Return a scaled PNG preview as MCP image content plus concise canvas metadata."""
        try:
            metadata, png_base64 = await service.preview(max_width, max_height)
            return [
                TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
                ImageContent(type="image", data=png_base64, mimeType="image/png"),
            ]
        except (InputError, BridgeCallError) as exc:
            raise _tool_error(exc) from exc

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
    async def krita_document(
        action: Literal["list", "create", "open", "activate", "close"] = "list",
        path: str | None = None,
        document_id: str | None = None,
        width: Annotated[int, Field(ge=1, le=32768)] = 1920,
        height: Annotated[int, Field(ge=1, le=32768)] = 1080,
        name: str = "Codex Document",
        color_model: str = "RGBA",
        color_depth: str = "U8",
        profile: str = "",
        resolution: Annotated[float, Field(ge=1, le=2400)] = 300.0,
        discard_changes: bool = False,
    ) -> dict[str, Any]:
        """List, create, open, activate, or explicitly close Krita documents."""
        return await _call(
            lambda: service.document(
                action=action,
                path=path,
                document_id=document_id,
                width=width,
                height=height,
                name=name,
                color_model=color_model,
                color_depth=color_depth,
                profile=profile,
                resolution=resolution,
                discard_changes=discard_changes,
            )
        )

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    async def krita_layers(
        action: Literal["read", "create", "activate", "rename", "set_properties"] = "read",
        layer_id: str | None = None,
        name: str | None = None,
        kind: Literal["paintlayer", "grouplayer"] = "paintlayer",
        parent_id: str | None = None,
        visible: bool | None = None,
        opacity: Annotated[float | None, Field(ge=0, le=1)] = None,
    ) -> dict[str, Any]:
        """Read layers or create, activate, rename, and change visibility/opacity by stable UUID."""
        return await _call(
            lambda: service.layers(
                action=action,
                layer_id=layer_id,
                name=name,
                kind=kind,
                parent_id=parent_id,
                visible=visible,
                opacity=opacity,
            )
        )

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
    async def krita_history(
        action: Literal["status", "undo", "redo"] = "status",
        steps: Annotated[int, Field(ge=1, le=100)] = 1,
    ) -> dict[str, Any]:
        """Read undo/redo availability or execute bounded undo/redo steps in Krita."""
        return await _call(lambda: service.history(action=action, steps=steps))

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    async def krita_brush(
        action: Literal["read", "list_presets", "set"] = "read",
        query: str | None = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        preset_name: str | None = None,
        brush_size: Annotated[float | None, Field(gt=0, le=1000)] = None,
        opacity: Annotated[float | None, Field(ge=0, le=1)] = None,
        flow: Annotated[float | None, Field(ge=0, le=1)] = None,
        rotation: Annotated[float | None, Field(ge=-3600, le=3600)] = None,
        eraser_mode: bool | None = None,
        disable_pressure: bool | None = None,
        foreground: str | None = None,
    ) -> dict[str, Any]:
        """Read/set brush state or list native Krita presets with optional filtering."""
        return await _call(
            lambda: service.brush(
                action=action,
                query=query,
                limit=limit,
                preset_name=preset_name,
                brush_size=brush_size,
                opacity=opacity,
                flow=flow,
                rotation=rotation,
                eraser_mode=eraser_mode,
                disable_pressure=disable_pressure,
                foreground=foreground,
            )
        )

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    async def krita_paint_path(
        points: list[dict[str, Any]],
        layer_id: str | None = None,
        preset_name: str | None = None,
        brush_size: Annotated[float | None, Field(gt=0, le=1000)] = None,
    ) -> dict[str, Any]:
        """Paint pressure-aware line segments with Krita's current or named native brush preset."""
        return await _call(
            lambda: service.paint_path(
                points=points,
                layer_id=layer_id,
                preset_name=preset_name,
                brush_size=brush_size,
            )
        )

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
    async def krita_save_export(
        kra_path: str | None = None,
        png_path: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Save KRA and/or export PNG only beneath configured allowed roots."""
        return await _call(
            lambda: service.save_export(kra_path=kra_path, png_path=png_path, overwrite=overwrite)
        )

    return mcp


async def _call(function: Any) -> dict[str, Any]:
    try:
        return await function()
    except (InputError, BridgeCallError) as exc:
        raise _tool_error(exc) from exc


def _tool_error(exc: InputError | BridgeCallError) -> RuntimeError:
    if isinstance(exc, BridgeCallError):
        payload = {
            "code": exc.code,
            "message": exc.message,
            "details": dict(exc.details),
            "retryable": exc.retryable,
        }
    else:
        payload = {"code": "INVALID_ARGUMENT", "message": str(exc), "retryable": False}
    return RuntimeError(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    try:
        config = BridgeConfig.load()
        server = build_server(config)
    except ConfigError as exc:
        print(f"krita-codex-mcp configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
