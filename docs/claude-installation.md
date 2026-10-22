# Claude installation

Documentation checked on 2026-10-22. Claude uses the existing local STDIO server and
authenticated loopback bridge. No Anthropic SDK, HTTP MCP transport, new bridge token or
renamed package is required. Codex configuration remains separate and continues to work.

## Clients and platforms

| Client | Windows, native | Linux | macOS |
|---|---|---|---|
| Claude Code CLI | Setup and automated client discovery covered | Experimental bridge; automated client discovery covered | Not covered by this project's Krita installation paths or tests |
| Claude Desktop, chat | Manual local MCP setup documented; GUI test still required | Anthropic's Desktop beta; manual setup experimental, GUI test still required | Not covered by this project |

Claude Code itself supports macOS, Windows and Linux; that does not validate this project's
Krita installer on every client platform. Anthropic lists Ubuntu 22.04+ and Debian 12+ for
Desktop's Linux beta. Other distributions, unofficial Desktop packages, WSL/Windows mixed
setups, Cowork VMs, remote Code sessions, web and mobile clients are outside this guide.
Run the MCP process and Krita under the same local OS and user account.

## 1. Install or reuse the Krita bridge

Follow the [Windows](windows-installation.md) or [Linux](linux-installation.md) guide through
the live preflight. An existing working `0.2.2` bridge can be reused by both Claude and Codex.
Keep the server and installed Krita plugin on that exact version; do not reinstall or reset
configuration merely to add a second client. The allowed roots and private token file are shared.

The following basic configuration works with the published `0.2.2` server. The new
`claude-config` preview helper below is an **unreleased checkout feature**; it is not present
in the already published `0.2.2` installer. This change does not request a release.

## 2. Claude Code

Install Claude Code using [Anthropic's instructions](https://code.claude.com/docs/en/setup).
Register the existing server in user scope so it is available in all your local projects:

PowerShell:

```powershell
$uvxPath = (Get-Command uvx -ErrorAction Stop).Source
claude mcp add --scope user --transport stdio krita -- "$uvxPath" --from "krita-codex-mcp==0.2.2" krita-codex-mcp
claude mcp get krita
```

Bash:

```bash
uvx_path="$(command -v uvx)"
claude mcp add --scope user --transport stdio krita -- "$uvx_path" --from "krita-codex-mcp==0.2.2" krita-codex-mcp
claude mcp get krita
```

This uses a direct executable, including on Windows; `cmd /c` is unnecessary for `uvx.exe`.
Use `--scope local` instead for just your current project. Avoid project scope for
machine-specific absolute paths. Claude Code's user/local entries live in `~/.claude.json`,
or `.claude.json` inside `CLAUDE_CONFIG_DIR` if set; project scope uses `.mcp.json`.

If a `krita` entry already exists, inspect it with `claude mcp get krita` and back up the
indicated configuration before editing it. Do not replace the entire file or unrelated
servers. A local/project entry of the same name can shadow a user entry. Do not register the
same server under multiple names merely to work around a configuration conflict.

### Explicit paths and XDG configuration

For custom `KRITA_CODEX_CONFIG` or XDG directories, use an explicit `env` object. From a checkout:

```bash
uv run --frozen krita-codex-install claude-config --client code
```

This prints JSON only and changes no files. It finds the absolute `uvx` path, pins the current
package version and sets the absolute bridge configuration path. It snapshots explicitly set,
absolute `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_STATE_HOME`; relative XDG values are ignored.
It never reads or prints the token and never copies your entire environment. Run it from the
same shell/user environment used for the Krita installation. Use `--uvx /absolute/path/to/uvx`
if PATH lookup finds the wrong executable. Regenerate the snippet if paths change.

After reviewing the output, Claude Code can register just the server entry (Bash):

```bash
server_json="$(uv run --frozen krita-codex-install claude-config --server-only)"
claude mcp add-json --scope user krita "$server_json"
```

PowerShell:

```powershell
$serverJson = uv run --frozen krita-codex-install claude-config --server-only
$serverJson
claude mcp add-json --scope user krita ($serverJson -join [Environment]::NewLine)
```

Use the basic `mcp add` command above if your older shell/batch launcher changes embedded
JSON quoting.

Start Claude Code and open `/mcp`. A configuration entry alone does not prove a connected
server or correct tool discovery. With Krita running, ask Claude to call `krita_state`, inspect
`runtime_compatibility`, then call `krita_preview` before attempting any edits. Approve the
client's tool permissions as appropriate. Do not drive edits from both clients concurrently.

## 3. Claude Desktop

In the installed app, open **Settings > Developer > Edit Config**. Use the file opened by
the app, especially on Windows packaged installations and Linux beta; do not guess a path
or create a second configuration file. The official MCP guide lists
`%APPDATA%\Claude\claude_desktop_config.json` for conventional Windows installations.

Fully quit Claude Desktop and back up that file. Preserve existing settings and other
`mcpServers` entries. Generate a machine-specific snippet from a checkout:

```bash
uv run --frozen krita-codex-install claude-config --client desktop
```

Merge only `mcpServers.krita` into the existing JSON object. Do not paste the wrapper as a
second JSON document or duplicate an existing `mcpServers` key. The helper is read-only and
does not apply or merge anything automatically. Its Desktop entry omits Code's `type` field.

Without a checkout, add the following entry manually, replacing both absolute Windows paths:

```json
{
  "mcpServers": {
    "krita": {
      "command": "C:\\Users\\YOUR_NAME\\.local\\bin\\uvx.exe",
      "args": ["--from", "krita-codex-mcp==0.2.2", "krita-codex-mcp"],
      "env": {
        "KRITA_CODEX_CONFIG": "C:\\Users\\YOUR_NAME\\AppData\\Local\\krita-codex-mcp\\config.json"
      }
    }
  }
}
```

For Linux, substitute the actual absolute `uvx` executable and bridge config paths. The
bridge config defaults to `$XDG_CONFIG_HOME/krita-codex-mcp/config.json`, falling back to
`~/.config/krita-codex-mcp/config.json`. Enter expanded absolute paths in JSON, not `$HOME`,
`~` or `%LOCALAPPDATA%` placeholders. A GUI launch may not inherit your shell's XDG variables;
include the explicit paths/environment generated above when using custom directories.

On Linux, keep the bridge config/token and any copies private (`0600`, directory `0700`).
Keep client configuration and backups user-private too, since other entries can contain
credentials. On Windows, retain user-only access in the profile directories; do not copy
the token into Claude configuration or loosen its ACLs.

Restart Desktop. Check the local MCP server status and logs in Developer settings and its
tools under the chat's **+ > Connectors** menu. Confirm all 13 tools from the
[tool reference](tool-reference.md), then perform the same `krita_state`/`krita_preview` checks.
Desktop extensions (`.mcpb`) are another distribution option, but are not needed or supplied
by this integration. Organization policies may disable local MCP servers.

## Updates and removal

To change versions, close Krita, run the target version's `krita-codex-install update`, restart
Krita and run that same version's live `check`. Follow the platform guide. Then change only
the package version in each client's `args`, retaining command paths, custom environment,
other servers and settings. Update the Codex launcher too. Back up and review the exact
changes; do not use `--reset-config`. Generating a snippet never updates an installed bridge.

To disconnect Claude Code, inspect the scope first, then use
`claude mcp remove --scope user krita` (or the scope you actually used).
For Desktop, remove only `mcpServers.krita` and
restart the app. Neither action uninstalls the bridge; keep it if Codex still uses it.

## Verification and limits

- **Configuration tests:** validate the generated client shapes, exact version pin, absolute
  paths, XDG forwarding, secret exclusion and byte-for-byte preservation of existing files.
- **Real Claude Code discovery:** Windows/Ubuntu CI install CLI `2.1.308` and run
  `scripts/claude_compatibility_smoke.py` against the built wheel. It uses an isolated
  `CLAUDE_CONFIG_DIR`, exercises `mcp add-json`, then requires a connected server and exactly
  13 named tools in Claude Code's own `system/init` event. Only the package source is replaced
  with the candidate wheel. The model endpoint is a loopback stub returning an intentional
  authentication error; no real model/key, paid inference or Krita GUI is used.
- **Codex regression:** its existing pinned CLI `0.163.0` discovery test remains in CI.
- **Still manual:** Claude Desktop GUI discovery, model-driven tool use, image presentation
  and live Claude-to-Krita operations. A successful JSON check or SDK handshake cannot prove
  these. Existing Windows Krita 5.3.2.1 and contributor Debian 13/Krita 5.2.9 (limited mode)
  live results predate the Claude integration; they do not establish Claude live validation.

## Official references

- [Claude Code MCP transports, scopes and JSON configuration](https://code.claude.com/docs/en/mcp)
- [Claude Code setup and supported systems](https://code.claude.com/docs/en/setup)
- [Claude Desktop availability and Linux beta](https://support.claude.com/en/articles/10065433-install-claude-desktop)
- [Local MCP servers in Claude Desktop](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop)
- [Official MCP local-server configuration walkthrough](https://modelcontextprotocol.io/docs/develop/connect-local-servers)
- [Claude Code 2.1.308](https://github.com/anthropics/claude-code/releases/tag/v2.1.308)
