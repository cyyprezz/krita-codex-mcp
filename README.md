# Krita Codex MCP Server

<!-- mcp-name: io.github.cyyprezz/krita-codex-mcp -->

Control Krita from OpenAI Codex or Anthropic Claude through a secure local
Model Context Protocol (MCP) server for Windows and Linux.

[PyPI package](https://pypi.org/project/krita-codex-mcp/) ·
[GitHub releases](https://github.com/cyyprezz/krita-codex-mcp/releases) ·
[Windows installation guide](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/windows-installation.md) ·
[Linux installation guide](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/linux-installation.md) ·
[Codex and MCP compatibility](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/codex-compatibility.md)

A local MCP server that lets Codex and Claude inspect and control Krita through a small,
authenticated bridge. The MCP process uses STDIO. The Krita Python plugin listens only on
`127.0.0.1`, validates a shared bearer token, and dispatches every Krita API call to Qt's main
thread.

Windows is the original supported platform; Linux support is experimental.
For Claude Code and Claude Desktop setup, supported platforms and test limits, see the
[Claude installation guide](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/claude-installation.md).
Both clients use the same STDIO server and authenticated Krita bridge. The package and
Codex plugin retain their existing names.
Both platforms use a capability-based runtime compatibility check. Krita 5.3.2.1 on Windows is a
tested build, not a hard version requirement. The server deliberately
exposes a small set of cohesive tools instead of a large collection of micro-tools:

| Tool | Purpose |
|---|---|
| `krita_state` | Active document, canvas/color space, recursive layer tree with UUIDs, active layer and current painting state |
| `krita_preview` | Whole-canvas or pixel-precise cropped PNG returned directly as MCP image content |
| `krita_canvas` | Sample colors; resize, scale, crop, rotate or flip; import editable raster layers |
| `krita_edit` | Create/refine/move selections; native copy, cut, paste, selection-to-layer, fill and clear |
| `krita_document` | Inspect runtime compatibility; list, create, open, activate, save and close documents |
| `krita_layers` | Read/edit the layer tree, safely reparent, and transform layers by stable UUID |
| `krita_history` | Read undo/redo state and execute bounded undo or redo steps |
| `krita_brush` | Read/set native brush state and search installed Krita presets |
| `krita_paint_path` | Pressure-aware line segments through Krita's native `Node.paintLine` API and active brush preset |
| `krita_draw` | Native preset-backed lines, open/closed paths, polygons, rectangles and ellipses |
| `krita_color` | KRA-backed project palettes, Krita palette swatches, color replacement and safe correction filters |
| `krita_asset` | Export layer PNGs plus Unity-oriented animation, tileset and named-region packages with direct previews |
| `krita_save_export` | Save KRA and export PNG beneath configured roots only |

```text
Codex -> local STDIO MCP process -> authenticated 127.0.0.1 HTTP bridge
      -> queued, timeout-aware Qt main-thread dispatch -> Krita Python API
```

The bridge has no arbitrary Python execution tool and no direct pixel painting fallback. Actual
painting, shapes, replacement fills and correction filters go through native Krita actions or
stroke APIs. If the running Krita build lacks a feature, `krita_state` and
`krita_document(action="compatibility")` report the affected tools. Calling that feature returns a
structured error; the bridge never silently switches to a different rendering backend.

## Quick start

Choose the installation source for your platform:

| Platform | Installation source | Validation |
|---|---|---|
| Windows | PyPI `0.2.2` or a checkout | Windows CI; original live testing with Krita 5.3.2.1 |
| Linux | PyPI `0.2.2` or a checkout | Linux CI; contributor live test on Debian 13 / Krita 5.2.9 in limited mode |

Version `0.2.2` includes the Linux/XDG path and private-configuration fixes. Keep the installer,
Krita plugin and Codex server on that same exact version; the marketplace launcher also pins it.
Other Linux distributions and Krita builds require their own live preflight.

A first-time setup has two separate parts: the local bridge must be installed and enabled in Krita,
then connect Codex through its marketplace plugin or direct MCP configuration, or
[configure Claude](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/claude-installation.md).
The `codex plugin` commands do **not** install or enable anything in Krita.

Krita must include Python scripting, pass the live compatibility check, and have
[`uv`/`uvx`](https://docs.astral.sh/uv/getting-started/installation/) on `PATH`.
Run the MCP process on the same computer as Krita. Local Codex CLI, IDE and supported desktop
clients use local MCP configuration; a hosted ChatGPT web chat cannot connect through this
computer's `127.0.0.1` bridge by adding this GitHub repository.

### Windows: published package

1. Close Krita, make sure the allowed directory already exists, and install the current release
   from PyPI:

```powershell
uvx --from "krita-codex-mcp==0.2.2" `
  krita-codex-install install `
  --allowed-root "C:\Krita Work"
```

2. In Krita, open **Settings > Configure Krita > Python Plugin Manager** and enable
   **Krita Codex Bridge**.
3. Close and reopen Krita so the installed plugin and shared protocol package are loaded together.
4. Run the same version's live check:

```powershell
uvx --from "krita-codex-mcp==0.2.2" krita-codex-install check
```

5. Require either `Ready for productive use.` or the explicit limited-mode success message.
6. Choose exactly one Codex connection method below: the marketplace plugin or the direct MCP
   configuration.

### Linux: PyPI package

Close Krita, create a narrow allowed directory and install from PyPI:

```bash
mkdir -p "$HOME/krita-codex-work"
uvx --from "krita-codex-mcp==0.2.2" krita-codex-install install --allowed-root "$HOME/krita-codex-work"
```

Enable **Krita Codex Bridge** in Krita's **Python Plugin Manager**, close and reopen Krita, then run:

```bash
uvx --from "krita-codex-mcp==0.2.2" krita-codex-install check
```

Accept `compatible` or explicitly reported `limited` mode. Follow the
[Linux guide's Codex setup](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/linux-installation.md#4-connect-codex) for either connection method.
The installer uses XDG directories and keeps its token configuration private. No `sudo` is needed.

### Install the Codex plugin from GitHub

The repository includes a public, local-first Codex plugin. It bundles the guided `krita-control`
skill and starts the version-pinned PyPI MCP server through `uvx`; Krita and the authenticated bridge
continue to run only on the user's machine. Run these commands only after the Krita bridge has been
installed, enabled, restarted, and checked as described above. They register the GitHub marketplace
and install the Codex bundle; they do not modify the Krita installation or enable its Python plugin.

```powershell
codex plugin marketplace add cyyprezz/krita-codex-mcp
codex plugin add krita-codex-mcp@krita-codex
```

Start a new Codex task after installation, then verify `krita_state` and `krita_preview`. Plugin users
should not also add a separate `[mcp_servers.krita]` block because the plugin already provides that
server. For later repository releases, run `codex plugin marketplace upgrade krita-codex`, rerun
`codex plugin add krita-codex-mcp@krita-codex`, and start another new Codex task.

### Configure the MCP server directly

For either platform, use this alternative only after the Krita
bridge has passed the live check. Preview the checkout-free, exactly version-pinned `uvx` Codex
block with `krita-codex-install codex-config`. Apply it only after explicit confirmation, restart
Codex, verify `krita_state` and `krita_preview`, and only then start normal work.

Install and update never close Krita. If the authenticated bridge is running, replacement is
rejected before any installed files are changed and Krita must be closed manually. A busy local
bridge port also stops the installer before replacement begins.

For a personal installation, use the user-level `~/.codex/config.toml` on Linux or
`C:\Users\<you>\.codex\config.toml` on Windows. This is the
recommended option because the server is then available to all local Codex tasks and Git
worktrees. The generated block uses `uvx`, has no checkout `cwd`, and pins the same exact
distribution version as the installed plugin. The installer only changes that file after showing
the full preview and receiving explicit confirmation. Unmarked or conflicting Krita entries are
never overwritten. Never commit personal configuration. See the
[Windows installation guide](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/windows-installation.md#4-connect-codex) for details.

The generated launcher and marketplace configuration explicitly forward custom XDG directories.
Codex and Krita must inherit the same values. For unreleased source changes, use the
checkout-specific alternative in the [Linux guide](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/linux-installation.md#4-connect-codex).

### Current Codex and MCP compatibility

The compatibility check uses Codex CLI `0.163.0` (released 2026-10-15) against the built wheel and
checks discovery of all 13 tools without an API key, model request or running Krita. See
[the compatibility notes](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/codex-compatibility.md) for its exact scope.

This server intentionally stays on the MCP Python SDK 1.x maintenance line (`mcp<2`). Current Codex
retains the legacy STDIO handshake used by this SDK. The newer MCP `2026-07-28` protocol and
portable plugin format do not by themselves require replacing this working local integration.
The existing Codex plugin manifest remains a supported compatibility format; full support for
newer MCP protocol features would be a separate, tested SDK migration.

The preflight checks local configuration, the complete installed hash manifest, wheel/plugin/
protocol versions, loopback port, authentication and the running Krita capability profile. The
running plugin reports the distribution, protocol and Krita versions without adding an MCP tool.
The check creates a disposable document, verifies a new paint layer, native drawing, Undo/Redo,
transactional reparenting, direct PNG preview bytes and temporary KRA/PNG output beneath an allowed
root. It restores the previously active document and removes every temporary file.

An unknown Krita version is accepted when all critical capabilities pass. Missing optional
features produce `limited` warnings naming the affected MCP tools; missing projection, persistence,
stable layer-tree or document-lifecycle APIs remain critical. The same structured report is
available to Codex through `krita_state`, so it can avoid unavailable operations.

## Capabilities and Krita limits

The current release supports native preset-backed painting and shapes, persistent document and
layer editing, selections, color operations, KRA/PNG output, and Unity-oriented animation,
tileset and named-region packages. Preview and asset operations return PNG images directly to the
MCP caller. Re-export preserves Unity `.meta` sidecars for stable generated filenames.

The tested Krita 5.3.2.1 build imposes several safety limits. Other builds are evaluated at runtime
and may report different available features:

- Free layer scale, rotate, shear and crop operations are not native Undo commands. The bridge
  creates a hidden backup layer and blocks a second direct transform until the KRA is saved,
  closed and reopened. `checkpoint_reopen=true` can perform that round trip automatically.
- Transform masks are limited to three per open document. Same-session mask mutation/removal and
  mixing mask and direct transform backends are blocked until Krita restarts.
- `Node.paintLine` requires integer `QPoint` values, so incoming canvas coordinates are rounded at
  the native API boundary.
- PNG export temporarily enables Krita batch mode so no format dialog can block the local bridge.

## Timeouts and production guardrails

Timeouts are operation-specific across Codex, the MCP `BridgeClient`, the local HTTP bridge and
Qt's main-thread dispatcher:

| Operation class | Dispatcher timeout |
|---|---:|
| Reads and normal edits | 30 seconds |
| Document open and checkpoint save | 120 seconds |
| KRA/PNG, layer, animation, tileset and region exports | 180 seconds |

The recommended Codex `tool_timeout_sec` is 210 seconds so the MCP client can receive the bridge's
structured result. If a timed-out Qt operation is already running, `operation_may_complete=true`
is preserved and later calls receive `BRIDGE_BUSY` until Krita actually finishes.

Keep `allowed_roots` as narrow as practical, keep ordinary versioned or manual KRA backups, and
do not treat the bridge timeout as cancellation. The bridge is local-only and binds exactly to
`127.0.0.1`.

See [Windows installation](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/windows-installation.md),
[Linux installation](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/linux-installation.md), [Codex/MCP compatibility](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/codex-compatibility.md),
[tool behavior and limits](https://github.com/cyyprezz/krita-codex-mcp/blob/main/docs/tool-reference.md), [security](https://github.com/cyyprezz/krita-codex-mcp/blob/main/SECURITY.md), and the
[GitHub releases](https://github.com/cyyprezz/krita-codex-mcp/releases).

## License

Released under the [MIT License](https://github.com/cyyprezz/krita-codex-mcp/blob/main/LICENSE). Copyright (c) 2026 Nicklas Desens.
