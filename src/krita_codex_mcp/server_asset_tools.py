from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp_types import ImageContent, TextContent
from pydantic import Field

from .bridge_client import BridgeCallError
from .core_service import KritaService
from .server_common import image_result, tool_call, tool_error
from .validation import InputError


def register_asset_tools(mcp: MCPServer, service: KritaService) -> None:
    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": True, "open_world_hint": False}
    )
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
            return image_result(metadata, png_base64)
        except (InputError, BridgeCallError) as exc:
            raise tool_error(exc) from exc

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": True, "open_world_hint": False}
    )
    async def krita_save_export(
        kra_path: str | None = None,
        png_path: str | None = None,
        overwrite: bool = False,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        """Save KRA and/or export PNG only beneath configured allowed roots."""
        return await tool_call(
            lambda: service.save_export(
                kra_path=kra_path,
                png_path=png_path,
                overwrite=overwrite,
                document_id=document_id,
            )
        )
