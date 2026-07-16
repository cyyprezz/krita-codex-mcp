# Windows installation

The supported production target is Krita 5.3.2.1 on Windows. The Python wheel contains the
STDIO MCP server, the dependency-free shared protocol package, the complete Krita plugin and the
wheel-native installation manager. A Git checkout is not required for a package installation.

Prerequisites:

- Krita 5.3.2.1;
- `uv` and `uvx` on `PATH`;
- a local Codex desktop app, CLI or IDE extension;
- one or more narrow local directories for KRA and export access.

The examples below use version `0.1.0`. Replace it with the exact wheel version being installed.
Until the package is published, use a locally built wheel path in `--from`; do not substitute an
unpublished PyPI name.

## 1. Install from a wheel

A new package installation requires at least one explicit allowed root. There is no implicit
Documents default:

```powershell
uvx --from .\dist\krita_codex_mcp-0.1.0-py3-none-any.whl `
  krita-codex-install install `
  --allowed-root "C:\Krita Work"
```

Once the same wheel is available from PyPI, the checkout-free form is:

```powershell
uvx --from "krita-codex-mcp==0.1.0" `
  krita-codex-install install `
  --allowed-root "C:\Krita Work"
```

The installer stages the bundled plugin, desktop descriptor, shared protocol and generated
hash manifest beneath `%APPDATA%\krita\pykrita`. It validates the complete stage, swaps each
component transactionally and restores the previous installation if any step fails. Configuration
is stored beneath `%LOCALAPPDATA%\krita-codex-mcp` and is not part of the wheel or manifest.

Install, update and uninstall are serialized with a non-blocking process lock. Before the first
managed-file mutation, the installer atomically writes and fsyncs a persistent journal. It records
the state before and after every target-to-backup, stage-to-target or remove rename. If the process
or machine stops mid-transaction, the next mutating installer command restores the previous
plugin, protocol, desktop descriptor and configuration before beginning new work. A journal is
removed only after final validation and transaction-directory cleanup. If recovery cannot be
proven safe, the command stops and prints the retained journal and backup paths.

An existing valid configuration, token, port, allowed roots and unknown configuration fields are
preserved during migration and update. A changed `--allowed-root` is rejected during migration;
configure policy changes separately instead of mixing them with a software update.

Install and update never close Krita. If the authenticated bridge is active, replacement is
rejected before any installed file is changed. On Windows, the installer does not infer port
availability from `connect()` or `connection refused`. Instead, it binds an unlistening socket with
`SO_EXCLUSIVEADDRUSE` exactly to `127.0.0.1` on every relevant old and new configured port. Those
sockets remain open before staging and throughout journal creation, swaps/removes, validation and
cleanup. This also prevents Krita or another process from claiming the port mid-install. Any bind
failure blocks the command; close Krita manually or free the configured port and rerun it.

## 2. Enable and restart the Krita plugin

In Krita, open **Settings > Configure Krita > Python Plugin Manager** and enable
**Krita Codex Bridge**. Close and reopen Krita after every install or update. The restart is what
loads the newly installed plugin and protocol together.

## 3. Run the preflight

The offline check needs no running Krita process:

```powershell
uvx --from "krita-codex-mcp==0.1.0" `
  krita-codex-install check --offline
```

After Krita has restarted, run the full local preflight:

```powershell
uvx --from "krita-codex-mcp==0.1.0" `
  krita-codex-install check
```

The check compares the running wheel/installer version, the installed plugin and protocol hashes,
and the distribution and protocol versions reported by the code actually loaded in Krita. It then
validates authentication, creates a disposable document, checks `krita_state`, validates direct PNG
preview bytes, exports beneath the first allowed root, closes the document, restores the previously
active document and removes the temporary PNG.

Do not start productive work until the command ends with `Ready for productive use.`

## 4. Configure Codex

The recommended server command is checkout-free and pinned to exactly the installed distribution
version:

```toml
[mcp_servers.krita]
command = "uvx"
args = ["--from", "krita-codex-mcp==0.1.0", "krita-codex-mcp"]
startup_timeout_sec = 60
tool_timeout_sec = 210
```

For all tasks and Git worktrees, place it in the user-level
`C:\Users\<you>\.codex\config.toml`. The installer does not edit that file during install or update.
Preview its proposed managed block with:

```powershell
uvx --from "krita-codex-mcp==0.1.0" krita-codex-install codex-config
```

Apply only after reviewing the complete preview:

```powershell
uvx --from "krita-codex-mcp==0.1.0" krita-codex-install codex-config --apply
```

The command asks for confirmation. `--yes` is an explicit non-interactive confirmation. Existing
unmarked or ambiguous `[mcp_servers.krita]` entries are never overwritten. Only the block enclosed
by installer markers can be updated or removed. Before an existing user config is replaced, an
atomic sibling backup is written and both staged and final TOML are validated. Immediately before
backup and replacement, the installer verifies that file existence and content still match the
displayed preview. An external change aborts without replacing the file and requires a new preview.

No bearer token is written to Codex configuration. Never commit personal configuration or machine
paths. Restart Codex after changing its MCP configuration, then call `krita_state` and
`krita_preview`.

## Update, migration and uninstall

Close Krita manually, then update with the new exact version:

```powershell
uvx --from "krita-codex-mcp==0.1.1" krita-codex-install update
```

After updating the payload, preview and explicitly update the managed Codex block to the same
version, restart Krita and run the full preflight. A checkout-based legacy installation is migrated
by `install`; its existing configuration and allowed roots are kept. The checkout and its `.venv`
are not removed.

Remove managed plugin files while keeping configuration and token:

```powershell
uvx --from "krita-codex-mcp==0.1.0" krita-codex-install uninstall
```

Configuration removal is deliberately explicit:

```powershell
uvx --from "krita-codex-mcp==0.1.0" krita-codex-install uninstall --remove-config
uvx --from "krita-codex-mcp==0.1.0" krita-codex-install codex-config --remove --apply
```

Uninstall never traverses allowed roots and never touches user images, KRA files or exports.

## Repository compatibility scripts

Trusted checkouts may continue to use `scripts/install.ps1`, `update.ps1`, `uninstall.ps1` and
`check-installation.ps1`. These scripts only prepare or locate the checkout `.venv` and delegate to
the same `krita-codex-install` Python implementation. They contain no separate copy of transaction,
manifest, configuration, preflight or uninstall logic.

## Production safety and Krita 5.3.2.1 limits

- Configure the narrowest practical allowed roots. The bridge refuses input and output paths
  outside them.
- Keep normal KRA backups. The bridge adds safety guards but is not a version-control or backup
  system.
- Direct free layer transforms are not undoable through Krita 5.3.2.1's Python API. The bridge
  creates hidden backup layers and blocks a second direct transform until save, close and reopen.
- Transform masks are limited to three per open document; same-session mutation/removal and mixing
  direct and mask transform backends are blocked because that Krita build is teardown-unstable.
- PNG export temporarily uses Krita batch mode to avoid format dialogs. Do not close Krita during a
  long export.

## Release verification

Offline CI runs unit tests, Ruff, direct wheel/sdist builds, wheel-from-sdist verification, artifact
content checks and local-wheel `uvx` smokes without a Krita GUI. It also compares every Python,
protocol and bundled plugin payload byte between the direct wheel and the wheel rebuilt from sdist,
while intentionally ignoring wheel metadata. Before a release, run all live scripts in the
[Krita 5.3.2.1 E2E checklist](krita-e2e-checklist.md).
