from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from krita_codex_protocol import PROTOCOL_VERSION

from .errors import InstallerError
from .paths import InstallPaths

MANIFEST_NAME = "_install_info.json"
MANIFEST_SCHEMA_VERSION = 1


def _managed_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory_name in ("kritacodexbridge", "krita_codex_protocol"):
        directory = root / directory_name
        if directory.is_symlink() or not directory.is_dir():
            raise InstallerError("The staged bridge payload is incomplete.")
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise InstallerError("The bridge payload must not contain symbolic links.")
            if not path.is_file() or _ignored_runtime_file(path):
                continue
            if path.name == MANIFEST_NAME:
                continue
            files.append(path)
    desktop = root / "kritacodexbridge.desktop"
    if desktop.is_symlink() or not desktop.is_file():
        raise InstallerError("The staged Krita desktop descriptor is missing.")
    files.append(desktop)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _ignored_runtime_file(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest(stage_root: Path, distribution_version: str) -> dict[str, Any]:
    files = {
        path.relative_to(stage_root).as_posix(): _sha256(path)
        for path in _managed_files(stage_root)
    }
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "distribution_version": distribution_version,
        "protocol_version": PROTOCOL_VERSION,
        "files": files,
    }


def write_manifest(stage_root: Path, distribution_version: str) -> dict[str, Any]:
    manifest = build_manifest(stage_root, distribution_version)
    destination = stage_root / "kritacodexbridge" / MANIFEST_NAME
    destination.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallerError("The installed bridge manifest is missing or invalid.") from exc
    if not isinstance(raw, dict):
        raise InstallerError("The installed bridge manifest must be a JSON object.")
    return raw


def validate_manifest_shape(
    manifest: Mapping[str, Any], *, expected_version: str
) -> Mapping[str, str]:
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise InstallerError("The installed bridge manifest schema is unsupported.")
    if manifest.get("distribution_version") != expected_version:
        raise InstallerError("The installed bridge distribution version does not match.")
    if manifest.get("protocol_version") != PROTOCOL_VERSION:
        raise InstallerError("The installed bridge protocol version does not match.")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise InstallerError("The installed bridge manifest contains no managed files.")
    for relative, digest in files.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise InstallerError("The installed bridge manifest has invalid file entries.")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise InstallerError("The installed bridge manifest contains an unsafe path.")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise InstallerError("The installed bridge manifest contains an invalid digest.")
    return files


def verify_payload_root(root: Path, expected_version: str) -> dict[str, Any]:
    manifest_path = root / "kritacodexbridge" / MANIFEST_NAME
    manifest = load_manifest(manifest_path)
    expected_files = validate_manifest_shape(manifest, expected_version=expected_version)
    actual_files = {
        path.relative_to(root).as_posix(): path for path in _managed_files(root)
    }
    if set(actual_files) != set(expected_files):
        raise InstallerError("The installed bridge file set does not match its manifest.")
    for relative, expected_digest in expected_files.items():
        if _sha256(actual_files[relative]) != expected_digest:
            raise InstallerError("An installed bridge file does not match its manifest.")
    return manifest


def verify_installed_payload(paths: InstallPaths, expected_version: str) -> dict[str, Any]:
    return verify_payload_root(paths.pykrita_root, expected_version)
