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

Only `state`, `preview`, `canvas`, `edit`, `document`, `layers`, `history`, `brush`, `paint_path`,
`draw`, `color`, `asset`, and `save_export` are valid operations. Request IDs are checked in both
directions. The bridge accepts one operation at a time and enforces request, response, preview,
import/asset-size, document-size, point-count and timeout limits.

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

`document` supports `list`, `create`, `open`, `activate`, `save`, and `close`. Open paths are
canonicalized and checked on both sides of the bridge. `save` is an in-place KRA checkpoint for a
document that already has an allowed `.kra` path; a new document must first use `save_export` with
a KRA path. Modified documents refuse to close unless `discard_changes=true` is explicit.
Document IDs are stable for the lifetime of the plugin and document, but are not persisted across
Krita restarts.

State, preview, canvas, edit, layer, history, brush, paint/draw, color and save/export requests
accept an optional `document_id`. Reads can inspect an inactive document without changing the
visible selection. View-dependent mutations first switch to the concrete Krita view that owns the
target document and fail closed if Krita cannot confirm the switch. Responses identify the
document they used.

`preview` returns either a bounded whole-canvas projection thumbnail or a bounded
document-projection region. Transparent outer margins remain part of the whole-canvas result.
An explicit `x`, `y`, `width`, `height` region may be enlarged by an integer scale of 1–16 using
nearest-neighbor sampling, while `max_width` and `max_height` remain hard output bounds.

`canvas` supports `read`, `sample_color`, `resize`, `scale`, `crop`, `rotate`, `flip`, and
`import_layer`. Color sampling reads one composited projection pixel and returns RGBA plus
`#RRGGBBAA`. Canvas resize uses one of nine explicit anchors; image scaling accepts only a strategy
reported by Krita, including `NearestNeighbor` on the validated Windows target. Crop rectangles
must stay inside the current canvas. Flip uses Krita's image-pixel actions rather than the
view-only mirror action.

`import_layer` accepts only existing raster files beneath allowed roots, checks encoded bytes and
decoded dimensions, and embeds the pixels into a new editable paint layer. The initial contract is
limited to RGBA/U8 destination documents so pixel layout conversion fails closed instead of
guessing. The import path is not retained as a file-layer dependency.

`edit` supports selection status, rectangle/all/opaque selection, replace/add/subtract/intersect
combination, deselect, invert, grow, shrink, selection-mask move/resize, fill, and clear. Native
`copy`, `cut`, and `paste` use Krita's global clipboard. `copy_to_layer` and `cut_to_layer` turn the
current selection into a new transformable paint layer without exposing raw clipboard bytes.
Paste creates a new paint layer and returns its UUID. Fill and clear require both a
non-empty document selection and an explicit paint-layer UUID; locked or non-paint layers fail
closed. They trigger Krita's native registered actions and report one expected undo step. Selection
mask changes increment `bridge_revision`, but are not presented as document-content undo steps.

`history` exposes bounded `undo`/`redo` steps through Krita's own registered actions. `brush`
reads or sets the native preset, size, opacity, flow, rotation, eraser/pressure state and managed
foreground color; preset listing is filtered and capped.

`layers` returns a bottom-to-top recursive tree with stable UUIDs, parent UUIDs, sibling indices,
canvas positions and content bounds. In addition to create/activate/rename/properties, it supports
native duplication, deletion, translation and reparenting/reordering. `reorder` moves to the root
when `parent_id` is omitted; `above_id` optionally names a direct child of the destination and an
omitted `above_id` inserts at the bottom. Cycles and non-group destinations fail closed.
Layer properties include visibility, opacity, lock, paint-layer alpha lock, alpha inheritance and
a conservative allow-list of stable Krita blending-mode IDs. Multi-property changes are verified
through getters and rolled back if Krita rejects any field. `merge_down` is intentionally limited
to two adjacent, visible, unlocked paint layers; it reports both consumed UUIDs and explicitly
states that Krita discards the source layer metadata. One normal Krita Undo restores the two
layers.

Layer transforms include scale, arbitrary rotation, shear, crop and horizontal/vertical mirror.
Scale strategies are checked against the running Krita build. Krita 5.3.2.1 does not put direct
`Node.scaleNode`, `rotateNode`, `shearNode`, or `cropNode` calls on its Undo stack; these four
actions therefore create a hidden full-layer backup first and return `backup_layer_id`. Restore by
showing that backup and removing/hiding the transformed layer. Mirror uses `mirrorNodeX/Y` and
reports one native Undo step. A second direct node transform in the same open document triggers a
native Krita 5.3.2.1 teardown crash even when it targets a different layer. The bridge therefore
fails closed with `UNSAFE_TRANSFORM_SEQUENCE`; save, close and reopen the KRA before applying the
next direct transform. Mirror remains available between direct transforms.

`draw` is a cohesive native-shape operation over `Node.paintLine`, `paintPath`, `paintPolygon`,
`paintRectangle`, and `paintEllipse`. It accepts explicit foreground/background stroke and fill
styles, optional preset and brush size, open or closed paths, and pressure for line endpoints.
Every successful call is one scripted-brush Undo step. The existing `paint_path` remains the
pressure-aware multi-segment API and reports one step per segment.

`color` can list/read installed Krita palettes and apply a swatch to foreground or background.
It also stores up to 256 named `#RRGGBB[AA]` entries as a private document annotation, so the
project palette persists inside the KRA without mutating global Krita resources. Exact/tolerant
RGBA/U8 replacement reads bounded layer pixels only to build a one-byte selection mask, then uses
Krita's native foreground-fill action; matching pixels remain selected so one Undo targets the
image mutation. `adjust` exposes a fixed filter allow-list, accepts only keys exposed by the
running filter configuration, and uses undoable `Filter.startFilter`, never direct `Filter.apply`.

`asset` supports `export_layer` for paint layers and groups. It clones the document, isolates the
target by its validated layer-index path, renders Krita's document projection and atomically
replaces the requested PNG only after encoding and size checks. The original document's visibility
and modified state are untouched. Canvas-sized and content-cropped output are supported, and the
MCP result includes a direct PNG preview. Krita 5.3.2.1's native `Node.save` path was explicitly
rejected after the live group export blocked the Qt main thread.

`asset` also supports `export_animation` for a group whose direct paint/group children are frames. The caller
can provide explicit frame UUID order or use Krita's bottom-to-top child order. It exports stable
numeric sequence filenames, an equal-cell spritesheet, a scaled direct sheet preview and a
`dev.desens.krita-codex.unity-animation` v1 manifest containing timing, loop, hashes, source IDs,
top-left PNG rectangles, bottom-left Unity rectangles, pivot and pixels-per-unit metadata. `canvas`
uses the full document; `union` computes one shared crop from every isolated frame's rendered alpha,
so frames do not jump because of per-frame trimming and empty individual frames remain valid.

The animation output path is a managed package directory containing `spritesheet.png`,
`manifest.json` and `frames/0000.png` onward. A new package is fully staged before it appears.
Re-export uses the ownership manifest as a fail-closed gate, transactionally replaces only known
generated files, preserves `.meta` sidecars and writes the manifest last in the staged package.
Unknown non-empty directories are never recursively overwritten. Frame-count, individual-file,
batch-byte, rendered-pixel, sheet-dimension and sheet-pixel limits are enforced.

`asset` additionally supports `export_tileset` and `export_regions`. A tileset uses an explicit or
auto-fitted top-left grid with tile size, origin and optional spacing. Named regions accept a
bounded request-ordered list of unique rectangles. Every rectangle must fit completely inside the
canvas. Both actions render one isolated full-canvas `spritesheet.png`, stable
`regions/0000.png` files and a `dev.desens.krita-codex.unity-regions` v1 manifest. The manifest
contains Point-filter Unity settings, PPU/pivot metadata, source identifiers, file hashes and both
top-left PNG and bottom-left Unity sprite rectangles. Optional `skip_empty` uses rendered alpha,
not RGB bytes, and preserves each retained region's source index.

Region output uses the same staged, schema-gated transaction as animation output. Animation and
region packages cannot overwrite one another, symbolic managed paths fail closed, only known
generated files are replaced and Unity `.meta` files remain untouched.

The canonical dependency-free protocol package is `src/krita_codex_protocol`. The Windows
installer copies that same package next to the Krita plugin, so the two processes use the same
validation code rather than hand-maintained wire-model duplicates.
