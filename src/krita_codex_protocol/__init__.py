"""Dependency-free protocol shared by the MCP process and the Krita plugin."""

from .config import BridgeConfig, ConfigError, default_config_path
from .messages import (
    PROTOCOL_VERSION,
    BridgeError,
    BridgeRequest,
    BridgeResponse,
    ProtocolError,
)
from .paths import KRITA_DOCUMENT_EXTENSIONS, PathPolicy, PathPolicyError

__all__ = [
    "PROTOCOL_VERSION",
    "BridgeConfig",
    "BridgeError",
    "BridgeRequest",
    "BridgeResponse",
    "ConfigError",
    "KRITA_DOCUMENT_EXTENSIONS",
    "PathPolicy",
    "PathPolicyError",
    "ProtocolError",
    "default_config_path",
]
