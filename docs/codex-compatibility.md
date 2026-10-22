# Codex and MCP compatibility

Checked against official documentation and Codex CLI `0.163.0` on 2026-10-22.

## Supported connection

Krita Codex MCP is a local STDIO MCP server. Codex launches it as a child process; that process
talks to the authenticated Krita plugin over loopback. The loopback bridge is an internal JSON
API, not a remote MCP endpoint. Codex and Krita must run on the same computer.

The repository uses the MCP Python SDK 1.x maintenance line (`mcp>=1.12,<2`, locked to `1.28.1`).
This intentionally retains its `FastMCP` API and legacy initialization handshake. Codex CLI
`0.163.0` retains that handshake for STDIO servers without the modern-protocol opt-in. No modern
protocol opt-in is needed in the provided configurations.

The newer MCP `2026-07-28` specification changes initialization, discovery and request metadata.
It also defines a legacy fallback for clients supporting both protocol eras. This repository does
not claim to implement the full newer protocol. SDK 2.x is a separate migration, not an unbounded
dependency update: the SDK's own maintenance documentation recommends `mcp<2` for existing v1 apps.

Codex continues to accept `[mcp_servers.krita]`, STDIO `command`/`args`, `startup_timeout_sec` and
`tool_timeout_sec`. Its removal of `codex mcp-server` affects hosting Codex itself as an MCP server;
Krita Codex MCP does not use that removed command.

Codex filters inherited environment variables for MCP subprocesses. Custom `XDG_*` directories
must be listed in `env_vars`, as in the Linux guide, or provided explicitly through the server's
`env` table. An intentional `KRITA_CODEX_CONFIG` runtime override also needs explicit forwarding.

## Plugins and distribution

The existing `.codex-plugin/plugin.json`, `.mcp.json` and `.agents/plugins/marketplace.json` remain
supported compatibility formats. The portable root `plugin.json` / `mcp.json` format is available
for new packages; it does not require renaming a working plugin's files. The newer portable MCP
format has different requirements, including an explicit transport type.

Release `0.2.2` includes the Linux/XDG fixes. The marketplace bundle and the installer's
`codex-config` command launch that exact package on both Windows and Linux and explicitly forward
custom XDG directories. Keep the installed Krita plugin and server on the same version.
For unreleased source changes, use the checkout-specific alternative in the
[Linux guide](linux-installation.md#4-connect-codex).

## What CI verifies

Both Windows and Ubuntu CI install the pinned Codex CLI and run
`scripts/codex_compatibility_smoke.py` against the built wheel. In an isolated, disposable Codex
profile, this checks:

- the actual CLI accepts the installer-generated MCP configuration;
- the documented plugin and marketplace commands exist;
- Codex starts the built STDIO server and discovers exactly the project's 13 tools.

The Ubuntu run uses a custom `XDG_CONFIG_HOME` passed through `env_vars`, so discovery also proves
that the server reads the forwarded Linux configuration path. It uses the environment and timeout
settings from the installer's actual generated block, replacing the package source with the built
wheel while that version is not yet published.

The test uses the app-server discovery endpoint only as a diagnostic client. It does not start a
model turn, require an API key or change a personal Codex profile. It does not exercise painting,
GUI plugin installation or a live Krita process. Run the installer's live preflight on the actual
Krita build before productive use.

## Official references

- [Codex MCP setup and configuration](https://learn.chatgpt.com/docs/extend/mcp)
- [Codex changelog](https://learn.chatgpt.com/docs/changelog)
- [Plugin packaging and compatibility formats](https://developers.openai.com/plugins/build/plugins)
- [MCP 2026-07-28 changes](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- [STDIO compatibility](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio#backward-compatibility)
- [Python SDK v1 maintenance documentation](https://py.sdk.modelcontextprotocol.io/v1/)
- [Codex 0.163.0 protocol selection](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/rmcp-client/src/protocol_mode.rs)
- [Codex 0.163.0 MCP environment filtering](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/rmcp-client/src/utils.rs)
