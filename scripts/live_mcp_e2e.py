from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client


async def run(executable: Path, repo_root: Path) -> dict[str, object]:
    parameters = StdioServerParameters(command=str(executable), cwd=repo_root)
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        names = [tool.name for tool in listed.tools]
        expected = {
            "krita_state",
            "krita_preview",
            "krita_document",
            "krita_layers",
            "krita_history",
            "krita_brush",
            "krita_paint_path",
            "krita_save_export",
        }
        if set(names) != expected:
            raise RuntimeError(f"Unexpected MCP tool surface: {names}")

        created_document_id = None
        try:
            state = await session.call_tool("krita_state", {})
            if state.isError:
                created = await session.call_tool(
                    "krita_document",
                    {
                        "action": "create",
                        "width": 128,
                        "height": 96,
                        "name": "Codex MCP Smoke Test",
                    },
                )
                if created.isError:
                    raise RuntimeError("krita_document could not create the smoke-test document")
                created_payload = _structured_payload(created)
                created_document_id = created_payload.get("affected_document_id")
                if not isinstance(created_document_id, str):
                    raise RuntimeError("krita_document returned no affected document ID")
                state = await session.call_tool("krita_state", {})
            if state.isError:
                raise RuntimeError("krita_state returned an MCP tool error")

            preview = await session.call_tool(
                "krita_preview", {"max_width": 256, "max_height": 256}
            )
            if preview.isError:
                raise RuntimeError("krita_preview returned an MCP tool error")
            images = [item for item in preview.content if isinstance(item, types.ImageContent)]
            texts = [item for item in preview.content if isinstance(item, types.TextContent)]
            if len(images) != 1 or images[0].mimeType != "image/png":
                raise RuntimeError("krita_preview did not return exactly one PNG ImageContent")
            image_bytes = base64.b64decode(images[0].data, validate=True)
            if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("MCP ImageContent payload is not a PNG")
            if not texts:
                raise RuntimeError("krita_preview did not return its metadata TextContent")

            return {
                "tools": names,
                "created_smoke_document": created_document_id is not None,
                "state_content_types": [type(item).__name__ for item in state.content],
                "preview_content_types": [type(item).__name__ for item in preview.content],
                "preview_mime_type": images[0].mimeType,
                "preview_bytes": len(image_bytes),
            }
        finally:
            if created_document_id is not None:
                closed = await session.call_tool(
                    "krita_document",
                    {
                        "action": "close",
                        "document_id": created_document_id,
                        "discard_changes": True,
                    },
                )
                if closed.isError:
                    raise RuntimeError("Could not close the MCP smoke-test document")


def _structured_payload(result) -> dict[str, object]:
    if isinstance(result.structuredContent, dict):
        return result.structuredContent
    for item in result.content:
        if isinstance(item, types.TextContent):
            raw = json.loads(item.text)
            if isinstance(raw, dict):
                return raw
    raise RuntimeError("MCP tool returned no structured object")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    default_executable = repo_root / ".venv" / "Scripts" / "krita-codex-mcp.exe"
    parser = argparse.ArgumentParser(description="Exercise the installed MCP STDIO surface")
    parser.add_argument("--executable", type=Path, default=default_executable)
    arguments = parser.parse_args()
    executable = arguments.executable.resolve()
    if not executable.is_file():
        raise SystemExit(f"MCP executable not found: {executable}")
    result = asyncio.run(run(executable, repo_root))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
