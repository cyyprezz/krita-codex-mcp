from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ImageContent, TextContent

from .bridge_client import BridgeCallError
from .validation import InputError


async def tool_call(function: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    try:
        return await function()
    except (InputError, BridgeCallError) as exc:
        raise tool_error(exc) from exc


def image_result(metadata: dict[str, Any], png_base64: str) -> list[TextContent | ImageContent]:
    return [
        TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
        ImageContent(type="image", data=png_base64, mime_type="image/png"),
    ]


def tool_error(exc: InputError | BridgeCallError) -> ToolError:
    if isinstance(exc, BridgeCallError):
        payload = {
            "code": exc.code,
            "message": exc.message,
            "details": dict(exc.details),
            "retryable": exc.retryable,
        }
    else:
        payload = {"code": "INVALID_ARGUMENT", "message": str(exc), "retryable": False}
    return ToolError(json.dumps(payload, ensure_ascii=False))
