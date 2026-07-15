from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

PROTOCOL_VERSION = "1"
ALLOWED_OPERATIONS = frozenset(
    {
        "state",
        "preview",
        "document",
        "layers",
        "history",
        "brush",
        "paint_path",
        "save_export",
    }
)


class ProtocolError(ValueError):
    """Raised when a request or response violates the bridge protocol."""


@dataclass(frozen=True)
class BridgeError:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)
    retryable: bool = False

    def to_mapping(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
            "retryable": self.retryable,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> BridgeError:
        code = raw.get("code")
        message = raw.get("message")
        details = raw.get("details", {})
        retryable = raw.get("retryable", False)
        if not isinstance(code, str) or not code:
            raise ProtocolError("error.code must be a non-empty string")
        if not isinstance(message, str) or not message:
            raise ProtocolError("error.message must be a non-empty string")
        if not isinstance(details, dict) or not isinstance(retryable, bool):
            raise ProtocolError("error.details or error.retryable has an invalid type")
        return cls(code=code, message=message, details=details, retryable=retryable)


@dataclass(frozen=True)
class BridgeRequest:
    operation: str
    arguments: Mapping[str, Any]
    request_id: str = field(default_factory=lambda: str(uuid4()))
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _validate_uuid(self.request_id, "request_id")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(f"unsupported protocol version: {self.protocol_version}")
        if self.operation not in ALLOWED_OPERATIONS:
            raise ProtocolError(f"unsupported operation: {self.operation}")
        if not isinstance(self.arguments, Mapping):
            raise ProtocolError("arguments must be an object")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "operation": self.operation,
            "arguments": dict(self.arguments),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> BridgeRequest:
        if not isinstance(raw, Mapping):
            raise ProtocolError("request must be an object")
        return cls(
            protocol_version=raw.get("protocol_version", ""),
            request_id=raw.get("request_id", ""),
            operation=raw.get("operation", ""),
            arguments=raw.get("arguments", {}),
        )


@dataclass(frozen=True)
class BridgeResponse:
    request_id: str
    result: Mapping[str, Any] | None = None
    error: BridgeError | None = None
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _validate_uuid(self.request_id, "request_id")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(f"unsupported protocol version: {self.protocol_version}")
        if (self.result is None) == (self.error is None):
            raise ProtocolError("response must contain exactly one of result or error")

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "ok": self.ok,
        }
        if self.ok:
            payload["result"] = dict(self.result or {})
        else:
            payload["error"] = self.error.to_mapping() if self.error else {}
        return payload

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> BridgeResponse:
        if not isinstance(raw, Mapping):
            raise ProtocolError("response must be an object")
        ok = raw.get("ok")
        if not isinstance(ok, bool):
            raise ProtocolError("response.ok must be a boolean")
        result = raw.get("result") if ok else None
        error_raw = raw.get("error") if not ok else None
        if ok and not isinstance(result, dict):
            raise ProtocolError("successful response.result must be an object")
        if not ok and not isinstance(error_raw, dict):
            raise ProtocolError("failed response.error must be an object")
        return cls(
            protocol_version=raw.get("protocol_version", ""),
            request_id=raw.get("request_id", ""),
            result=result,
            error=BridgeError.from_mapping(error_raw) if error_raw is not None else None,
        )


def _validate_uuid(value: Any, field_name: str) -> None:
    if not isinstance(value, str):
        raise ProtocolError(f"{field_name} must be a UUID string")
    try:
        UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ProtocolError(f"{field_name} must be a UUID string") from exc
