from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp_types import ImageContent, TextContent
from pydantic import Field

from .bridge_client import BridgeCallError
from .core_service import KritaService
from .server_common import image_result, tool_call, tool_error
from .validation import InputError


def register_core_tools(mcp: MCPServer, service: KritaService) -> None:
    @mcp.tool(annotations={"read_only_hint": True, "open_world_hint": False})
    async def krita_state(document_id: str | None = None) -> dict[str, Any]:
        """Read document/layers/painting state plus the running Krita compatibility report."""
        return await tool_call(lambda: service.state(document_id=document_id))

    @mcp.tool(annotations={"read_only_hint": True, "open_world_hint": False})
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
            return image_result(metadata, png_base64)
        except (InputError, BridgeCallError) as exc:
            raise tool_error(exc) from exc

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": True, "open_world_hint": False}
    )
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
        return await tool_call(
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

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": True, "open_world_hint": False}
    )
    async def krita_document(
        action: Literal[
            "list", "compatibility", "create", "open", "activate", "save", "close"
        ] = "list",
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
        """Inspect compatibility; list, create, open, activate, save, or close documents."""
        return await tool_call(
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

    @mcp.tool(
        annotations={"read_only_hint": False, "destructive_hint": True, "open_world_hint": False}
    )
    async def krita_history(
        action: Literal["status", "undo", "redo"] = "status",
        steps: Annotated[int, Field(ge=1, le=100)] = 1,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        """Read undo/redo availability or execute bounded undo/redo steps in Krita."""
        return await tool_call(
            lambda: service.history(action=action, steps=steps, document_id=document_id)
        )
