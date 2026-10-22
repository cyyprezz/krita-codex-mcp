# Security model

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Send a private report to
`nicklas@desens.dev` with affected versions, reproduction steps and potential impact. Please allow
time for assessment and a fix before public disclosure.

## Supported versions

This project is pre-1.0. Security fixes are provided for the current `main` branch only.

## Design and operating boundaries

This project is designed for one Windows user controlling one local Krita process.

The experimental Linux checkout installation uses the same per-user, loopback-only model.
Its installer makes the application configuration directory private (`0700`) and creates token
files with mode `0600` before writing them, including staged and recovered copies. Runtime
configurations selected manually with `KRITA_CODEX_CONFIG` must also be kept private. See the
[Linux installation guide](docs/linux-installation.md) for paths and token replacement after an
older installation may have exposed its configuration to another local user.

- The MCP transport is STDIO; the internal bridge binds only to `127.0.0.1`.
- Every bridge request requires a random bearer token from the current user's LocalAppData.
- The wheel and generated installation manifest contain no bearer token or local configuration.
  Manifest paths are relative and hash only the managed plugin, protocol and desktop payload.
- Install and update refuse to replace files while the authenticated bridge is active. The
  installer never closes Krita. Runtime responses identify the distribution and wire-protocol
  versions actually loaded by Krita, and the MCP client fails closed on a mismatch.
- On Windows, install, update and uninstall require exclusive access to every configured local
  bridge port. A busy or uncertain port blocks the command before managed files are changed.
- Install, update and uninstall are serialized and transactional. An interrupted change is
  recovered before the next mutation; if safe recovery is impossible, recovery files are retained
  and their locations are reported.
- A previewed Codex configuration change is applied only if both file existence and content still
  match the preview snapshot immediately before backup and replacement.
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
- Native painting and shapes use Krita's painting API; there is no direct pixel-painting fallback.
  Color replacement uses a bounded temporary selection mask and a native fill action. Editable
  raster import is restricted to allowed input paths, byte/pixel limits and RGBA/U8 destinations.

Copy/cut/paste intentionally use Krita's process-global clipboard, so they may replace clipboard
content visible to the same Windows user. Free Node transforms are not undoable through Krita's
Python API; the bridge creates a hidden backup layer before those operations instead. Krita
5.3.2.1 crashes when two such direct transforms are applied before document teardown, so the
bridge rejects the second until that KRA has been saved, closed and reopened. Project palette
annotations are size- and entry-limited when both writing and reading a KRA.

The token protects against accidental or unrelated localhost callers, not against malware running
as the same Windows user. Keep `%LOCALAPPDATA%\krita-codex-mcp\config.json` private. Choose narrow
allowed roots rather than an entire drive. Do not expose the port through firewall rules, proxies,
containers or port forwarding.

Layer changes and native painting rely on Krita's own undo behavior. Run the full installer
preflight before productive use, do not experiment on irreplaceable work, and keep normal KRA
backups.
