from __future__ import annotations

import json
import secrets
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from krita_codex_mcp import __version__
from krita_codex_mcp.installer.bridge import probe_authenticated_bridge
from krita_codex_mcp.installer.errors import InstallerError
from krita_codex_protocol import PROTOCOL_VERSION, BridgeConfig, BridgeResponse


class InstallerBridgeProbeTests(unittest.TestCase):
    @staticmethod
    def _config(directory: str, credential: str, port: int) -> BridgeConfig:
        return BridgeConfig.from_mapping(
            {
                "token": credential,
                "allowed_roots": [directory],
                "port": port,
            }
        )

    def test_authenticated_loopback_bridge_is_detected_with_loaded_versions(self) -> None:
        credential = secrets.token_urlsafe(32)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                length = int(self.headers["Content-Length"])
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                if self.headers["Authorization"] != f"Bearer {credential}":
                    raise AssertionError("unexpected authorization header")
                response = BridgeResponse(
                    request_id=request["request_id"],
                    result={
                        "documents": [],
                        "bridge_distribution_version": __version__,
                        "bridge_protocol_version": PROTOCOL_VERSION,
                    },
                )
                body = json.dumps(response.to_mapping()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = self._config(directory, credential, server.server_port)
                active = probe_authenticated_bridge(config)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        self.assertIsNotNone(active)
        self.assertEqual(active.distribution_version, __version__)
        self.assertEqual(active.protocol_version, PROTOCOL_VERSION)

    def test_diagnostic_probe_reports_connection_refusal_as_inactive(self) -> None:
        credential = secrets.token_urlsafe(32)
        with tempfile.TemporaryDirectory() as directory:
            config = self._config(directory, credential, 17641)
            with patch(
                "krita_codex_mcp.installer.bridge.socket.create_connection",
                side_effect=ConnectionRefusedError("connection refused"),
            ):
                self.assertIsNone(probe_authenticated_bridge(config, timeout_seconds=0.5))

    def test_slow_handler_blocks_instead_of_appearing_stopped(self) -> None:
        credential = secrets.token_urlsafe(32)

        class SlowHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                time.sleep(0.5)
                try:
                    self.send_response(204)
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, _format: str, *_args: object) -> None:
                return

        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = self._config(directory, credential, server.server_port)
                with self.assertRaisesRegex(InstallerError, "accepted TCP.*timed out"):
                    probe_authenticated_bridge(config, timeout_seconds=0.1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_invalid_local_service_blocks_instead_of_appearing_stopped(self) -> None:
        credential = secrets.token_urlsafe(32)

        class InvalidHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                body = b"not-a-bridge"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(("127.0.0.1", 0), InvalidHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config = self._config(directory, credential, server.server_port)
                with self.assertRaisesRegex(InstallerError, "valid authenticated bridge"):
                    probe_authenticated_bridge(config, timeout_seconds=0.5)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
