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
| `krita_preview` | Scaled PNG returned directly as MCP image content |
| `krita_document` | List, create, open, activate and explicitly close documents with input/path and size policy |
| `krita_layers` | Read, create, activate, rename, show/hide and set opacity |
| `krita_history` | Read undo/redo state and execute bounded undo or redo steps |
| `krita_brush` | Read/set native brush state and search installed Krita presets |
| `krita_paint_path` | Pressure-aware line segments through Krita's native `Node.paintLine` API and active brush preset |
| `krita_save_export` | Save KRA and export PNG beneath configured roots only |

```text
Codex -> local STDIO MCP process -> authenticated 127.0.0.1 HTTP bridge
      -> queued, timeout-aware Qt main-thread dispatch -> Krita Python API
```

The bridge has no arbitrary Python execution tool and no direct pixel painting fallback. If the
installed Krita build does not expose `Node.paintLine`, painting fails closed with an
`UNSUPPORTED_FEATURE` error.

## Current status

- Protocol, configuration, dual input/output path policy, bridge client and eight-tool MCP surface
  implemented.
- Krita plugin implements state, preview, basic layers, native path painting, KRA save and PNG
  export.
- Offline unit, functional HTTP and contract tests cover protocol, path escapes, authentication,
  request limits, timeout/busy serialization, input validation and the absence of
  pixel/arbitrary-code painting.
- A live Windows run on Krita 5.3.2.1 verified state, direct PNG previews, stable layer UUIDs,
  pressure-aware native painting, two undo steps for two segments, dialog-free KRA/PNG output,
  authentication failure and allowed-root enforcement.
- A real eight-tool MCP STDIO session created its own disposable document, verified direct
  metadata plus PNG `ImageContent`, and closed the document again.
- The expansion live run verified stable in-session document IDs, preset search and brush state,
  native painting, bounded undo/redo, unsaved-close refusal, KRA/PNG output, close and reopen.

Krita 5.3.2.1's Python binding requires integer `QPoint` values for `Node.paintLine`; incoming
canvas coordinates are therefore rounded at the native API boundary. PNG export temporarily
enables document batch mode so no format dialog can block the local bridge.

See [Windows installation](docs/windows-installation.md), [protocol](docs/protocol.md),
[security](SECURITY.md), and the [Krita E2E checklist](docs/krita-e2e-checklist.md).

## Development

```powershell
uv sync --all-groups --python 3.12
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\ruff.exe check .
```

No GitHub remote or publishing step is part of installation or development.
