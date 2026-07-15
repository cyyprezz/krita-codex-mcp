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
    async def krita_state(document_id: str | None = None) -> dict[str, Any]:
        """Read active document, canvas, recursive layer tree, active layer, color and brush."""
        return await _call(lambda: service.state(document_id=document_id))

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def krita_preview(
        max_width: Annotated[int, Field(ge=64, le=4096)] = 1024,
        max_height: Annotated[int, Field(ge=64, le=4096)] = 1024,
        document_id: str | None = None,
        x: Annotated[int | None, Field(ge=0, le=1_000_000)] = None,
        y: Annotated[int | None, Field(ge=0, le=1_000_000)] = None,
        width: Annotated[int | None, Field(ge=1, le=32768)] = None,
        height: Annotated[int | None, Field(ge=1, le=32768)] = None,
        scale: Annotated[int, Field(ge=1, le=16)] = 1,
    ) -> list[TextContent | ImageContent]:
        """Return a whole-canvas or pixel-precise cropped PNG preview as MCP image content."""
        try:
            metadata, png_base64 = await service.preview(
                max_width,
                max_height,
                document_id=document_id,
                x=x,
                y=y,
                width=width,
                height=height,
                scale=scale,
            )
            return [
                TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
                ImageContent(type="image", data=png_base64, mimeType="image/png"),
            ]
        except (InputError, BridgeCallError) as exc:
            raise _tool_error(exc) from exc

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
    async def krita_canvas(
        action: Literal[
            "read",
            "sample_color",
            "resize",
            "scale",
            "crop",
            "rotate",
            "flip",
            "import_layer",
        ] = "read",
        document_id: str | None = None,
        x: Annotated[int | None, Field(ge=-1_000_000, le=1_000_000)] = None,
        y: Annotated[int | None, Field(ge=-1_000_000, le=1_000_000)] = None,
        width: Annotated[int | None, Field(ge=1, le=32768)] = None,
        height: Annotated[int | None, Field(ge=1, le=32768)] = None,
        anchor: Literal[
            "top_left",
            "top",
            "top_right",
            "left",
            "center",
            "right",
            "bottom_left",
            "bottom",
            "bottom_right",
        ] = "center",
        strategy: str = "NearestNeighbor",
        angle_degrees: Annotated[float | None, Field(ge=-360, le=360)] = None,
        direction: Literal["horizontal", "vertical"] | None = None,
        path: str | None = None,
        name: str | None = None,
        parent_id: str | None = None,
        above_id: str | None = None,
    ) -> dict[str, Any]:
        """Inspect/sample, resize, scale, crop, rotate, flip, or import an editable raster layer."""
        return await _call(
            lambda: service.canvas(
                action=action,
                document_id=document_id,
                x=x,
                y=y,
                width=width,
                height=height,
                anchor=anchor,
                strategy=strategy,
                angle_degrees=angle_degrees,
                direction=direction,
                path=path,
                name=name,
                parent_id=parent_id,
                above_id=above_id,
            )
        )

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
    async def krita_edit(
        action: Literal[
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
        ] = "selection_status",
        document_id: str | None = None,
        layer_id: str | None = None,
        x: Annotated[int | None, Field(ge=-1_000_000, le=1_000_000)] = None,
        y: Annotated[int | None, Field(ge=-1_000_000, le=1_000_000)] = None,
        width: Annotated[int | None, Field(ge=1, le=32768)] = None,
        height: Annotated[int | None, Field(ge=1, le=32768)] = None,
        combine: Literal["replace", "add", "subtract", "intersect"] = "replace",
        amount: Annotated[int | None, Field(ge=1, le=4096)] = None,
        color: str | None = None,
    ) -> dict[str, Any]:
        """Create and refine selections, then fill or clear them on a stable paint-layer UUID."""
        return await _call(
            lambda: service.edit(
                action=action,
                document_id=document_id,
                layer_id=layer_id,
                x=x,
                y=y,
                width=width,
                height=height,
                combine=combine,
                amount=amount,
                color=color,
            )
        )

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
    async def krita_document(
        action: Literal["list", "create", "open", "activate", "save", "close"] = "list",
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
        """List, create, open, activate, checkpoint-save, or explicitly close documents."""
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

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
    async def krita_layers(
        action: Literal[
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
        ] = "read",
        document_id: str | None = None,
        layer_id: str | None = None,
        name: str | None = None,
        kind: Literal["paintlayer", "grouplayer"] = "paintlayer",
        parent_id: str | None = None,
        above_id: str | None = None,
        visible: bool | None = None,
        opacity: Annotated[float | None, Field(ge=0, le=1)] = None,
        locked: bool | None = None,
        alpha_locked: bool | None = None,
        inherit_alpha: bool | None = None,
        blending_mode: str | None = None,
        offset_x: Annotated[int | None, Field(ge=-1_000_000, le=1_000_000)] = None,
        offset_y: Annotated[int | None, Field(ge=-1_000_000, le=1_000_000)] = None,
    ) -> dict[str, Any]:
        """Manage layers by stable UUID, including duplicate, delete, reorder and translation."""
        return await _call(
            lambda: service.layers(
                action=action,
                document_id=document_id,
                layer_id=layer_id,
                name=name,
                kind=kind,
                parent_id=parent_id,
                above_id=above_id,
                visible=visible,
                opacity=opacity,
                locked=locked,
                alpha_locked=alpha_locked,
                inherit_alpha=inherit_alpha,
                blending_mode=blending_mode,
                offset_x=offset_x,
                offset_y=offset_y,
            )
        )

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
    async def krita_history(
        action: Literal["status", "undo", "redo"] = "status",
        steps: Annotated[int, Field(ge=1, le=100)] = 1,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        """Read undo/redo availability or execute bounded undo/redo steps in Krita."""
        return await _call(
            lambda: service.history(action=action, steps=steps, document_id=document_id)
        )

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False})
    async def krita_brush(
        action: Literal["read", "list_presets", "set"] = "read",
        document_id: str | None = None,
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
                document_id=document_id,
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
        document_id: str | None = None,
        layer_id: str | None = None,
        preset_name: str | None = None,
        brush_size: Annotated[float | None, Field(gt=0, le=1000)] = None,
    ) -> dict[str, Any]:
        """Paint pressure-aware line segments with Krita's current or named native brush preset."""
        return await _call(
            lambda: service.paint_path(
                points=points,
                document_id=document_id,
                layer_id=layer_id,
                preset_name=preset_name,
                brush_size=brush_size,
            )
        )

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
    async def krita_asset(
        layer_id: str,
        output_path: str,
        action: Literal[
            "export_layer", "export_animation", "export_tileset", "export_regions"
        ] = "export_layer",
        crop: Literal["canvas", "content", "union"] = "canvas",
        overwrite: bool = False,
        document_id: str | None = None,
        preview_max_width: Annotated[int, Field(ge=64, le=4096)] = 1024,
        preview_max_height: Annotated[int, Field(ge=64, le=4096)] = 1024,
        frame_ids: list[str] | None = None,
        animation_name: str | None = None,
        columns: Annotated[int, Field(ge=1, le=64)] = 8,
        padding: Annotated[int, Field(ge=0, le=64)] = 0,
        frame_duration_ms: Annotated[int, Field(ge=1, le=60000)] = 100,
        pivot_x: Annotated[float, Field(ge=0, le=1)] = 0.5,
        pivot_y: Annotated[float, Field(ge=0, le=1)] = 0.5,
        pixels_per_unit: Annotated[float, Field(gt=0, le=10000)] = 100.0,
        loop: bool = True,
        regions: list[dict[str, Any]] | None = None,
        package_name: str | None = None,
        tile_width: Annotated[int | None, Field(ge=1, le=32768)] = None,
        tile_height: Annotated[int | None, Field(ge=1, le=32768)] = None,
        origin_x: Annotated[int, Field(ge=0, le=1000000)] = 0,
        origin_y: Annotated[int, Field(ge=0, le=1000000)] = 0,
        grid_columns: Annotated[int | None, Field(ge=1, le=1024)] = None,
        grid_rows: Annotated[int | None, Field(ge=1, le=1024)] = None,
        spacing_x: Annotated[int, Field(ge=0, le=32768)] = 0,
        spacing_y: Annotated[int, Field(ge=0, le=32768)] = 0,
        skip_empty: bool = False,
        name_prefix: str | None = None,
    ) -> list[TextContent | ImageContent]:
        """Export a layer, animation, tileset, or named-region package with direct preview."""
        try:
            metadata, png_base64 = await service.asset(
                action=action,
                layer_id=layer_id,
                output_path=output_path,
                crop=crop,
                overwrite=overwrite,
                document_id=document_id,
                preview_max_width=preview_max_width,
                preview_max_height=preview_max_height,
                frame_ids=frame_ids,
                animation_name=animation_name,
                columns=columns,
                padding=padding,
                frame_duration_ms=frame_duration_ms,
                pivot_x=pivot_x,
                pivot_y=pivot_y,
                pixels_per_unit=pixels_per_unit,
                loop=loop,
                regions=regions,
                package_name=package_name,
                tile_width=tile_width,
                tile_height=tile_height,
                origin_x=origin_x,
                origin_y=origin_y,
                grid_columns=grid_columns,
                grid_rows=grid_rows,
                spacing_x=spacing_x,
                spacing_y=spacing_y,
                skip_empty=skip_empty,
                name_prefix=name_prefix,
            )
            return [
                TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
                ImageContent(type="image", data=png_base64, mimeType="image/png"),
            ]
        except (InputError, BridgeCallError) as exc:
            raise _tool_error(exc) from exc

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False})
    async def krita_save_export(
        kra_path: str | None = None,
        png_path: str | None = None,
        overwrite: bool = False,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        """Save KRA and/or export PNG only beneath configured allowed roots."""
        return await _call(
            lambda: service.save_export(
                kra_path=kra_path,
                png_path=png_path,
                overwrite=overwrite,
                document_id=document_id,
            )
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
