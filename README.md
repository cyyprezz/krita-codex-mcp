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
| `krita_edit` | Create/refine/move selections; native copy, cut, paste, selection-to-layer, fill and clear |
| `krita_document` | List, create, open, activate, checkpoint-save and explicitly close documents |
| `krita_layers` | Read/edit the layer tree and transform layers by stable UUID |
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

## Current status

- Protocol, configuration, dual input/output path policy, bridge client and thirteen-tool MCP surface
  implemented.
- Krita plugin implements state, preview, persistent layer-tree editing, native path painting, KRA
  save and PNG export.
- Offline unit, functional HTTP and contract tests cover protocol, path escapes, authentication,
  request limits, timeout/busy serialization, input validation and the absence of
  pixel/arbitrary-code painting.
- A live Windows run on Krita 5.3.2.1 verified state, direct PNG previews, stable layer UUIDs,
  pressure-aware native painting, two undo steps for two segments, dialog-free KRA/PNG output,
  authentication failure and allowed-root enforcement.
- A real thirteen-tool MCP STDIO session created its own disposable document, used `krita_draw`
  and a KRA-backed project palette, verified direct
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
- The creation live run verified native line/path/polygon/rectangle/ellipse painting, meaningful
  shape Undo/Redo, Krita-global clipboard actions, selection-to-layer workflows, exact RGBA color
  replacement through a temporary selection plus native fill, undoable filter strokes, and a
  document project palette that survived save/close/reopen. Free layer scale/rotate/shear/crop are
  not undoable in Krita 5.3.2.1's Python API, so each creates a hidden backup layer and returns its
  UUID. Because two direct node transforms in one open document crash that Krita build during
  teardown, a second is rejected until the KRA is saved, closed and reopened. Native layer mirror
  remains one normal Undo step and does not count toward that guard.
- The transform-mask live run verified non-destructive scale, rotate and shear on one asymmetric
  transparent layer. Each operation created a native `transformmask`, changed the direct PNG
  preview, survived a KRA save/close/reopen round trip with stable UUIDs, and could then be removed
  to restore the original projection. Krita 5.3.2.1 is unstable when a mask is repeatedly mutated
  or removed before the first reopen, so the bridge allows at most three masks, blocks same-session
  mask mutation/removal, and refuses to mix mask and direct layer-transform backends until Krita is
  restarted.
- A direct Codex-to-Krita asset workflow created and reopened a layered transparent 128x128 pickup,
  preserved its project palette and stable layer UUIDs, and verified that whole-canvas previews
  retain transparent margins. Closing the final document now reports no stale active UUID.

Krita 5.3.2.1's Python binding requires integer `QPoint` values for `Node.paintLine`; incoming
canvas coordinates are therefore rounded at the native API boundary. PNG export temporarily
enables document batch mode so no format dialog can block the local bridge.

See [Windows installation](docs/windows-installation.md), [protocol](docs/protocol.md),
[security](SECURITY.md), and the [Krita E2E checklist](docs/krita-e2e-checklist.md).

## Plugin structure

The Qt-main-thread bridge keeps `service.py` as a small composition and dispatch entrypoint.
Document, canvas, selection/editing, layer-tree operations, layer transforms, painting/drawing,
palette handling, color adjustments, animation/region assets, managed packages, shared context
and validation behavior live in focused sibling modules under `krita-plugin/kritacodexbridge`;
the public bridge operations and error payloads do not depend on that internal split.

The STDIO side follows the same boundary: `service.py` is a compatibility facade, while core,
editing, layer, drawing, color, asset and shared input validation live in focused modules under
`src/krita_codex_mcp`. Tool registration is grouped into core, creation and asset modules, with
`server.py` limited to composition and startup. This keeps the MCP schema stable without
rebuilding another monolith.

## Development

```powershell
uv sync --all-groups --python 3.12
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\ruff.exe check .
```

No GitHub remote or publishing step is part of installation or development.
