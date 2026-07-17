# Krita Codex MCP Server

<!-- mcp-name: io.github.cyyprezz/krita-codex-mcp -->

Control Krita from OpenAI Codex through a secure local
Model Context Protocol (MCP) server for Windows.

[PyPI package](https://pypi.org/project/krita-codex-mcp/) ·
[GitHub releases](https://github.com/cyyprezz/krita-codex-mcp/releases) ·
[Windows installation guide](docs/windows-installation.md)

A Windows-first local MCP server that lets Codex inspect and control Krita through a small,
authenticated bridge. The MCP process uses STDIO. The Krita Python plugin listens only on
`127.0.0.1`, validates a shared bearer token, and dispatches every Krita API call to Qt's main
thread.

This repository is a Windows-first integration validated against Krita 5.3.2.1. It deliberately
exposes a small set of cohesive tools instead of a large collection of micro-tools:

| Tool | Purpose |
|---|---|
| `krita_state` | Active document, canvas/color space, recursive layer tree with UUIDs, active layer and current painting state |
| `krita_preview` | Whole-canvas or pixel-precise cropped PNG returned directly as MCP image content |
| `krita_canvas` | Sample colors; resize, scale, crop, rotate or flip; import editable raster layers |
| `krita_edit` | Create/refine/move selections; native copy, cut, paste, selection-to-layer, fill and clear |
| `krita_document` | List, create, open, activate, checkpoint-save and explicitly close documents |
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
stroke APIs. If the installed Krita build lacks a required method, the operation fails closed.

## Quick start

This requires Krita 5.3.2.1 and [`uv`/`uvx`](https://docs.astral.sh/uv/getting-started/installation/)
on `PATH`. Close Krita, make sure the allowed directory already exists, and install the current
release from PyPI:

```powershell
uvx --from "krita-codex-mcp==0.1.0" `
  krita-codex-install install `
  --allowed-root "C:\Krita Work"
```

Then use this order for a real Krita session:

1. Enable **Krita Codex Bridge** in Krita's Python Plugin Manager.
2. Restart Krita so the installed plugin and shared protocol package are loaded together.
3. Run the same version's `krita-codex-install check` and require
   `Ready for productive use.`
4. Preview the checkout-free, exactly version-pinned `uvx` Codex block with
   `krita-codex-install codex-config`.
5. Apply it only after explicit confirmation, restart Codex, verify `krita_state` and
   `krita_preview`, and only then start normal work.

Install and update never close Krita. If the authenticated bridge is running, replacement is
rejected before any installed files are changed and Krita must be closed manually. A busy local
bridge port also stops the installer before replacement begins.

For a personal installation, use the user-level `C:\Users\<you>\.codex\config.toml`. This is the
recommended option because the server is then available to all local Codex tasks and Git
worktrees. The generated block uses `uvx`, has no checkout `cwd`, and pins the same exact
distribution version as the installed plugin. The installer only changes that file after showing
the full preview and receiving explicit confirmation. Unmarked or conflicting Krita entries are
never overwritten. Never commit personal configuration. See the
[Windows installation guide](docs/windows-installation.md#4-configure-codex) for details.

The preflight checks local configuration, the complete installed hash manifest, wheel/plugin/
protocol versions, loopback port, authentication and `krita_state`. The running plugin reports the
distribution and protocol version loaded by Krita without adding an MCP tool. The check creates a
disposable document, validates direct PNG preview bytes, exports a temporary PNG beneath an allowed
root, closes the document and removes the file. It restores the previously active document and does
not edit Codex configuration or contact an external service.

## Capabilities and Krita limits

The current release supports native preset-backed painting and shapes, persistent document and
layer editing, selections, color operations, KRA/PNG output, and Unity-oriented animation,
tileset and named-region packages. Preview and asset operations return PNG images directly to the
MCP caller. Re-export preserves Unity `.meta` sidecars for stable generated filenames.

Krita 5.3.2.1 imposes several safety limits:

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

See [Windows installation](docs/windows-installation.md),
[tool behavior and limits](docs/tool-reference.md), [security](SECURITY.md), and the
[GitHub releases](https://github.com/cyyprezz/krita-codex-mcp/releases).

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Nicklas Desens.
