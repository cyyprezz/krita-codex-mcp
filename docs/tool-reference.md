# Tool behavior and limits

This guide documents the observable behavior shared by the public MCP tools. The private bridge
between the MCP process and Krita stays on authenticated localhost and is not a public network API.
Failures return a stable error code, a readable message, optional details and a `retryable` flag.

Only `state`, `preview`, `canvas`, `edit`, `document`, `layers`, `history`, `brush`, `paint_path`,
`draw`, `color`, `asset`, and `save_export` are valid operations. The bridge accepts one operation
at a time and enforces request, response, preview,
import/asset-size, document-size, point-count and timeout limits.

`bridge_revision` counts successful mutations performed through this bridge instance. It is not
a document revision and does not detect edits made manually in Krita or by another plugin.

`krita_state` includes `runtime_compatibility`, and `krita_document` supports the read-only
`compatibility` action even when no document ID is supplied. The report contains the running Krita
version, compatibility-profile version, `compatible`/`limited`/`incompatible`/`unknown` status,
per-feature availability and structured issues with affected MCP tools. An unknown application
version is capability-checked instead of rejected by name. A missing optional API limits only its
reported tools; missing document lifecycle, projection, persistence or stable layer-tree APIs are
critical. Native drawing never silently falls back to a direct pixel renderer.

Timeout selection is shared by the MCP client and installed bridge protocol package. Normal reads
and edits use `request_timeout_seconds` (30 seconds by default), `document.open` and
`document.save` use `open_save_timeout_seconds` (120 seconds), and `asset` plus `save_export` use
`export_timeout_seconds` (180 seconds). The MCP HTTP client waits five additional seconds for the
structured bridge response; the outer Codex tool timeout should therefore be at least 210 seconds.

If a main-thread operation is still running when its selected timeout expires, the bridge returns
`BRIDGE_TIMEOUT` with `details.operation_may_complete` set to `true`. It keeps later operations
serialized behind that call; they receive retryable `BRIDGE_BUSY` responses until actual
completion. An operation that had not begun before timeout reports `operation_may_complete=false`.

`paint_path` accepts 2–512 points with canvas-space `x`, `y`, and pressure in the range 0–1. It
uses one native `paintLine` call per adjacent pair. The response reports `undo_steps` explicitly;
the current contract expects one Krita undo step per segment.

The public request accepts numeric coordinates. On the tested Krita 5.3.2.1 binding they are
rounded to integer `QPoint` values because that build rejects `QPointF` for `Node.paintLine`.

`document` supports `list`, `compatibility`, `create`, `open`, `activate`, `save`, and `close`.
`compatibility` is read-only and may target the active document or an explicit document ID. Open
paths are canonicalized and checked on both sides of the bridge. `save` is an in-place KRA
checkpoint for a document that already has an allowed `.kra` path; a new document must first use
`save_export` with a KRA path. Modified documents refuse to close unless `discard_changes=true` is
explicit.
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
Preview waits for pending Krita work and refreshes the projection so visibility and reorder changes
are reflected before rendering. Preview and asset responses are size-limited, validated PNGs.

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

`layers` read calls return a bottom-to-top recursive tree with stable UUIDs, parent UUIDs, sibling
indices, canvas positions and content bounds. Mutations default to `response_mode="compact"` and
return only the affected layer summary; `response_mode="full"` opts back into the recursive tree.
In addition to create/activate/rename/properties, the operation supports native duplication,
deletion, translation and reparenting/reordering. `reorder` moves to the root when `parent_id` is
omitted; `above_id` optionally names a direct child of the destination and an omitted `above_id`
inserts at the bottom. Cycles and non-group destinations fail closed. Reparenting is verified after
Krita finishes and rolls back if the destination cannot be confirmed.
Layer properties include visibility, opacity, lock, paint-layer alpha lock, alpha inheritance and
a conservative allow-list of stable Krita blending-mode IDs. Multi-property changes are verified
through getters and rolled back if Krita rejects any field. `merge_down` is intentionally limited
to two adjacent, visible, unlocked paint layers; it reports both consumed UUIDs and explicitly
states that Krita discards the source layer metadata. One normal Krita Undo restores the two
layers.

Layer transforms include scale, arbitrary rotation, shear, crop and horizontal/vertical mirror.
Scale, rotate and shear default to `transform_mode="mask"`. The plugin creates a native Krita
`TransformMask`; no transform XML crosses the MCP boundary. Each call creates a separate mask and
returns its UUID, effective matrix and zero native Undo steps. Removing the mask after a KRA round
trip restores the unchanged source pixels.

Krita 5.3.2.1 becomes teardown-unstable when transform masks are repeatedly rewritten, replaced,
hidden/shown or removed in the same open session. The bridge therefore permits at most three
transform masks in a document, rejects a fourth with `UNSAFE_TRANSFORM_MASK_SEQUENCE`, and blocks
mutation or removal of a newly created mask with `UNSAFE_TRANSFORM_MASK_MUTATION`. Save, close and
reopen the KRA before removing a mask.

`transform_mode="direct"` remains an explicit compatibility fallback; crop is necessarily direct.
Scale strategies are checked against the running Krita build. Krita 5.3.2.1 does not put direct
`Node.scaleNode`, `rotateNode`, `shearNode`, or `cropNode` calls on its Undo stack, so each creates a
hidden full-layer backup and returns `backup_layer_id`. A second direct transform in one open
document fails with `UNSAFE_TRANSFORM_SEQUENCE`. For a saved KRA beneath an allowed root,
`checkpoint_reopen=true` requires an explicit `document_id` and makes the MCP process checkpoint,
close and reopen the KRA before transforming. It verifies the stable layer UUID and returns both
old and replacement document/layer IDs. Direct and mask layer-transform backends still may not be
mixed in one Krita process, and
direct mode is rejected while the target document contains transform masks.
`UNSAFE_TRANSFORM_BACKEND_MIX` requires using mask mode, safely removing masks, or saving all
documents and restarting Krita. Mirror uses `mirrorNodeX/Y`, reports one native Undo step, and is
exempt from both backend guards.

`draw` is a cohesive native-shape operation over `Node.paintLine`, `paintPath`, `paintPolygon`,
`paintRectangle`, and `paintEllipse`. It accepts explicit foreground/background stroke and fill
styles, optional preset and brush size, open or closed paths, and pressure for line endpoints.
Every successful call is one scripted-brush Undo step and includes
`verified_content_change=true`. On an initially empty layer, bounds/extents must become non-empty;
on an existing layer, content must visibly change. A native call that changes neither pixels nor
bounds fails with `DRAW_NO_CHANGE` and does not risk undoing an unrelated prior action. The existing
`paint_path` remains the pressure-aware multi-segment API and reports one step per segment.

`color` can list/read installed Krita palettes and apply a swatch to foreground or background.
It also stores up to 256 named `#RRGGBB[AA]` entries as a private document annotation, so the
project palette persists inside the KRA without mutating global Krita resources. Exact/tolerant
RGBA/U8 replacement leaves matching pixels selected and uses one undoable native fill action.
`adjust` exposes a fixed filter allow-list and accepts only keys supported by the running Krita
filter configuration.

`asset` supports `export_layer` for paint layers and groups. The original document's visibility and
modified state are untouched. Canvas-sized and content-cropped output are supported, replacement
is atomic, and the MCP result includes a direct PNG preview.

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
