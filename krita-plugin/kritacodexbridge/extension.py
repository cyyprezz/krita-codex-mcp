from __future__ import annotations

import sys

from krita import Extension
from PyQt5.QtWidgets import QMessageBox

from krita_codex_protocol import BridgeConfig, ConfigError

from .http_bridge import BridgeRuntime
from .logging_utils import bridge_log_path, get_bridge_logger

logger = get_bridge_logger()


class KritaCodexBridgeExtension(Extension):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.runtime: BridgeRuntime | None = None
        self._last_status = "The bridge has not started yet."

    def setup(self) -> None:
        self._start_bridge()

    def createActions(self, window) -> None:  # noqa: N802 - Krita plugin API
        action = window.createAction(
            "krita_codex_bridge_restart", "Restart Codex Bridge", "tools/scripts"
        )
        action.triggered.connect(self._restart_bridge)
        status_action = window.createAction(
            "krita_codex_bridge_status", "Codex Bridge Status", "tools/scripts"
        )
        status_action.triggered.connect(self._show_status)

    def _restart_bridge(self) -> None:
        if self.runtime is not None:
            self.runtime.stop()
            self.runtime = None
            logger.info("Bridge stopped for restart")
        self._start_bridge()

    def _show_status(self) -> None:
        if self.runtime is not None:
            config = self.runtime.config
            status = f"Running on {config.host}:{config.port}."
        else:
            status = self._last_status
        log_path = bridge_log_path()
        log_line = f"Log: {log_path}" if log_path is not None else "Log unavailable."
        QMessageBox.information(None, "Codex Bridge Status", f"{status}\n\n{log_line}")

    def _start_bridge(self) -> None:
        if self.runtime is not None:
            return
        try:
            config = BridgeConfig.load()
            runtime = BridgeRuntime(config)
            runtime.start()
            self.runtime = runtime
            self._last_status = f"Running on {config.host}:{config.port}."
            logger.info("Bridge listening on %s:%s", config.host, config.port)
            print(
                f"krita-codex-mcp bridge listening on {config.host}:{config.port}",
                file=sys.stderr,
            )
        except (ConfigError, OSError) as exc:
            self._last_status = f"Bridge not started: {exc}"
            logger.error("Bridge not started: %s", exc)
            print(f"krita-codex-mcp bridge not started: {exc}", file=sys.stderr)
        except Exception as exc:
            self._last_status = f"Bridge failed unexpectedly: {type(exc).__name__}: {exc}"
            logger.exception("Bridge failed unexpectedly")
            print(f"krita-codex-mcp bridge failed unexpectedly: {exc}", file=sys.stderr)
