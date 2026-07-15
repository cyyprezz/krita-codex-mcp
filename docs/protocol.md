# Bridge protocol v1

The transport between the external MCP process and Krita is intentionally private and small. It
is JSON over HTTP/1.1 on `127.0.0.1`; every request uses `Authorization: Bearer <token>`.

Endpoint: `POST /v1/execute`

```json
{
  "protocol_version": "1",
  "request_id": "a UUID",
  "operation": "state",
  "arguments": {}
}
```

Successful response:

```json
{
  "protocol_version": "1",
  "request_id": "the same UUID",
  "ok": true,
  "result": {"bridge_revision": 0}
}
```

Structured failure:

```json
{
  "protocol_version": "1",
  "request_id": "the same UUID",
  "ok": false,
  "error": {
    "code": "NO_ACTIVE_DOCUMENT",
    "message": "Krita has no active document",
    "details": {},
    "retryable": false
  }
}
```

Only `state`, `preview`, `document`, `layers`, `history`, `brush`, `paint_path`, and `save_export`
are valid operations. Request IDs are checked in both directions. The bridge accepts one
operation at a time and enforces request, response, preview, document-size, point-count and
timeout limits.

`bridge_revision` counts successful mutations performed through this bridge instance. It is not
a document revision and does not detect edits made manually in Krita or by another plugin.

If a main-thread operation is still running when the request timeout expires, the bridge returns
`BRIDGE_TIMEOUT` with `details.operation_may_complete` set to `true`. It keeps later operations
serialized behind that call; they receive retryable `BRIDGE_BUSY` responses until completion.

`paint_path` accepts 2–512 points with canvas-space `x`, `y`, and pressure in the range 0–1. It
uses one native `paintLine` call per adjacent pair. The response reports `undo_steps` explicitly;
the first spike expects one Krita undo step per segment and the live checklist verifies that.

The public request accepts numeric coordinates. At the Krita 5.3.2.1 binding boundary they are
rounded to integer `QPoint` values because that version rejects `QPointF` for `Node.paintLine`.

`document` supports `list`, `create`, `open`, `activate`, and `close`. Open paths are canonicalized
and checked on both sides of the bridge. Modified documents refuse to close unless
`discard_changes=true` is explicit. Document IDs are stable for the lifetime of the plugin and
document, but are not persisted across Krita restarts.

`history` exposes bounded `undo`/`redo` steps through Krita's own registered actions. `brush`
reads or sets the native preset, size, opacity, flow, rotation, eraser/pressure state and managed
foreground color; preset listing is filtered and capped.

The canonical dependency-free protocol package is `src/krita_codex_protocol`. The Windows
installer copies that same package next to the Krita plugin, so the two processes use the same
validation code rather than hand-maintained wire-model duplicates.
