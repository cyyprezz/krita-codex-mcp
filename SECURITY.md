# Security model

This spike is designed for one Windows user controlling one local Krita process.

- The MCP transport is STDIO; the internal bridge binds only to `127.0.0.1`.
- Every bridge request requires a random bearer token from the current user's LocalAppData.
- The protocol has a fixed operation allow-list. There is no arbitrary Python, shell or Krita
  script execution operation.
- Both the MCP process and Krita plugin validate open/save/export paths after canonicalization.
  Input files and `.kra`/`.png` outputs must stay beneath configured roots; output parents must
  exist and existing files require explicit overwrite permission.
- Request, response, preview-image, exported-asset, animation-frame, batch-byte, render-pixel,
  document-dimension, document-pixel, point-count and timeout limits are enforced. Mutations are
  serialized and dispatched to Krita's Qt main thread.
- New animation and region packages are staged before their final directory appears. Re-export
  only updates known generated files inside a directory carrying the exact expected ownership
  schema; cross-kind/unknown packages and symbolic managed paths fail closed, while Unity `.meta`
  sidecars are preserved.
- Closing a modified document fails unless `discard_changes=true` is explicit.
- Native painting uses `Node.paintLine`; there is no `setPixelData` painting fallback. Bounded
  editable raster import is the only operation that copies decoded image bytes into a paint layer,
  and it is restricted to allowed input paths, byte/pixel limits and RGBA/U8 destinations.

The token protects against accidental or unrelated localhost callers, not against malware running
as the same Windows user. Keep `%LOCALAPPDATA%\krita-codex-mcp\config.json` private. Choose narrow
allowed roots rather than an entire drive. Do not expose the port through firewall rules, proxies,
containers or port forwarding.

For the technical spike, layer changes and native painting rely on Krita's own undo behavior. The
live checklist must pass before using the bridge on irreplaceable work. Keep normal KRA backups.
