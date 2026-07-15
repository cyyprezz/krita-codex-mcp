from __future__ import annotations

import json
import os
import re
import shutil
from contextlib import suppress
from typing import Any
from uuid import uuid4

from .errors import KritaOperationError


class PackageOperationsMixin:
    def _commit_asset_directory(
        self,
        stage_dir,
        output_dir,
        *,
        overwrite: bool,
        schema: str,
        item_directory: str,
        package_label: str,
    ) -> None:
        backup_dir = output_dir.with_name(f".{output_dir.name}.{uuid4().hex}.backup")
        installed: list[Any] = []
        backed_up: list[tuple[Any, Any]] = []
        committed = False
        try:
            staged_manifest_path = stage_dir / "manifest.json"
            try:
                staged_manifest = json.loads(
                    staged_manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Staged package manifest is unreadable"
                ) from exc
            if staged_manifest.get("schema") != schema or staged_manifest.get(
                "schema_version"
            ) != 1:
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Staged package manifest has an unexpected schema"
                )
            if not output_dir.exists():
                os.rename(stage_dir, output_dir)
                return
            if not overwrite:
                raise KritaOperationError(
                    "OUTPUT_EXISTS",
                    "Output directory appeared after validation; overwrite remains disabled",
                    {"path": str(output_dir)},
                )
            manifest_path = output_dir / "manifest.json"
            final_items_dir = output_dir / item_directory
            protected_paths = [output_dir / "spritesheet.png", manifest_path, final_items_dir]
            if any(path.is_symlink() for path in protected_paths):
                raise KritaOperationError(
                    "UNMANAGED_OUTPUT_DIRECTORY",
                    "Managed package paths must not be symbolic links",
                    {"path": str(output_dir)},
                )
            existing_entries = list(output_dir.iterdir())
            if existing_entries:
                if not manifest_path.is_file():
                    raise KritaOperationError(
                        "UNMANAGED_OUTPUT_DIRECTORY",
                        "Refusing to overwrite a non-empty directory without a managed manifest",
                        {"path": str(output_dir)},
                    )
                try:
                    existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise KritaOperationError(
                        "UNMANAGED_OUTPUT_DIRECTORY",
                        "Existing package manifest is unreadable",
                        {"path": str(manifest_path)},
                    ) from exc
                if existing_manifest.get("schema") != schema or existing_manifest.get(
                    "schema_version"
                ) != 1:
                    raise KritaOperationError(
                        "UNMANAGED_OUTPUT_DIRECTORY",
                        f"Existing directory is not a Krita Codex {package_label} package",
                        {"path": str(output_dir)},
                    )

            backup_dir.mkdir()
            owned_paths = [output_dir / "spritesheet.png", output_dir / "manifest.json"]
            if final_items_dir.is_dir():
                owned_paths.extend(
                    path
                    for path in final_items_dir.iterdir()
                    if path.is_file() and re.fullmatch(r"\d{4}\.png", path.name)
                )
            for source in owned_paths:
                if not source.exists():
                    continue
                relative = source.relative_to(output_dir)
                backup = backup_dir / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, backup)
                backed_up.append((source, backup))

            final_items_dir.mkdir(exist_ok=True)
            staged_paths = [stage_dir / "spritesheet.png"]
            staged_paths.extend(sorted((stage_dir / item_directory).glob("*.png")))
            staged_paths.append(stage_dir / "manifest.json")
            for source in staged_paths:
                relative = source.relative_to(stage_dir)
                destination = output_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
                installed.append(destination)
            committed = True
        except KritaOperationError:
            raise
        except Exception as exc:
            raise KritaOperationError(
                "ASSET_EXPORT_FAILED",
                f"{package_label.capitalize()} package could not be finalized transactionally",
                {"path": str(output_dir), "reason": str(exc)},
            ) from exc
        finally:
            if backup_dir.exists():
                if committed:
                    self._remove_asset_path(backup_dir)
                else:
                    for destination in reversed(installed):
                        with suppress(OSError):
                            destination.unlink()
                    for destination, backup in reversed(backed_up):
                        if backup.exists():
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            with suppress(OSError):
                                os.replace(backup, destination)
                    self._remove_asset_path(backup_dir)

    def _remove_asset_path(self, path) -> None:
        with suppress(OSError):
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
