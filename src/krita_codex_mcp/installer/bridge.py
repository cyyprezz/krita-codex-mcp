from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from krita_codex_protocol import BridgeConfig, BridgeRequest, BridgeResponse, ProtocolError

from .errors import BridgeRequestError, InstallerError


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class ActiveBridge:
    distribution_version: str | None
    protocol_version: str | None


class BridgeAPI:
    def __init__(self, config: BridgeConfig, *, probe_timeout: float | None = None) -> None:
        self.config = config
        self.probe_timeout = probe_timeout
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def execute(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request = BridgeRequest(operation=operation, arguments=arguments)
        encoded = json.dumps(request.to_mapping(), separators=(",", ":")).encode("utf-8")
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
        timeout = self.probe_timeout
        if timeout is None:
            timeout = self.config.timeout_for(operation, arguments) + 5.0
        try:
            with self._opener.open(http_request, timeout=timeout) as response:
                body = response.read(self.config.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            body = exc.read(self.config.max_response_bytes + 1)
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise InstallerError(
                    "The authenticated local Krita bridge timed out."
                ) from exc
            raise InstallerError("The authenticated local Krita bridge is unavailable.") from exc
        except TimeoutError as exc:
            raise InstallerError("The authenticated local Krita bridge timed out.") from exc
        except OSError as exc:
            raise InstallerError("The authenticated local Krita bridge is unavailable.") from exc
        if len(body) > self.config.max_response_bytes:
            raise InstallerError("The local Krita bridge response exceeds the configured limit.")
        try:
            response = BridgeResponse.from_mapping(json.loads(body.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, ProtocolError) as exc:
            raise InstallerError("The local Krita bridge returned an invalid response.") from exc
        if response.request_id != request.request_id:
            raise InstallerError("The local Krita bridge response does not match the request.")
        if response.error is not None:
            raise BridgeRequestError(
                response.error.code,
                response.error.message,
                details=dict(response.error.details),
            )
        return dict(response.result or {})


def _is_connection_refused(error: OSError) -> bool:
    return isinstance(error, ConnectionRefusedError) or (
        getattr(error, "winerror", None) == 10061
    )


def probe_authenticated_bridge(
    config: BridgeConfig,
    *,
    timeout_seconds: float = 2.0,
) -> ActiveBridge | None:
    try:
        with socket.create_connection(
            (config.host, config.port),
            timeout=timeout_seconds,
        ):
            pass
    except OSError as exc:
        if _is_connection_refused(exc):
            return None
        if isinstance(exc, TimeoutError):
            message = "The local bridge port did not refuse the TCP probe before timeout."
        else:
            message = "The local bridge port could not be proven stopped or free."
        raise InstallerError(
            message
            + " No installer files were changed; close Krita or free the configured port "
            "and retry."
        ) from exc

    try:
        result = BridgeAPI(config, probe_timeout=timeout_seconds).execute(
            "document", {"action": "list"}
        )
    except BridgeRequestError as exc:
        raise InstallerError(
            "The local port accepted TCP and returned a bridge error "
            f"({exc.code}). No installer files were changed; close Krita manually "
            "or free the configured port and retry."
        ) from exc
    except InstallerError as exc:
        raise InstallerError(
            "The local port accepted TCP but did not return a valid authenticated bridge "
            f"response: {exc}. No installer files were changed; close Krita manually or "
            "free the configured port and retry."
        ) from exc
    distribution_version = result.get("bridge_distribution_version")
    protocol_version = result.get("bridge_protocol_version")
    return ActiveBridge(
        distribution_version=(
            distribution_version if isinstance(distribution_version, str) else None
        ),
        protocol_version=protocol_version if isinstance(protocol_version, str) else None,
    )
