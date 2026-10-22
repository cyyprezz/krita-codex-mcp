from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from krita_codex_protocol.platform_dirs import uses_windows_layout, xdg_state_dir

LOGGER_NAME = "krita-codex-mcp"


def bridge_log_path() -> Path | None:
    if not uses_windows_layout():
        return xdg_state_dir() / "bridge.log"
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    return Path(local_app_data) / "krita-codex-mcp" / "bridge.log"


def get_bridge_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if getattr(logger, "_krita_codex_configured", False):
        return logger

    try:
        path = bridge_log_path()
        if path is None:
            raise OSError("LOCALAPPDATA is not set")
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            path,
            maxBytes=262_144,
            backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    except Exception:
        logger.addHandler(logging.NullHandler())

    logger._krita_codex_configured = True  # type: ignore[attr-defined]
    return logger
