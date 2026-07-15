from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any

from PyQt5.QtGui import QColor

from .errors import KritaOperationError


def _safe_call(target, method: str, default=None):
    if target is None:
        return default
    function = getattr(target, method, None)
    if function is None:
        return default
    try:
        return function()
    except Exception:
        return default


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KritaOperationError("INVALID_ARGUMENT", f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise KritaOperationError(
            "INVALID_ARGUMENT", f"{field} must not exceed {maximum} characters"
        )
    return result


def _optional_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise KritaOperationError("INVALID_ARGUMENT", f"{field} must be a string")
    result = value.strip()
    if len(result) > maximum:
        raise KritaOperationError(
            "INVALID_ARGUMENT", f"{field} must not exceed {maximum} characters"
        )
    return result


def _optional_bool(arguments: Mapping[str, Any], field: str) -> bool | None:
    if field not in arguments:
        return None
    value = arguments[field]
    if not isinstance(value, bool):
        raise KritaOperationError("INVALID_ARGUMENT", f"{field} must be a boolean")
    return value


def _parse_qcolor(value: Any) -> QColor:
    text = _required_text(value, "foreground", 9)
    if len(text) not in {7, 9} or not text.startswith("#"):
        raise KritaOperationError("INVALID_ARGUMENT", "foreground must be #RRGGBB or #RRGGBBAA")
    try:
        int(text[1:], 16)
    except ValueError as exc:
        raise KritaOperationError(
            "INVALID_ARGUMENT", "foreground must be #RRGGBB or #RRGGBBAA"
        ) from exc
    color = QColor(text[:7])
    if len(text) == 9:
        color.setAlpha(int(text[7:9], 16))
    if not color.isValid():
        raise KritaOperationError("INVALID_ARGUMENT", "foreground color is invalid")
    return color


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise KritaOperationError(
            "INVALID_ARGUMENT", f"{field} must be an integer between {minimum} and {maximum}"
        )
    return value


def _bounded_float(value: Any, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KritaOperationError(
            "INVALID_ARGUMENT", f"{field} must be a number between {minimum} and {maximum}"
        )
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise KritaOperationError(
            "INVALID_ARGUMENT", f"{field} must be a number between {minimum} and {maximum}"
        )
    return result


def _fit_size(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    scale = min(max_width / width, max_height / height, 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _file_sha256(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
