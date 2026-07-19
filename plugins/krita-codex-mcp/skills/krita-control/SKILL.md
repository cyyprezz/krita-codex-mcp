---
name: krita-control
description: Control a local Krita session through Krita Codex MCP. Use when Codex needs to inspect or modify Krita documents, preview canvases, manage layers, paint or draw, edit selections and colors, use undo or redo, save KRA files, export PNG files, or package animation, tileset, and named-region assets.
---

# Krita Control

Use the bundled `krita` MCP server. Keep the workflow local-first and preserve the user's active
document, filesystem boundaries, and undo history.

## Start with a verified session

1. Call `krita_state` before any edit.
2. Read the returned connection, document, layer, and compatibility information. Stop if the bridge
   is unavailable or the requested feature is incompatible.
3. Call `krita_preview` before changing visible content. Use a cropped preview when the task targets
   a small region.
4. Reuse the returned `document_id` and `layer_id` values. Do not guess identifiers from names.

If the bridge is unavailable, explain that the user must install and enable Krita Codex Bridge,
restart Krita, and run `krita-codex-install check`. Do not install software, change allowed roots,
or edit user-wide Codex configuration without explicit permission.

## Choose the narrowest tool

- Use `krita_state` and `krita_preview` for inspection.
- Use `krita_document` for compatibility, document lifecycle, and KRA saves.
- Use `krita_canvas` for canvas geometry, color sampling, and raster-layer imports.
- Use `krita_layers` for layer creation, activation, properties, ordering, and transforms.
- Use `krita_brush`, `krita_paint_path`, and `krita_draw` for native Krita marks and shapes.
- Use `krita_edit` for selections, clipboard operations, fills, and clears.
- Use `krita_color` for project colors, palettes, replacement, and filters.
- Use `krita_history` for bounded undo and redo.
- Use `krita_save_export` for KRA and PNG output.
- Use `krita_asset` for layer, animation, tileset, and named-region packages.

## Apply edits safely

1. Serialize mutating tool calls. Do not edit the same document concurrently.
2. State the exact document, layer, region, and output path before a destructive operation.
3. Keep `overwrite=false` unless the user explicitly authorizes replacing the exact target.
4. Keep every input and output path beneath the configured allowed roots. Never widen those roots as
   a convenience.
5. Call `krita_preview` after each meaningful visual change. Call `krita_state` after layer-tree or
   document-lifecycle changes.
6. Save a KRA checkpoint before a long or hard-to-reverse sequence when the user has provided an
   allowed destination.

Treat a timeout during a mutation as an unknown outcome. Do not retry the mutation automatically.
If the response reports `operation_may_complete=true` or `BRIDGE_BUSY`, wait for the bridge to
become responsive, then inspect with `krita_state` and `krita_preview` before deciding what remains.

## Finish with evidence

Verify the final visible result with `krita_preview`. When saving or exporting, report the exact
paths returned by the tool and distinguish a saved KRA checkpoint from derived PNG or asset output.
