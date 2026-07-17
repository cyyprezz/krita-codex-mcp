from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from krita_codex_protocol import BridgeConfig, ConfigError

from .bridge_client import BridgeClient
from .server_asset_tools import register_asset_tools
from .server_core_tools import register_core_tools
from .server_creation_tools import register_creation_tools
from .service import KritaService

SERVER_INSTRUCTIONS = (
    "Control the currently active Krita document through a local authenticated bridge. "
    "Call krita_state before mutating when layer IDs or canvas bounds may be stale. "
    "Read runtime_compatibility in krita_state and avoid tools or actions listed as affected "
    "when the running Krita build is limited. "
    "Use krita_preview after visual changes. File writes are restricted to configured roots. "
    "bridge_revision counts bridge mutations only, not manual edits in Krita."
)


def build_server(config: BridgeConfig, client: BridgeClient | None = None) -> FastMCP:
    service = KritaService(config, client or BridgeClient(config))
    mcp = FastMCP("krita-codex-mcp", instructions=SERVER_INSTRUCTIONS)
    register_core_tools(mcp, service)
    register_creation_tools(mcp, service)
    register_asset_tools(mcp, service)
    return mcp


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
