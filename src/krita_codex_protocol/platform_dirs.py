"""Per-user directories shared by the installer, the MCP server and the Krita plugin.

Windows keeps the ``APPDATA``/``LOCALAPPDATA`` layout. Every other platform
follows the XDG base directory specification. The three components must agree
on the same paths, so the rule lives here and nowhere else.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

APP_DIRNAME = "krita-codex-mcp"


def uses_windows_layout(env: Mapping[str, str] | None = None) -> bool:
    """Return True when per-user paths come from ``APPDATA``/``LOCALAPPDATA``."""
    if os.name == "nt":
        return True
    source = env if env is not None else os.environ
    return bool(source.get("APPDATA")) and bool(source.get("LOCALAPPDATA"))


def _xdg_dir(env: Mapping[str, str], variable: str, home_relative: str) -> Path:
    value = env.get(variable)
    # The XDG specification says a relative value must be ignored.
    if value and Path(value).is_absolute():
        return Path(value)
    home = env.get("HOME")
    return (Path(home) if home else Path.home()) / home_relative


def xdg_pykrita_root(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    return _xdg_dir(source, "XDG_DATA_HOME", ".local/share") / "krita" / "pykrita"


def xdg_config_dir(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    return _xdg_dir(source, "XDG_CONFIG_HOME", ".config") / APP_DIRNAME


def xdg_state_dir(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    return _xdg_dir(source, "XDG_STATE_HOME", ".local/state") / APP_DIRNAME
