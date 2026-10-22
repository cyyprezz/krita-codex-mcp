"""Read-only launch configuration for Claude's local STDIO clients."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path

from .errors import InstallerError
from .paths import InstallPaths


def claude_server_config(
    paths: InstallPaths,
    distribution_version: str,
    *,
    client: str = "code",
    uvx: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build one server entry without reading credentials or changing any files."""
    source = os.environ if environment is None else environment
    executable = uvx if uvx is not None else shutil.which("uvx")
    if not executable:
        raise InstallerError("uvx was not found. Install uv or pass --uvx with its absolute path.")
    command = Path(executable)
    if not command.is_absolute() or not command.is_file():
        raise InstallerError("The uvx command must be an existing file with an absolute path.")
    config = Path(source.get("KRITA_CODEX_CONFIG") or paths.config_file)
    if not config.is_absolute():
        raise InstallerError("KRITA_CODEX_CONFIG must be an absolute path for Claude clients.")
    # A GUI may not inherit the shell's XDG variables. Snapshot only this allowlist,
    # never the entire environment or the contents of the authenticated config.
    environment_block = {"KRITA_CODEX_CONFIG": str(config)}
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        value = source.get(name)
        if value and Path(value).is_absolute():
            environment_block[name] = value
    server: dict[str, object] = {
        "command": str(command),
        "args": ["--from", f"krita-codex-mcp=={distribution_version}", "krita-codex-mcp"],
        "env": environment_block,
    }
    if client == "code":
        server["type"] = "stdio"
    elif client != "desktop":
        raise InstallerError("The Claude client must be code or desktop.")
    return server
