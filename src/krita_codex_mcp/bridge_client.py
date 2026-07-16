from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from krita_codex_protocol import (
    BridgeConfig,
    BridgeRequest,
    BridgeResponse,
    ProtocolError,
)


@dataclass(frozen=True)
class BridgeCallError(RuntimeError):
    code: str
    message: str
    details: Mapping[str, Any]
    retryable: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BridgeClient:
    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    async def execute(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        request = BridgeRequest(operation=operation, arguments=arguments)
        return await asyncio.to_thread(self._execute_sync, request)

    def _execute_sync(self, request: BridgeRequest) -> dict[str, Any]:
        encoded = json.dumps(request.to_mapping(), separators=(",", ":")).encode("utf-8")
        if len(encoded) > self.config.max_request_bytes:
            raise BridgeCallError(
                "REQUEST_TOO_LARGE", "bridge request exceeds configured limit", {}
            )

        http_request = urllib.request.Request(
            self.config.execute_url,
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(
                http_request, timeout=self.config.request_timeout_seconds + 2.0
            ) as response:
                body = response.read(self.config.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            body = exc.read(self.config.max_response_bytes + 1)
            if len(body) > self.config.max_response_bytes:
                raise BridgeCallError(
                    "RESPONSE_TOO_LARGE", "bridge error response exceeds configured limit", {}
                ) from exc
            try:
                raw = json.loads(body.decode("utf-8")) if body else {}
                error = raw.get("error", {}) if isinstance(raw, dict) else {}
                code = error.get("code")
                message = error.get("message")
            except (UnicodeDecodeError, json.JSONDecodeError):
                code = None
                message = None
            if isinstance(code, str) and isinstance(message, str):
                raise BridgeCallError(
                    code,
                    message,
                    {"http_status": exc.code},
                    retryable=exc.code >= 500,
                ) from exc
            raise BridgeCallError(
                "BRIDGE_HTTP_ERROR", f"Krita bridge returned HTTP {exc.code}", {}, True
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BridgeCallError(
                "BRIDGE_UNAVAILABLE",
                "Cannot reach the Krita bridge on 127.0.0.1. "
                "Is Krita running with the plugin enabled?",
                {"reason": str(exc)},
                True,
            ) from exc

        return self._decode_response(body, request.request_id)

    def _decode_response(self, body: bytes, expected_request_id: str) -> dict[str, Any]:
        if len(body) > self.config.max_response_bytes:
            raise BridgeCallError(
                "RESPONSE_TOO_LARGE", "bridge response exceeds configured limit", {}
            )
        try:
            raw = json.loads(body.decode("utf-8"))
            response = BridgeResponse.from_mapping(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ProtocolError) as exc:
            raise BridgeCallError(
                "INVALID_BRIDGE_RESPONSE",
                "Krita bridge returned an invalid response",
                {"reason": str(exc)},
            ) from exc
        if response.request_id != expected_request_id:
            raise BridgeCallError(
                "RESPONSE_MISMATCH", "bridge response request_id does not match the request", {}
            )
        if response.error:
            raise BridgeCallError(
                response.error.code,
                response.error.message,
                response.error.details,
                response.error.retryable,
            )
        return dict(response.result or {})
