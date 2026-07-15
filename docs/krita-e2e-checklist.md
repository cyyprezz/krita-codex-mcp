# Krita 5.3.2.1 end-to-end checklist

These checks require a real Krita GUI and are intentionally not simulated in the offline test
suite.

The automated live helpers cover the normal flow plus wrong-token, missing-preset,
out-of-bounds, group-layer, overwrite and outside-root failures:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe .\scripts\live_e2e.py --output-dir "C:\absolute\allowed\root"
.\.venv\Scripts\python.exe .\scripts\live_expansion_e2e.py --output-dir "C:\absolute\allowed\root"
.\.venv\Scripts\python.exe .\scripts\live_mcp_e2e.py
```

1. Create a 512×512 RGBA/U8 document through `krita_document`, list it, then open a KRA from an
   allowed root. Confirm document IDs remain stable while Krita stays open and outside-root input
   is rejected.
2. Add a paint layer and a group containing another paint layer. Run `krita_state`; confirm
   dimensions, color space, active UUID and recursive stacking order match Krita.
3. Rename, activate, hide/show and set a layer to 50% opacity through `krita_layers`. Confirm the
   returned UUID remains stable and the UI updates without a cross-thread Qt warning.
4. Run `krita_preview` at 256×256 and 1024×1024. Confirm Codex receives an image (not just a path),
   aspect ratio is preserved, and transparent areas remain transparent.
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
9. Try relative paths, `..` escapes, sibling-prefix paths, wrong extensions and a path outside all
   roots. Confirm both MCP-side and plugin-side policy reject them.
10. Change the token in only one process's config copy (or temporarily use a wrong token) and
   confirm authentication fails without executing an operation.
11. Attempt to close a modified document without discard permission and confirm `UNSAVED_CHANGES`.
    Then close it explicitly and retry all active-document categories; confirm
    `NO_ACTIVE_DOCUMENT` instead of plugin crashes or hangs.

Record the exact Krita version and whether `state.capabilities.native_paint_line` is true. The
current generated API reference labels `Node.paintLine` as a Krita 6.0 addition even though the
combined 5.3/6.0 release notes announce the painting API; runtime feature detection is therefore
a hard gate for the 5.3.2.1 target.

## Recorded Windows result

Krita `5.3.2.1 (git 0619060)` reported both native painting capabilities. A three-point pressure
ramp produced two native segments and required exactly two Undo commands: the preview hash after
the second Undo matched the pre-paint hash. KRA and PNG output completed without a dialog after
temporarily enabling document batch mode.

The expansion run created and listed a document with a stable root-node UUID, searched native
presets, applied managed foreground color plus brush settings, painted two segments, completed
two Undo and two Redo operations, refused an unsafe close, saved KRA/PNG, closed, reopened and
closed the document. The eight-tool STDIO check also created its own document and returned a PNG
preview containing `TextContent` plus `ImageContent` before cleaning up.

The locked-layer and forced mid-path rollback cases remain manual checklist items because the
bridge intentionally has no layer-lock mutation or arbitrary failure-injection tool.
