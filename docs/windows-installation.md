# Windows installation

Krita compatibility is checked against the running Windows installation instead of being pinned
to one application version. Krita 5.3.2.1 is the currently tested build. The Python wheel contains
the STDIO MCP server, the dependency-free shared protocol package, the complete Krita plugin and
the wheel-native installation manager. A Git checkout is not required for a package installation.

A first-time setup has two separate parts. Steps 1 through 3 install, enable and verify the local
Krita bridge. Step 4 then connects Codex through either the marketplace plugin or a direct MCP
configuration. The `codex plugin` commands only configure Codex; they do **not** install the bridge,
write to Krita's plugin directory, enable the Python plugin or restart Krita.

Prerequisites:

- a Windows Krita installation that passes the live compatibility check;
- `uv` and `uvx` on `PATH`;
- a local Codex desktop app, CLI or IDE extension;
- one or more narrow local directories for KRA and export access.

If `uvx` is not available, install `uv` with Windows Package Manager and open a new terminal:

```powershell
winget install --id=astral-sh.uv -e
```

The [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/)
lists alternative installation methods.

The examples below use release `0.2.0` from
[PyPI](https://pypi.org/project/krita-codex-mcp/). Always keep the installer, Krita plugin and
Codex server pinned to the same exact version.

## 1. Install from PyPI

A new package installation requires at least one explicit allowed root. There is no implicit
Documents default. Close Krita, make sure the directory exists, and run:

```powershell
uvx --from "krita-codex-mcp==0.2.0" `
  krita-codex-install install `
  --allowed-root "C:\Krita Work"
```

The installer places the plugin and shared protocol beneath `%APPDATA%\krita\pykrita` and stores
configuration beneath `%LOCALAPPDATA%\krita-codex-mcp`. Installation, update and uninstall are
transactional: a failed or interrupted change is restored automatically when safe. If recovery
cannot be proven safe, the command stops and reports the retained recovery files.

An existing valid configuration, token, port, allowed roots and unknown configuration fields are
preserved during migration and update. A changed `--allowed-root` is rejected during migration;
configure policy changes separately instead of mixing them with a software update.

Install and update never close Krita. If Krita or another process is using the configured local
bridge port, the command stops before changing installed files. Close Krita manually or free the
port and rerun it.

Because Krita must be closed while files are replaced, installation can only verify the wheel,
plugin, protocol and configuration offline. Runtime compatibility remains pending until Krita is
restarted and the live check in step 3 succeeds.

## 2. Enable and restart the Krita plugin

In Krita, open **Settings > Configure Krita > Python Plugin Manager** and enable
**Krita Codex Bridge**. Close and reopen Krita after every install or update. The restart is what
loads the newly installed plugin and protocol together.

## 3. Run the preflight

The offline check needs no running Krita process:

```powershell
uvx --from "krita-codex-mcp==0.2.0" `
  krita-codex-install check --offline
```

After Krita has restarted, run the full local preflight:

```powershell
uvx --from "krita-codex-mcp==0.2.0" `
  krita-codex-install check
```

The check compares the running wheel/installer version, installed plugin and protocol hashes, and
the distribution and protocol versions reported by the code actually loaded in Krita. It then
creates a disposable document and checks the current compatibility profile, a new paint layer,
native drawing, Undo/Redo, verified layer reparenting, preview bytes and temporary KRA/PNG output.
The temporary document and files are removed and the previously active document is restored.

Results are deliberately tiered:

- `compatible`: all checked capabilities are available;
- `limited`: productive use remains possible, but warnings name each affected MCP tool;
- `incompatible`: a critical document, projection, persistence or stable-layer capability is
  missing, so the command stops.

Unknown Krita versions are not rejected by their version string. They are reported as
`capability_checked` and accepted when the critical profile passes. Codex receives the same static
capability report in `krita_state` and through `krita_document(action="compatibility")`; it should
avoid affected tools rather than assuming an unavailable feature. Do not start normal work until
the command ends with either `Ready for productive use.` or
`Ready for productive use with the reported limitations.`

## 4. Connect Codex

Complete steps 1 through 3 first, then choose exactly one of the following options. Do not combine
the marketplace plugin with a separate `[mcp_servers.krita]` block because both start the same local
MCP server.

### Option A: Install the Codex plugin from GitHub

The public marketplace plugin bundles the `krita-control` skill and the version-pinned MCP server
configuration:

```powershell
codex plugin marketplace add cyyprezz/krita-codex-mcp
codex plugin add krita-codex-mcp@krita-codex
```

These commands register the GitHub marketplace and install the Codex bundle only. They do not
perform any of the Krita installation, activation or restart steps above. Start a new Codex task
after installation, then call `krita_state` and `krita_preview` before normal work.

### Option B: Configure the MCP server directly

Use this alternative when you do not want the marketplace plugin. The recommended direct server
command is checkout-free and pinned to exactly the installed distribution version:

```toml
[mcp_servers.krita]
command = "uvx"
args = ["--from", "krita-codex-mcp==0.2.0", "krita-codex-mcp"]
startup_timeout_sec = 60
tool_timeout_sec = 210
```

For all tasks and Git worktrees, place it in the user-level
`C:\Users\<you>\.codex\config.toml`. The installer does not edit that file during install or update.
Preview its proposed managed block with:

```powershell
uvx --from "krita-codex-mcp==0.2.0" krita-codex-install codex-config
```

Apply only after reviewing the complete preview:

```powershell
uvx --from "krita-codex-mcp==0.2.0" krita-codex-install codex-config --apply
```

The command asks for confirmation. `--yes` is an explicit non-interactive confirmation. Existing
unmarked or ambiguous `[mcp_servers.krita]` entries are never overwritten. Only the installer-owned
block can be updated or removed. If the file changes after the preview, the command stops and
requires a new preview instead of overwriting it.

No bearer token is written to Codex configuration. Never commit personal configuration or machine
paths. Restart Codex after changing its MCP configuration, then call `krita_state` and
`krita_preview`.

## Update, migration and uninstall

Close Krita manually, then update with the new exact version:

```powershell
$Version = "<new-version>"
uvx --refresh --from "krita-codex-mcp==$Version" krita-codex-install update
```

After updating the payload, refresh the Codex connection that you selected in step 4.

Marketplace plugin users refresh the marketplace and reinstall its current entry:

```powershell
codex plugin marketplace upgrade krita-codex
codex plugin add krita-codex-mcp@krita-codex
```

Direct-configuration users preview and explicitly update the managed Codex block to the same
version:

```powershell
uvx --refresh --from "krita-codex-mcp==$Version" krita-codex-install codex-config
uvx --refresh --from "krita-codex-mcp==$Version" krita-codex-install codex-config --apply
```

Restart Krita and run the full preflight. Direct-configuration users must also restart Codex;
marketplace plugin users must start a new Codex task. Replace `<new-version>` with the version shown
on PyPI. A checkout-based legacy installation is migrated by `install`; its existing configuration
and allowed roots are kept. The checkout and its `.venv` are not removed.

Remove managed plugin files while keeping configuration and token:

```powershell
uvx --from "krita-codex-mcp==0.2.0" krita-codex-install uninstall
```

Configuration removal is deliberately explicit:

```powershell
uvx --from "krita-codex-mcp==0.2.0" krita-codex-install uninstall --remove-config
uvx --from "krita-codex-mcp==0.2.0" krita-codex-install codex-config --remove --apply
```

Uninstall never traverses allowed roots and never touches user images, KRA files or exports.

## Production safety and tested Krita 5.3.2.1 limits

- Configure the narrowest practical allowed roots. The bridge refuses input and output paths
  outside them.
- Keep normal KRA backups. The bridge adds safety guards but is not a version-control or backup
  system.
- Direct free layer transforms are not undoable through Krita 5.3.2.1's Python API. The bridge
  creates hidden backup layers and blocks a second direct transform until save, close and reopen.
- Transform masks are limited to three per open document; same-session mutation/removal and mixing
  direct and mask transform backends are blocked because that Krita build is teardown-unstable.
- Native painting never falls back silently to direct pixel rendering. If a shape or stroke method
  is absent, the compatibility report marks the affected tools and the specific operation fails
  with a structured error; unrelated tools remain available.
- PNG export temporarily uses Krita batch mode to avoid format dialogs. Do not close Krita during a
  long export.
