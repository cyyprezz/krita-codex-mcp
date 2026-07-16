# Krita 5.3.2.1 end-to-end checklist

These checks require a real Krita GUI and are intentionally not simulated in the offline test
suite.

The automated live helpers cover the normal flow plus wrong-token, missing-preset,
out-of-bounds, group-layer, overwrite and outside-root failures:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe .\scripts\live_e2e.py --output-dir "C:\absolute\allowed\root"
.\.venv\Scripts\python.exe .\scripts\live_expansion_e2e.py --output-dir "C:\absolute\allowed\root"
.\.venv\Scripts\python.exe .\scripts\live_editing_e2e.py --output-dir "C:\absolute\allowed\root"
.\.venv\Scripts\python.exe .\scripts\live_regions_e2e.py --output-dir "C:\absolute\allowed\root"
.\.venv\Scripts\python.exe .\scripts\live_creation_e2e.py --output-dir "C:\absolute\allowed\root"
.\.venv\Scripts\python.exe .\scripts\live_mcp_e2e.py
```

1. Create a 512×512 RGBA/U8 document through `krita_document`, list it, then open a KRA from an
   allowed root. Confirm document IDs remain stable while Krita stays open and outside-root input
   is rejected.
   Open a second decoy document and verify targeted reads and mutations affect only the requested
   `document_id`. Save the target in place with `action=save` after its first KRA save.
2. Add a paint layer and a group containing another paint layer. Run `krita_state`; confirm
   dimensions, color space, active UUID and recursive stacking order match Krita.
3. Rename, activate, hide/show and set a layer to 50% opacity through `krita_layers`. Confirm the
   returned UUID remains stable and the UI updates without a cross-thread Qt warning.
   Duplicate and translate that layer, move it into a group, delete a disposable duplicate, then
   save/reopen the KRA and confirm names, nesting and positions persist.
   Set lock, alpha lock, inherit alpha and a supported blending mode, and verify every returned
   property. Reject alpha lock on a group and an unknown blending mode without partial mutation.
   Merge two adjacent visible/unlocked paint layers, verify the projection stays pixel-identical,
   then Undo once and confirm both original UUIDs return. Locked or hidden neighbors must refuse.
4. Run `krita_preview` at 256×256 and 1024×1024. Confirm Codex receives an image (not just a path),
   aspect ratio is preserved, and transparent areas remain transparent. Request a small projection
   region with integer scale and verify exact PNG dimensions and nearest-neighbor pixel blocks.
5. Use `krita_brush` to search presets and set foreground, size, opacity, flow and pressure mode.
   Confirm the UI and returned state agree. Draw a two-point and multi-point pressure ramp with
   `krita_paint_path`. Confirm preset texture/dynamics are present and no soft-circle pixel
   renderer behavior appears.
6. Use `krita_history` for undo and redo. Confirm the returned completed count and preview match
   Krita's actual undo stack; native path painting currently creates one step per segment.
7. Try painting on a group, locked paint layer and out-of-bounds point. Confirm structured errors
   and no partial canvas mutation. Force a mid-path failure if practical and verify completed
   segments are rolled back.
8. Save a KRA and export a transparent PNG inside an allowed root. Verify both files open. Repeat
   without `overwrite=true` and confirm refusal.
   Export a paint layer and a nested frame group with `krita_asset`; verify full-canvas/content
   dimensions, transparent corner pixels and direct MCP `ImageContent` previews.
   Export two direct children of a frame group with `action=export_animation`; verify explicit
   order, equal-size sequence PNGs, common alpha-union crop, sheet layout, PNG-top-left to
   Unity-bottom-left rectangles, hashes and timing/pivot/PPU metadata. Re-export with
   `overwrite=true` and confirm generated Unity `.meta` sidecars survive; without overwrite it
   must refuse.
   Export a two-cell grid with `action=export_tileset` and two request-ordered rectangles with
   `action=export_regions`. Verify exact tile pixels, sheet/region hashes, auto-fit grid dimensions,
   PNG/Unity rectangles, direct preview, schema-gated overwrite and preserved sheet/region `.meta`
   files.
9. Try relative paths, `..` escapes, sibling-prefix paths, wrong extensions and a path outside all
   roots. Confirm both MCP-side and plugin-side policy reject them.
10. Change the token in only one process's config copy (or temporarily use a wrong token) and
   confirm authentication fails without executing an operation.
11. Create a rectangular selection, fill it with an explicit RGBA color, clear it, and undo the
    clear. Sample pixels inside and outside the selection through `krita_canvas`; verify only the
    intended region changes. Deselect, select the layer's opaque pixels, and verify the returned
    bounds. Exercise add/subtract/intersect manually for irregular combinations.
12. Grow the canvas with a center anchor, scale with `NearestNeighbor`, flip horizontally and
    vertically, crop, and rotate 90 degrees. Use asymmetric marker pixels to verify image pixels,
    not only the view, moved to the expected coordinates.
13. Import a small alpha PNG as an editable layer, delete the source PNG, save the KRA, close and
    reopen it. Confirm the imported layer remains a paint layer, retains its UUID and pixels, and
    is not a linked file layer. Wrong extensions, oversized images and outside-root inputs must
    fail before creating a layer.
14. Attempt to close a modified document without discard permission and confirm `UNSAVED_CHANGES`.
    Then close it explicitly and retry all active-document categories; confirm
    `NO_ACTIVE_DOCUMENT` instead of plugin crashes or hangs.
15. Copy, cut and paste an active rectangular selection through `krita_edit`. Confirm paste creates
    a new paint layer with a returned UUID, and that `copy_to_layer` and `cut_to_layer` create
    addressable layers. Undo the cut and confirm the source pixels return. Move and resize the
    selection mask and verify the image pixels remain unchanged until a later edit uses it.
16. Exercise scale, rotate, shear and crop in separate disposable documents through
    `krita_layers`; each transformed layer must remain addressable by UUID and return a hidden
    full-layer backup UUID because those direct node APIs add no native Undo command. In one
    document, apply one direct transform and confirm a second fails with
    `UNSAFE_TRANSFORM_SEQUENCE` until save/close/reopen. Confirm flip still adds one native Undo
    command and does not consume the direct-transform allowance.
17. Draw a line, open and closed path, rectangle, ellipse and polygon through `krita_draw`. Verify
    native preset texture/dynamics, stroke/fill styles and one `Scripted Brush Stroke` Undo entry
    per shape. Reject unsupported style names and malformed geometry before painting.
18. Store a named project palette through `krita_color`, save, close and reopen the KRA, then read
    the same entries from the document annotation. List and read installed Krita palettes without
    mutating the global resources. Apply a project swatch and confirm the foreground changes.
19. Replace an exact color and a tolerance-matched color through `krita_color`. Confirm only the
    matching pixels change, the matching selection remains active, and one Undo restores the
    original pixels. Deselect when finished. Apply an allowlisted correction filter such as
    `invert` or `levels` and confirm one native Undo restores the previous projection.

Record the exact Krita version and whether `state.capabilities.native_paint_line` is true. The
current generated API reference labels `Node.paintLine` as a Krita 6.0 addition even though the
combined 5.3/6.0 release notes announce the painting API; runtime feature detection is therefore
a hard gate for the 5.3.2.1 target.

## Recorded Windows result

Krita `5.3.2.1 (git 0619060)` reported both native painting capabilities. A three-point pressure
ramp produced two native segments and required exactly two Undo commands: the preview hash after
the second Undo matched the pre-paint hash. KRA and PNG output completed without a dialog after
temporarily enabling document batch mode.

The expansion run created a target plus a decoy document and read the inactive target by explicit
UUID. It switched the concrete Krita view only for target mutations, searched native presets,
painted two segments, completed two Undo and two Redo operations, exercised persistent layer-tree
editing, refused an unsafe close, saved KRA/PNG, renamed a layer, checkpoint-saved the existing
KRA, exported an isolated 320×240 group PNG with a transparent corner, closed, reopened and
verified the checkpointed layer structure before closing both documents. The thirteen-tool STDIO
check also created its own document and returned a PNG preview containing `TextContent` plus
`ImageContent`, exercised native ellipse drawing and stored a project palette before cleaning up.

The animation-package extension additionally exported two explicitly ordered frame layers as
individual PNGs and a padded spritesheet, derived the shared crop from isolated rendered alpha,
validated the manifest hashes and Unity rectangle conversion, then re-exported while preserving
test `.meta` sidecars.

The editing extension created a transparent target layer and verified a 32×32 nearest-neighbor
preview of an 8×8 source region, exact composited color sampling, rectangular fill/clear and one
Undo for clear, selection from layer opacity, centered resize, nearest-neighbor scale, horizontal
pixel flip, crop and 90-degree rotation. It embedded a 2×2 alpha PNG, deleted the source, saved and
reopened the KRA, then confirmed the imported paint layer retained both pixels and UUID.

The region extension set and read lock, alpha lock, inherit alpha and Multiply, rejected alpha lock
on a group and an unknown blending mode, exported exact red/blue grid cells plus request-ordered
named regions, checked hashes and Unity rectangle conversion, preserved generated `.meta`
sidecars across an auto-fit re-export, refused a non-overwrite and a locked-layer merge, and
verified pixel-identical merge-down plus Undo restoration of both source UUIDs. Forced mid-path
painting rollback remains a manual checklist item because the bridge intentionally has no
arbitrary failure-injection tool.

The creation extension drew native rectangle, ellipse, polygon, open/closed path and pressure-line
shapes, then verified rectangle Undo and Redo. Exact color replacement changed 192 pixels and one
Undo restored them; an undoable `invert` filter behaved the same way. Clipboard copy/paste,
copy-to-layer and cut-to-layer returned stable layer UUIDs, and cut was undoable. Direct scale,
rotate, shear and crop were confirmed non-undoable on Krita 5.3.2.1 and each returned a hidden
backup layer UUID in an isolated document; a second direct transform in one open document was
rejected before mutation to avoid the reproduced native teardown crash. Flip remained natively
undoable. A three-entry `Hero Core` project palette survived a KRA save, close and reopen round
trip.

A direct Codex MCP workflow created a 128x128 three-layer energy pickup, stored four project
colors, exported KRA plus PNG, and reopened the KRA with the same three layer UUIDs and palette.
After clearing Krita's initially opaque background, exported corner pixels had alpha zero. The
whole-canvas preview retained the full 128x128 transparent canvas and scaled to 64x64 on request;
closing the last document returned an empty list with `active_document_id=null`.
