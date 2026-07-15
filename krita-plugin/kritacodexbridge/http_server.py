from __future__ import annotations

import hmac
import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

from krita_codex_protocol import (
    BridgeConfig,
    BridgeError,
    BridgeRequest,
    BridgeResponse,
    ProtocolError,
)


@dataclass(frozen=True)
class DispatchResult:
    response: BridgeResponse
    completion: threading.Event | None = None


class Dispatcher(Protocol):
    def call(self, request: BridgeRequest, timeout_seconds: float) -> DispatchResult: ...


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: BridgeConfig, dispatcher: Dispatcher) -> None:
        super().__init__((config.host, config.port), BridgeRequestHandler)
        self.config = config
        self.dispatcher = dispatcher
        self.request_lock = threading.Lock()


class BridgeRequestHandler(BaseHTTPRequestHandler):
    server: BridgeHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "KritaCodexBridge/1"
    sys_version = ""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/v1/execute":
            self._simple_error(404, "NOT_FOUND", "Unknown bridge endpoint")
            return
        if not self._authorized():
            self._simple_error(401, "UNAUTHORIZED", "Invalid bridge token")
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._simple_error(
                415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json"
            )
            return
        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text) if length_text is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self._simple_error(411, "LENGTH_REQUIRED", "Content-Length is required")
            return
        if length > self.server.config.max_request_bytes:
            self._simple_error(413, "REQUEST_TOO_LARGE", "Request exceeds configured limit")
            return
        self.connection.settimeout(self.server.config.request_timeout_seconds)
        try:
            body = self.rfile.read(length)
        except (TimeoutError, OSError):
            self._simple_error(408, "REQUEST_TIMEOUT", "Timed out while reading the request")
            return
        try:
            raw = json.loads(body.decode("utf-8"))
            request = BridgeRequest.from_mapping(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ProtocolError) as exc:
            self._simple_error(400, "INVALID_REQUEST", str(exc))
            return

        acquired = self.server.request_lock.acquire(blocking=False)
        if not acquired:
            response = BridgeResponse(
                request_id=request.request_id,
                error=BridgeError("BRIDGE_BUSY", "Krita bridge is busy", retryable=True),
            )
        else:
            release_here = True
            try:
                dispatched = self.server.dispatcher.call(
                    request, self.server.config.request_timeout_seconds
                )
                response = dispatched.response
                if dispatched.completion is not None:
                    self._release_lock_when_complete(dispatched.completion)
                    release_here = False
            except Exception as exc:
                response = BridgeResponse(
                    request_id=request.request_id,
                    error=BridgeError(
                        "INTERNAL_ERROR",
                        "Unexpected error while dispatching the Krita request",
                        {"type": type(exc).__name__, "reason": str(exc)},
                    ),
                )
            finally:
                if release_here:
                    self.server.request_lock.release()
        self._bridge_response(response)

    def _release_lock_when_complete(self, completion: threading.Event) -> None:
        request_lock = self.server.request_lock

        def release() -> None:
            completion.wait()
            request_lock.release()

        threading.Thread(
            target=release,
            name="krita-codex-bridge-completion",
            daemon=True,
        ).start()

    def _authorized(self) -> bool:
        expected = f"Bearer {self.server.config.token}"
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(expected, supplied)

    def _simple_error(self, status: int, code: str, message: str) -> None:
        self._json_response(status, {"ok": False, "error": {"code": code, "message": message}})

    def _bridge_response(self, response: BridgeResponse) -> None:
        payload = response.to_mapping()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self.server.config.max_response_bytes:
            response = BridgeResponse(
                request_id=response.request_id,
                error=BridgeError(
                    "RESPONSE_TOO_LARGE",
                    "Krita bridge response exceeds the configured limit",
                    {"limit": self.server.config.max_response_bytes},
                ),
            )
            payload = response.to_mapping()
        self._json_response(200, payload)

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.close_connection = True

    def log_message(self, _format: str, *_args: Any) -> None:
        return
