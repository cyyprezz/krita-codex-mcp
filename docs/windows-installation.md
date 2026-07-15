# Windows installation

Prerequisites:

- Krita 5.3.2.1 initially; newer builds remain feature-detected.
- `uv` available on `PATH`.
- A local Codex client (desktop app, CLI, or IDE extension).

From this repository, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 `
  -AllowedRoot "C:\Users\you\Documents\Krita Work"
```

The installer performs local-only setup:

1. creates `.venv` and installs the MCP package with `uv`;
2. copies the Krita plugin and shared protocol into `%APPDATA%\krita\pykrita`;
3. writes a random token and allowed roots to
   `%LOCALAPPDATA%\krita-codex-mcp\config.json`;
4. prints a Codex `config.toml` snippet, but does not edit Codex configuration itself.

Treat the config file as a secret because it contains the localhost bearer token. Do not commit
it or paste it into chat.

In Krita, open **Settings > Configure Krita > Python Plugin Manager**, enable **Krita Codex
Bridge**, and restart Krita. The plugin starts only after it can read a valid shared config.

Add the printed block to either `~/.codex/config.toml` or a trusted project's `.codex/config.toml`:

```toml
[mcp_servers.krita]
command = "C:\\absolute\\path\\krita-codex-mcp\\.venv\\Scripts\\krita-codex-mcp.exe"
cwd = "C:\\absolute\\path\\krita-codex-mcp"
startup_timeout_sec = 10
tool_timeout_sec = 120
```

Codex's desktop app, CLI and IDE extension share the same host MCP configuration. Restart the
client after changing it, then inspect `/mcp` or the MCP server settings.

This repository includes `.codex/config.toml.example` for a project-scoped setup. Copy it to
`.codex/config.toml` and replace the absolute paths. The active file is intentionally ignored by
Git because those paths are specific to one Windows machine.

Start with `krita_state`. If it reports `BRIDGE_UNAVAILABLE`, verify that Krita is open, the
plugin is enabled, both processes use the same Windows account/config file, and port 17641 is not
already occupied.

If no document is open, use `krita_document` with `action="create"` or `action="open"`. Opening is
restricted to the same configured roots used for exports; Krita itself still needs to be running.

## Live smoke tests

Create or open a disposable 512×512 RGBA/U8 document in Krita. The first command creates layers,
paints two native brush segments and writes a KRA plus PNG beneath the configured allowed root:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe .\scripts\live_e2e.py `
  --output-dir "C:\absolute\allowed\root"
```

Then verify the installed STDIO server and direct MCP image payload:

```powershell
.\.venv\Scripts\python.exe .\scripts\live_expansion_e2e.py `
  --output-dir "C:\absolute\allowed\root"
.\.venv\Scripts\python.exe .\scripts\live_mcp_e2e.py
```

The scripts never edit Codex configuration, commit files or contact GitHub. Live outputs should
stay in the ignored `e2e-output` directory (or another disposable allowed root).
