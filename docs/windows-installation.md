# Windows installation

The supported production target is Krita 5.3.2.1 on Windows. Newer Krita builds remain
feature-detected, but must complete the same preflight and live release checks before use.

Prerequisites:

- Krita 5.3.2.1;
- `uv` on `PATH`;
- a local Codex desktop app, CLI or IDE extension;
- one or more narrow local directories for KRA and export access.

## 1. Install

Clone the repository and run the local installer:

```powershell
git clone https://github.com/cyyprezz/krita-codex-mcp.git
Set-Location .\krita-codex-mcp
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 `
  -AllowedRoot "C:\Users\you\Documents\Krita Work"
```

The installer creates `.venv`, installs the MCP package, stages a clean plugin and shared protocol
package, swaps them into `%APPDATA%\krita\pykrita`, and writes the shared config beneath
`%LOCALAPPDATA%\krita-codex-mcp`. Existing plugin files are replaced as a unit, so obsolete Python
files cannot survive an update. If replacement fails, the installer restores its temporary
backup where possible.

On repeat installation, a valid existing config and token are preserved. `-AllowedRoot` or
`-Port` changes only those explicit settings. An invalid config is not silently replaced; repair
it or use `-ResetConfig` deliberately to create a new token and config.

The installer never edits Codex configuration, commits files or contacts a service other than the
Python package source used by `uv`.

## 2. Enable the Krita plugin

In Krita, open **Settings > Configure Krita > Python Plugin Manager** and enable
**Krita Codex Bridge**.

## 3. Restart Krita

Close and reopen Krita. A restart is required after install or update so the plugin and copied
protocol package are loaded from the same version.

## 4. Run the installation preflight

With Krita running, execute:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-installation.ps1
```

The preflight is local-only. It validates the Windows config, token, absolute writable allowed
roots, MCP executable, plugin and protocol files, loopback host, port and authentication. It then
creates a disposable 64×64 document, runs `krita_state`, validates a direct PNG preview, exports a
temporary PNG beneath the first allowed root, closes the document and removes the temporary file.
If another document was active before the check, the script reactivates it during cleanup.

A successful result ends with:

```text
Configuration: OK
Plugin installation: OK
Bridge: OK
Authentication: OK
MCP: OK
Preview: OK
Export: OK

Ready for productive use.
```

On failure, the script prints the failed category, a concrete cause and a suggested fix, then
returns a non-zero exit code. Do not start productive Codex work until all categories are `OK`.

## 5. Configure Codex and start work

Use the block printed by the installer, or adapt `.codex/config.toml.example`:

```toml
[mcp_servers.krita]
command = "C:\\absolute\\path\\krita-codex-mcp\\.venv\\Scripts\\krita-codex-mcp.exe"
cwd = "C:\\absolute\\path\\krita-codex-mcp"
startup_timeout_sec = 10
tool_timeout_sec = 210
```

Restart Codex after changing its MCP configuration. The 210-second outer timeout allows the local
bridge to use 30 seconds for normal reads and edits, 120 seconds for document open/save, and 180
seconds for KRA/PNG, layer, animation, tileset and region exports, plus response overhead.

If a running operation times out, it may still complete inside Krita. The bridge reports
`operation_may_complete=true` and returns `BRIDGE_BUSY` to later calls until the original Qt
operation really finishes. Do not immediately repeat a save or export against the same output.

## Update and uninstall

Update from the current checkout without replacing the existing config or token:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update.ps1
```

Remove the installed plugin and copied protocol package while keeping config, token and `.venv`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1
```

Optional destructive switches are explicit:

```powershell
.\scripts\uninstall.ps1 -RemovePythonEnvironment
.\scripts\uninstall.ps1 -RemoveConfig
```

Uninstall never traverses allowed roots and does not touch user images, KRA files or exports.

## Production safety and Krita 5.3.2.1 limits

- Configure the narrowest practical allowed roots. The bridge refuses input and output paths
  outside them.
- Keep normal KRA backups. The bridge adds safety guards but is not a version-control or backup
  system.
- Direct free layer transforms are not undoable through Krita 5.3.2.1's Python API. The bridge
  creates hidden backup layers and blocks a second direct transform until save, close and reopen.
- Transform masks are limited to three per open document; same-session mutation/removal and mixing
  direct and mask transform backends are blocked because that Krita build is teardown-unstable.
- `Node.paintLine` accepts integer `QPoint` values in this build, so public numeric coordinates are
  rounded at the native boundary.
- PNG export temporarily uses Krita batch mode to avoid format dialogs. Keep the UI responsive and
  do not close Krita during a long export.

## Release verification

Offline CI runs unit tests, Ruff and package builds without a Krita GUI. Before a release, run all
live scripts in [the Krita 5.3.2.1 E2E checklist](krita-e2e-checklist.md), including the direct
bridge, expansion, editing, regions, creation, transform-mask and real MCP STDIO checks.
