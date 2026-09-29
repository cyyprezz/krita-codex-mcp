# Linux installation

The bridge runs on Linux from a local checkout. Windows paths (`%APPDATA%`, `%LOCALAPPDATA%`) are
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

`KRITA_CODEX_CONFIG` (an absolute path) still overrides the configuration path. No step needs
`sudo`: `pykrita_root` is the Krita plugin folder, unrelated to the root account.

Prerequisites: Krita with Python scripting, `uv` on `PATH`, and one narrow directory for KRA and
export access. Run every command below from the root of your checkout (the directory that contains
`pyproject.toml`). The examples use `~/krita-codex-work` as the allowed root: change it if you
prefer another directory.

## 1. Install with Krita closed

```bash
mkdir -p "$HOME/krita-codex-work"
uv run krita-codex-install install --allowed-root "$HOME/krita-codex-work"
```

## 2. Enable the plugin

In Krita, open **Settings > Configure Krita > Python Plugin Manager**, enable
**Krita Codex Bridge**, then restart Krita.

## 3. Run the preflight

```bash
uv run krita-codex-install check --offline   # Krita closed
uv run krita-codex-install check             # Krita restarted
```

Expected result: `compatible` or `limited`, never `incompatible`.

## 4. Connect an MCP client

Use the checkout, not PyPI: the published `0.2.0` package predates the XDG rule. JSON does not
expand variables, so this command prints the block for opencode already filled in with your
checkout path; paste it into `~/.config/opencode/opencode.jsonc`:

```bash
cat <<EOF
"mcp": {
  "krita": {
    "type": "local",
    "command": ["uv", "run", "--directory", "$PWD", "krita-codex-mcp"],
    "enabled": true,
    "timeout": 210000
  }
}
EOF
```

The 210 s timeout matches the bridge limits (120 s open/save, 180 s export); opencode's default of
5 s would cut them.

## Tests

```bash
uv run --frozen python -m unittest discover -s tests
```
