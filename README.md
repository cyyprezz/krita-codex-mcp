# krita-codex-mcp

A Windows-first local MCP server that lets Codex inspect and control Krita through a small,
authenticated bridge. The MCP process uses STDIO. The Krita Python plugin listens only on
`127.0.0.1`, validates a shared bearer token, and dispatches every Krita API call to Qt's main
thread.

This repository is a technical spike validated against Krita 5.3.2.1 on Windows. It deliberately
exposes a small set of cohesive tools instead of a large collection of micro-tools:

| Tool | Purpose |
|---|---|
| `krita_state` | Active document, canvas/color space, recursive layer tree with UUIDs, active layer and current painting state |
| `krita_preview` | Whole-canvas or pixel-precise cropped PNG returned directly as MCP image content |
| `krita_canvas` | Sample colors; resize, scale, crop, rotate or flip; import editable raster layers |
| `krita_edit` | Create/refine selections and fill or clear them on a paint layer |
| `krita_document` | List, create, open, activate, checkpoint-save and explicitly close documents |
| `krita_layers` | Read, create, duplicate, delete, reorder, translate, merge and edit layers by UUID |
| `krita_history` | Read undo/redo state and execute bounded undo or redo steps |
| `krita_brush` | Read/set native brush state and search installed Krita presets |
| `krita_paint_path` | Pressure-aware line segments through Krita's native `Node.paintLine` API and active brush preset |
| `krita_asset` | Export layer PNGs plus Unity-oriented animation, tileset and named-region packages with direct previews |
| `krita_save_export` | Save KRA and export PNG beneath configured roots only |

```text
Codex -> local STDIO MCP process -> authenticated 127.0.0.1 HTTP bridge
      -> queued, timeout-aware Qt main-thread dispatch -> Krita Python API
```

The bridge has no arbitrary Python execution tool and no direct pixel painting fallback. If the
installed Krita build does not expose `Node.paintLine`, painting fails closed with an
`UNSUPPORTED_FEATURE` error.

## Current status

- Protocol, configuration, dual input/output path policy, bridge client and eleven-tool MCP surface
  implemented.
- Krita plugin implements state, preview, persistent layer-tree editing, native path painting, KRA
  save and PNG export.
- Offline unit, functional HTTP and contract tests cover protocol, path escapes, authentication,
  request limits, timeout/busy serialization, input validation and the absence of
  pixel/arbitrary-code painting.
- A live Windows run on Krita 5.3.2.1 verified state, direct PNG previews, stable layer UUIDs,
  pressure-aware native painting, two undo steps for two segments, dialog-free KRA/PNG output,
  authentication failure and allowed-root enforcement.
- A real eleven-tool MCP STDIO session created its own disposable document, verified direct
  metadata plus PNG `ImageContent`, and closed the document again.
- The expansion live run verified stable in-session document IDs, preset search and brush state,
  native painting, bounded undo/redo, layer duplication/grouping/reordering/translation/deletion,
  explicit targeting with two open documents, unsaved-close refusal, KRA/PNG output, an in-place
  KRA checkpoint, isolated transparent group export, and close/reopen with the saved layer
  structure intact.
- Frame-group animation export produces equal-size transparent frame PNGs, a deterministic
  spritesheet and a Unity-oriented JSON manifest in one managed package directory. Explicit frame
  order, canvas/common-union crops, timing, loop, padding, pivot and pixels-per-unit metadata are
  supported. Re-export preserves Unity `.meta` sidecars for stable generated filenames.
- Tileset-grid and explicit-region export produce an isolated full-canvas spritesheet, stable
  numeric region PNGs and a Unity-oriented manifest with Point-filter settings, hashes, pivots and
  both PNG-top-left and Unity-bottom-left rectangles. Re-export is transactional and keeps `.meta`
  sidecars untouched.
- The editing live run verified cropped nearest-neighbor previews, composited RGBA sampling,
  rectangular and opaque-pixel selections, native fill/clear with undo, anchored canvas resize,
  nearest-neighbor scaling, crop/rotate/flip, and an embedded editable raster import. The import
  survived source deletion plus a KRA save/close/reopen round trip with the same layer UUID.

Krita 5.3.2.1's Python binding requires integer `QPoint` values for `Node.paintLine`; incoming
canvas coordinates are therefore rounded at the native API boundary. PNG export temporarily
enables document batch mode so no format dialog can block the local bridge.

See [Windows installation](docs/windows-installation.md), [protocol](docs/protocol.md),
[security](SECURITY.md), and the [Krita E2E checklist](docs/krita-e2e-checklist.md).

## Plugin structure

The Qt-main-thread bridge keeps `service.py` as a small composition and dispatch entrypoint.
Document, canvas, selection/editing, layer, painting, layer/animation/region asset, managed-package,
shared context and validation behavior live in focused sibling modules under
`krita-plugin/kritacodexbridge`; the public bridge operations and error payloads do not depend on
that internal split.

The STDIO side follows the same boundary: `service.py` is a compatibility facade, while core,
editing, layer, asset and shared input validation live in focused modules under
`src/krita_codex_mcp`. This keeps the MCP schema stable without rebuilding another monolith.

## Development

```powershell
uv sync --all-groups --python 3.12
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\ruff.exe check .
```

No GitHub remote or publishing step is part of installation or development.
