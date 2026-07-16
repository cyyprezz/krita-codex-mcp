from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import InstallerError


@dataclass(frozen=True)
class InstallPaths:
    pykrita_root: Path
    plugin_dir: Path
    protocol_dir: Path
    desktop_file: Path
    config_dir: Path
    config_file: Path
    codex_config_file: Path
    transaction_journal_file: Path
    installer_lock_file: Path

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> InstallPaths:
        env = environment if environment is not None else os.environ
        app_data = env.get("APPDATA")
        local_app_data = env.get("LOCALAPPDATA")
        user_profile = env.get("USERPROFILE") or env.get("HOME")
        if not app_data or not local_app_data or not user_profile:
            raise InstallerError(
                "APPDATA, LOCALAPPDATA and USERPROFILE are required for a Windows installation."
            )
        pykrita_root = Path(app_data) / "krita" / "pykrita"
        config_dir = Path(local_app_data) / "krita-codex-mcp"
        return cls(
            pykrita_root=pykrita_root,
            plugin_dir=pykrita_root / "kritacodexbridge",
            protocol_dir=pykrita_root / "krita_codex_protocol",
            desktop_file=pykrita_root / "kritacodexbridge.desktop",
            config_dir=config_dir,
            config_file=config_dir / "config.json",
            codex_config_file=Path(user_profile) / ".codex" / "config.toml",
            transaction_journal_file=config_dir / ".installer-transaction.json",
            installer_lock_file=config_dir / ".installer.lock",
        )
