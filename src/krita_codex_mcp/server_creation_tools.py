from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .core_service import KritaService
from .server_common import tool_call


def register_creation_tools(mcp: FastMCP, service: KritaService) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
    )
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
            "move_selection",
            "resize_selection",
            "copy",
            "cut",
            "paste",
            "copy_to_layer",
            "cut_to_layer",
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
        """Create/refine selections; move/resize, copy/cut/paste, fill, or clear content."""
        return await tool_call(
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

    @mcp.tool(
        annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
    )
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
            "scale",
            "rotate",
            "shear",
            "crop",
            "flip",
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
        x: Annotated[int | None, Field(ge=-1_000_000, le=1_000_000)] = None,
        y: Annotated[int | None, Field(ge=-1_000_000, le=1_000_000)] = None,
        width: Annotated[int | None, Field(ge=1, le=32768)] = None,
        height: Annotated[int | None, Field(ge=1, le=32768)] = None,
        strategy: str = "NearestNeighbor",
        angle_degrees: Annotated[float | None, Field(ge=-360, le=360)] = None,
        shear_x_degrees: Annotated[float | None, Field(ge=-89, le=89)] = None,
        shear_y_degrees: Annotated[float | None, Field(ge=-89, le=89)] = None,
        direction: Literal["horizontal", "vertical"] | None = None,
    ) -> dict[str, Any]:
        """Manage layers by UUID, including stack, properties, and native transformations."""
        return await tool_call(
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
                x=x,
                y=y,
                width=width,
                height=height,
                strategy=strategy,
                angle_degrees=angle_degrees,
                shear_x_degrees=shear_x_degrees,
                shear_y_degrees=shear_y_degrees,
                direction=direction,
            )
        )

    @mcp.tool(
        annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
    )
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
        return await tool_call(
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

    @mcp.tool(
        annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
    )
    async def krita_paint_path(
        points: list[dict[str, Any]],
        document_id: str | None = None,
        layer_id: str | None = None,
        preset_name: str | None = None,
        brush_size: Annotated[float | None, Field(gt=0, le=1000)] = None,
    ) -> dict[str, Any]:
        """Paint pressure-aware line segments with Krita's current or named native brush preset."""
        return await tool_call(
            lambda: service.paint_path(
                points=points,
                document_id=document_id,
                layer_id=layer_id,
                preset_name=preset_name,
                brush_size=brush_size,
            )
        )

    @mcp.tool(
        annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
    )
    async def krita_draw(
        shape: Literal["line", "path", "rectangle", "ellipse", "polygon"],
        document_id: str | None = None,
        layer_id: str | None = None,
        points: list[dict[str, Any]] | None = None,
        x: Annotated[float | None, Field(ge=-1_000_000, le=1_000_000)] = None,
        y: Annotated[float | None, Field(ge=-1_000_000, le=1_000_000)] = None,
        width: Annotated[float | None, Field(gt=0, le=32768)] = None,
        height: Annotated[float | None, Field(gt=0, le=32768)] = None,
        closed: bool = False,
        stroke_style: Literal["None", "ForegroundColor", "BackgroundColor"] = (
            "ForegroundColor"
        ),
        fill_style: Literal["None", "ForegroundColor", "BackgroundColor"] = "None",
        preset_name: str | None = None,
        brush_size: Annotated[float | None, Field(gt=0, le=1000)] = None,
        foreground: str | None = None,
        background: str | None = None,
    ) -> dict[str, Any]:
        """Draw native lines, paths, polygons, rectangles, or ellipses with Krita presets."""
        return await tool_call(
            lambda: service.draw(
                shape=shape,
                document_id=document_id,
                layer_id=layer_id,
                points=points,
                x=x,
                y=y,
                width=width,
                height=height,
                closed=closed,
                stroke_style=stroke_style,
                fill_style=fill_style,
                preset_name=preset_name,
                brush_size=brush_size,
                foreground=foreground,
                background=background,
            )
        )

    @mcp.tool(
        annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
    )
    async def krita_color(
        action: Literal[
            "read",
            "list_palettes",
            "read_palette",
            "set_project_palette",
            "apply_swatch",
            "replace_color",
            "adjust",
        ] = "read",
        document_id: str | None = None,
        layer_id: str | None = None,
        palette_name: str | None = None,
        swatch_index: Annotated[int | None, Field(ge=0, le=4999)] = None,
        target: Literal["foreground", "background"] = "foreground",
        project_name: str | None = None,
        entries: list[dict[str, Any]] | None = None,
        from_color: str | None = None,
        to_color: str | None = None,
        tolerance: Annotated[int, Field(ge=0, le=255)] = 0,
        filter_name: Literal[
            "autocontrast",
            "brightnesscontrast",
            "desaturate",
            "hsvadjustment",
            "invert",
            "levels",
            "normalize",
        ]
        | None = None,
        settings: dict[str, Any] | None = None,
        x: Annotated[int | None, Field(ge=-1_000_000, le=1_000_000)] = None,
        y: Annotated[int | None, Field(ge=-1_000_000, le=1_000_000)] = None,
        width: Annotated[int | None, Field(ge=1, le=32768)] = None,
        height: Annotated[int | None, Field(ge=1, le=32768)] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        """Use KRA project colors, Krita palettes, reversible replacement, and safe filters."""
        return await tool_call(
            lambda: service.color(
                action=action,
                document_id=document_id,
                layer_id=layer_id,
                palette_name=palette_name,
                swatch_index=swatch_index,
                target=target,
                project_name=project_name,
                entries=entries,
                from_color=from_color,
                to_color=to_color,
                tolerance=tolerance,
                filter_name=filter_name,
                settings=settings,
                x=x,
                y=y,
                width=width,
                height=height,
                limit=limit,
            )
        )
