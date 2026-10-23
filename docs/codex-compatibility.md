# Codex and MCP compatibility

Compatibility baseline: Codex CLI `0.163.0`, Claude Code `2.1.308`, MCP Python SDK `2.3.0`.
Updated on 2026-10-23; CI tests the candidate wheel rather than substituting the PyPI release.

## Supported connection

Krita Codex MCP is a local STDIO MCP server. Codex launches it as a child process; that process
talks to the authenticated Krita plugin over loopback. The loopback bridge is an internal JSON
API, not a remote MCP endpoint. Codex and Krita must run on the same computer.

The source checkout uses MCP Python SDK 2 (`mcp>=2.3,<3`, locked to `2.3.0`) and its `MCPServer`
API. The server accepts both modern `2026-07-28` discovery and legacy `initialize` over STDIO.
Codex CLI `0.163.0` uses the legacy handshake for the provided configurations; no modern-protocol
opt-in is required. Async tool handlers, authenticated loopback requests and Krita's Qt main-thread
dispatcher retain their existing ownership. There is no new network MCP listener.

The migration updates SDK types and result objects, keeps PNGs as direct image content alongside
text metadata, and uses explicit `ToolError` for expected failures so error codes, details and
retryability remain visible. Image tools no longer duplicate image blocks in an SDK-generated
`structuredContent.result` wrapper; their direct `content` blocks remain available in both
protocols. The repository does not claim every optional MCP feature or model-driven client flow.

**Unreleased source change:** PyPI `0.2.2` still uses SDK 1 (`mcp<2`). Installation examples and
marketplace pins continue to select that published release. To test SDK 2 before a new release,
run from a checkout or build a local wheel. Keep the Krita plugin and MCP server versions aligned.

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

`scripts/stdio_surface_smoke.py` launches the built wheel twice through the real SDK client:
automatic modern discovery must negotiate `2026-07-28`, and explicit legacy initialization must
negotiate `2025-11-25`. Both must expose exactly 13 tools.

`tests/test_stdio_contract.py` also sends JSON-RPC directly to the real server process. Both wire
formats cover tool schemas and approval annotations, preview/asset PNG bytes and MIME types,
invalid input and path escapes before bridge dispatch, and actionable busy/timeout errors.
Concurrent requests must retain distinct bridge request IDs and correctly correlated results.
Cancelling an MCP request must leave subsequent requests usable; it cannot undo an operation
already dispatched to Krita. Only the external Krita endpoint is substituted in these tests.
The CI matrix covers Python 3.12 on Windows/Ubuntu and the minimum supported Python 3.11 on Ubuntu.

Windows/Ubuntu CI also runs the real Claude Code CLI against the candidate wheel with an isolated
profile and a loopback model stub; see [Claude verification and limits](claude-installation.md#verification-and-limits).
Claude Desktop GUI rendering and live client-to-Krita operations remain manual checks.

## Official references

- [Codex MCP setup and configuration](https://learn.chatgpt.com/docs/extend/mcp)
- [Codex changelog](https://learn.chatgpt.com/docs/changelog)
- [Plugin packaging and compatibility formats](https://developers.openai.com/plugins/build/plugins)
- [MCP 2026-07-28 changes](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- [STDIO compatibility](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio#backward-compatibility)
- [Python SDK 2 changes](https://py.sdk.modelcontextprotocol.io/whats-new/)
- [Python SDK migration guide](https://py.sdk.modelcontextprotocol.io/migration/)
- [Codex 0.163.0 protocol selection](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/rmcp-client/src/protocol_mode.rs)
- [Codex 0.163.0 MCP environment filtering](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/rmcp-client/src/utils.rs)
