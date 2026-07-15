from __future__ import annotations

import math
from pathlib import Path
from typing import Any


class InputError(ValueError):
    pass


def required_text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise InputError(f"{field} must not exceed {maximum} characters")
    return result


def optional_text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise InputError(f"{field} must be a string")
    result = value.strip()
    if len(result) > maximum:
        raise InputError(f"{field} must not exceed {maximum} characters")
    return result


def hex_color(value: Any) -> str:
    if not isinstance(value, str) or len(value) not in {7, 9} or not value.startswith("#"):
        raise InputError("foreground must be #RRGGBB or #RRGGBBAA")
    try:
        int(value[1:], 16)
    except ValueError as exc:
        raise InputError("foreground must be #RRGGBB or #RRGGBBAA") from exc
    return value.upper()


def bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InputError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def finite_number(value: Any, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{field} must be a number between {minimum} and {maximum}")
    result = float(value)
    if not math.isfinite(result):
        raise InputError(f"{field} must be finite")
    if not minimum <= result <= maximum:
        raise InputError(f"{field} must be a number between {minimum} and {maximum}")
    return result


def display_path(path: Path) -> str:
    return str(path.resolve(strict=False))
