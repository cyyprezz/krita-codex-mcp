from __future__ import annotations

import base64
import binascii

from .bridge_client import BridgeCallError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def validate_png_base64(
    value: object,
    *,
    missing_message: str,
    malformed_message: str,
) -> int:
    if not isinstance(value, str):
        raise BridgeCallError("INVALID_PREVIEW", missing_message, {})
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BridgeCallError("INVALID_PREVIEW", malformed_message, {}) from exc
    if not decoded.startswith(PNG_SIGNATURE):
        raise BridgeCallError(
            "INVALID_PREVIEW",
            "bridge response is not a PNG image",
            {"expected_signature": "89504e470d0a1a0a"},
        )
    return len(decoded)
