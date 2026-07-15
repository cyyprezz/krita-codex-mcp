from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when the shared local bridge configuration is unsafe or invalid."""


def default_config_path() -> Path:
    override = os.environ.get("KRITA_CODEX_CONFIG")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ConfigError("KRITA_CODEX_CONFIG must be an absolute path")
        return path
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise ConfigError("LOCALAPPDATA is not set; set KRITA_CODEX_CONFIG explicitly")
    return Path(local_app_data) / "krita-codex-mcp" / "config.json"


@dataclass(frozen=True)
class BridgeConfig:
    token: str
    allowed_roots: tuple[Path, ...]
    host: str = "127.0.0.1"
    port: int = 17641
    request_timeout_seconds: float = 30.0
    max_request_bytes: int = 1_048_576
    max_response_bytes: int = 16_777_216
    preview_max_dimension: int = 2048
    preview_max_bytes: int = 8_388_608
    document_max_dimension: int = 16_384
    document_max_pixels: int = 67_108_864

    @property
    def execute_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1/execute"

    @classmethod
    def load(cls, path: Path | None = None) -> BridgeConfig:
        config_path = path or default_config_path()
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(
                f"Bridge configuration not found at {config_path}. Run scripts/install.ps1 first."
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Cannot read bridge configuration at {config_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError("Bridge configuration must be a JSON object")
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> BridgeConfig:
        token = raw.get("token")
        if not isinstance(token, str) or len(token) < 32:
            raise ConfigError("token must be a secret string of at least 32 characters")

        host = raw.get("host", "127.0.0.1")
        if host != "127.0.0.1":
            raise ConfigError("host must be exactly 127.0.0.1")

        roots = raw.get("allowed_roots")
        if not isinstance(roots, list) or not roots:
            raise ConfigError("allowed_roots must be a non-empty list")
        allowed_roots: list[Path] = []
        for value in roots:
            if not isinstance(value, str) or not value.strip():
                raise ConfigError("each allowed root must be a non-empty path string")
            path = Path(value).expanduser()
            if not path.is_absolute():
                raise ConfigError(f"allowed root must be absolute: {value}")
            allowed_roots.append(path.resolve(strict=False))

        port = _bounded_int(raw, "port", 17641, minimum=1024, maximum=65535)
        timeout = _bounded_float(raw, "request_timeout_seconds", 30.0, minimum=1.0, maximum=120.0)
        max_request = _bounded_int(
            raw, "max_request_bytes", 1_048_576, minimum=4096, maximum=4_194_304
        )
        max_response = _bounded_int(
            raw, "max_response_bytes", 16_777_216, minimum=65_536, maximum=67_108_864
        )
        preview_dimension = _bounded_int(
            raw, "preview_max_dimension", 2048, minimum=64, maximum=4096
        )
        preview_bytes = _bounded_int(
            raw, "preview_max_bytes", 8_388_608, minimum=65_536, maximum=max_response
        )
        document_dimension = _bounded_int(
            raw, "document_max_dimension", 16_384, minimum=64, maximum=32_768
        )
        document_pixels = _bounded_int(
            raw,
            "document_max_pixels",
            67_108_864,
            minimum=4096,
            maximum=268_435_456,
        )
        return cls(
            token=token,
            allowed_roots=tuple(allowed_roots),
            host=host,
            port=port,
            request_timeout_seconds=timeout,
            max_request_bytes=max_request,
            max_response_bytes=max_response,
            preview_max_dimension=preview_dimension,
            preview_max_bytes=preview_bytes,
            document_max_dimension=document_dimension,
            document_max_pixels=document_pixels,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "host": self.host,
            "port": self.port,
            "allowed_roots": [str(path) for path in self.allowed_roots],
            "request_timeout_seconds": self.request_timeout_seconds,
            "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
            "preview_max_dimension": self.preview_max_dimension,
            "preview_max_bytes": self.preview_max_bytes,
            "document_max_dimension": self.document_max_dimension,
            "document_max_pixels": self.document_max_pixels,
        }


def _bounded_int(
    raw: Mapping[str, Any], key: str, default: int, *, minimum: int, maximum: int
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigError(f"{key} must be an integer between {minimum} and {maximum}")
    return value


def _bounded_float(
    raw: Mapping[str, Any], key: str, default: float, *, minimum: float, maximum: float
) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} must be a number between {minimum} and {maximum}")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ConfigError(f"{key} must be a number between {minimum} and {maximum}")
    return result
