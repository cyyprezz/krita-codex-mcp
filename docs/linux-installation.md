# Linux installation

The `0.2.1` package installs the bridge on Linux without a Git checkout.
Windows paths (`%APPDATA%`, `%LOCALAPPDATA%`) are
replaced by the XDG base directories; a Windows environment keeps its layout unchanged.

Final layout, with no `XDG_*` variable set:

```text
~/.local/share/krita/pykrita/
├── kritacodexbridge/          Krita plugin (loaded by Krita)
├── kritacodexbridge.desktop   plugin descriptor
└── krita_codex_protocol/      shared protocol, copied next to the plugin
~/.config/krita-codex-mcp/
└── config.json                token, port and allowed roots, read by server and plugin
~/.local/state/krita-codex-mcp/
└── bridge.log                 plugin log
```

| Purpose | Variable | Default |
|---|---|---|
| Plugin and protocol | `$XDG_DATA_HOME/krita/pykrita` | `~/.local/share/krita/pykrita` |
| Configuration, transaction journal, lock | `$XDG_CONFIG_HOME/krita-codex-mcp` | `~/.config/krita-codex-mcp` |
| Plugin log | `$XDG_STATE_HOME/krita-codex-mcp` | `~/.local/state/krita-codex-mcp` |

`KRITA_CODEX_CONFIG` (an absolute path) overrides the configuration path for the MCP server and
Krita plugin only. Installer commands always manage the default XDG configuration shown above;
they do not follow this runtime override. Leave it unset for the installation described here.

The installer keeps its application configuration directory private (`0700`) and writes token
files with mode `0600`, including staging and recovery snapshots. It also tightens existing
configuration permissions before update or uninstall. If an older installation's token may have
been readable by another user, close Krita and reinstall with `--reset-config` and `--allowed-root`
to generate a new token, then restart Krita.

No step needs `sudo`: `pykrita_root` is the Krita plugin folder, unrelated to the root account.

Prerequisites: Krita with Python scripting, `uv` on `PATH`, and one narrow directory for KRA and
export access. The examples use `~/krita-codex-work` as the allowed root: change it if you
prefer another directory.

## 1. Install with Krita closed

```bash
mkdir -p "$HOME/krita-codex-work"
uvx --from "krita-codex-mcp==0.2.1" krita-codex-install install --allowed-root "$HOME/krita-codex-work"
```

## 2. Enable the plugin

In Krita, open **Settings > Configure Krita > Python Plugin Manager** (French interface:
**Paramètres > Configuration de Krita > Gestionnaire de modules Python**), enable
**Krita Codex Bridge**, then restart Krita.

## 3. Run the preflight

```bash
uvx --from "krita-codex-mcp==0.2.1" krita-codex-install check --offline   # Krita closed
uvx --from "krita-codex-mcp==0.2.1" krita-codex-install check             # Krita restarted
```

Expected result: `compatible` or `limited`, never `incompatible`.

## 4. Connect Codex

After the live preflight passes, choose exactly one connection method. The marketplace plugin
provides the version-pinned server and guided Krita skill:

```bash
codex plugin marketplace add cyyprezz/krita-codex-mcp
codex plugin add krita-codex-mcp@krita-codex
```

Alternatively, preview the installer's direct user-wide Codex block, then apply it after checking
the preview and confirming the prompt:

```bash
uvx --from "krita-codex-mcp==0.2.1" krita-codex-install codex-config
uvx --from "krita-codex-mcp==0.2.1" krita-codex-install codex-config --apply
```

The direct block in `~/.codex/config.toml` uses the same version as the installer:

```toml
[mcp_servers.krita]
command = "uvx"
args = ["--from", "krita-codex-mcp==0.2.1", "krita-codex-mcp"]
env_vars = ["XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"]
startup_timeout_sec = 60
tool_timeout_sec = 210
```

Restart Codex, inspect `codex mcp list` or `/mcp`, then call `krita_state` and `krita_preview` in a
new task. Codex and Krita must run on the same computer and resolve the same XDG environment.
Codex filters the environment of MCP subprocesses; `env_vars` explicitly forwards custom XDG
paths. Launch Codex from an environment containing those same values, or set their absolute
values in a `[mcp_servers.krita.env]` table if your desktop launcher does not inherit them.

For updates, close Krita, run the new version's `krita-codex-install update`, restart Krita and
repeat the live check. Then update your chosen Codex launcher to that same version. Keep existing
allowed roots; do not mix policy changes with a software update.

For development, install and check from a checkout with `uv run --frozen krita-codex-install`.
To launch that same source in Codex, replace only the direct block's command and arguments:

```toml
command = "uv"
args = ["run", "--frozen", "--directory", "/absolute/path/to/krita-codex-mcp", "krita-codex-mcp"]
```

Keep the `env_vars` and timeout settings from the full block. Use an absolute checkout path;
TOML does not expand `$HOME` or shell variables. The installer's `codex-config` command and the
marketplace always launch a versioned package, so use this alternative for unreleased source changes.

See [Codex/MCP compatibility](codex-compatibility.md) for the checked client and protocol boundary.

## 5. Other MCP clients

For opencode, paste this block into `~/.config/opencode/opencode.jsonc`:

```json
"mcp": {
  "krita": {
    "type": "local",
    "command": ["uvx", "--from", "krita-codex-mcp==0.2.1", "krita-codex-mcp"],
    "enabled": true,
    "timeout": 210000
  }
}
```

The 210 s timeout matches the bridge limits (120 s open/save, 180 s export); opencode's default of
5 s would cut them.

## Tests from a source checkout

```bash
uv run --frozen python -m unittest discover -s tests
```
