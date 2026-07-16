from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "krita_state",
    "krita_preview",
    "krita_canvas",
    "krita_edit",
    "krita_document",
    "krita_layers",
    "krita_history",
    "krita_brush",
    "krita_paint_path",
    "krita_draw",
    "krita_color",
    "krita_asset",
    "krita_save_export",
}


async def smoke(wheel: Path, *, offline: bool) -> None:
    uvx = shutil.which("uvx")
    if uvx is None:
        raise RuntimeError("uvx is unavailable")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config = root / "config.json"
        config.write_text(
            json.dumps(
                {
                    "token": secrets.token_urlsafe(32),
                    "host": "127.0.0.1",
                    "allowed_roots": [str(root)],
                }
            ),
            encoding="utf-8",
        )
        arguments = []
        if offline:
            arguments.append("--offline")
        arguments.extend(["--from", str(wheel.resolve()), "krita-codex-mcp"])
        environment = dict(os.environ)
        environment["KRITA_CODEX_CONFIG"] = str(config)
        parameters = StdioServerParameters(
            command=uvx,
            args=arguments,
            env=environment,
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
    names = {tool.name for tool in tools.tools}
    if names != EXPECTED_TOOLS:
        raise RuntimeError(f"Unexpected MCP tool surface: {sorted(names)}")
    print("Local wheel STDIO smoke: 13 tools")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    asyncio.run(smoke(args.wheel, offline=args.offline))


if __name__ == "__main__":
    main()
