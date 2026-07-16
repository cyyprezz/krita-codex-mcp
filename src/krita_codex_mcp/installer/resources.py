from __future__ import annotations

import shutil
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from .errors import InstallerError
from .manifest import verify_payload_root, write_manifest


def _plugin_resource_root() -> Traversable:
    packaged = resources.files("krita_codex_mcp").joinpath(
        "_resources", "krita_plugin"
    )
    if packaged.joinpath("kritacodexbridge.desktop").is_file():
        return packaged
    repository_root = Path(__file__).resolve().parents[3]
    checkout = repository_root / "krita-plugin"
    if (checkout / "kritacodexbridge.desktop").is_file():
        return checkout
    raise InstallerError("The wheel does not contain the Krita plugin payload.")


def _copy_tree(source: Traversable, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        if child.name == "__pycache__" or child.name.endswith((".pyc", ".pyo")):
            continue
        target = destination / child.name
        if child.is_dir():
            _copy_tree(child, target)
        elif child.is_file():
            with child.open("rb") as source_handle, target.open("wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle)


def stage_distribution_payload(stage_root: Path, distribution_version: str) -> None:
    plugin_resource = _plugin_resource_root()
    _copy_tree(
        plugin_resource.joinpath("kritacodexbridge"),
        stage_root / "kritacodexbridge",
    )
    desktop_source = plugin_resource.joinpath("kritacodexbridge.desktop")
    stage_root.mkdir(parents=True, exist_ok=True)
    with desktop_source.open("rb") as source_handle, (
        stage_root / "kritacodexbridge.desktop"
    ).open("wb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle)
    _copy_tree(
        resources.files("krita_codex_protocol"),
        stage_root / "krita_codex_protocol",
    )
    write_manifest(stage_root, distribution_version)
    verify_payload_root(stage_root, distribution_version)
