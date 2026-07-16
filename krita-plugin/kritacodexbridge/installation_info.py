from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def loaded_installation_info() -> dict[str, Any]:
    """Return the installer manifest loaded by this exact plugin package."""
    path = Path(__file__).with_name("_install_info.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            "distribution_version": "unmanaged",
            "protocol_version": "unknown",
        }
    if not isinstance(raw, dict):
        return {
            "distribution_version": "unmanaged",
            "protocol_version": "unknown",
        }
    distribution_version = raw.get("distribution_version")
    protocol_version = raw.get("protocol_version")
    if not isinstance(distribution_version, str) or not distribution_version:
        distribution_version = "unmanaged"
    if not isinstance(protocol_version, str) or not protocol_version:
        protocol_version = "unknown"
    return {
        "distribution_version": distribution_version,
        "protocol_version": protocol_version,
    }
