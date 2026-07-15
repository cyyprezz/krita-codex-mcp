from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from contextlib import suppress
from typing import Any
from uuid import uuid4

from PyQt5.QtCore import QRect, Qt

from krita_codex_protocol import PathPolicyError

from ._service_helpers import (
    _bounded_float,
    _bounded_int,
    _file_sha256,
    _fit_size,
    _required_text,
)
from .errors import KritaOperationError

REGION_PACKAGE_SCHEMA = "dev.desens.krita-codex.unity-regions"


class RegionOperationsMixin:
    def _export_region_asset(
        self,
        doc,
        node,
        arguments: Mapping[str, Any],
        *,
        action: str,
        overwrite: bool,
        preview_max_width: int,
        preview_max_height: int,
    ) -> dict[str, Any]:
        try:
            output_dir = self.path_policy.validate_output_directory(
                arguments.get("output_path"), overwrite=overwrite
            )
        except PathPolicyError as exc:
            raise KritaOperationError("PATH_NOT_ALLOWED", str(exc)) from exc

        pivot_x = _bounded_float(arguments.get("pivot_x", 0.5), "pivot_x", 0.0, 1.0)
        pivot_y = _bounded_float(arguments.get("pivot_y", 0.5), "pivot_y", 0.0, 1.0)
        pixels_per_unit = _bounded_float(
            arguments.get("pixels_per_unit", 100.0),
            "pixels_per_unit",
            0.01,
            10_000.0,
        )
        skip_empty = arguments.get("skip_empty", False)
        if not isinstance(skip_empty, bool):
            raise KritaOperationError("INVALID_ARGUMENT", "skip_empty must be a boolean")
        package_name = (
            _required_text(arguments["package_name"], "package_name", 120)
            if arguments.get("package_name") is not None
            else output_dir.name
        )
        canvas_width = int(doc.width())
        canvas_height = int(doc.height())
        canvas_rect = QRect(0, 0, canvas_width, canvas_height)
        if action == "export_tileset":
            specs, layout = self._grid_region_specs(arguments, canvas_width, canvas_height)
        else:
            specs, layout = self._explicit_region_specs(arguments, canvas_width, canvas_height)

        region_pixels = sum(spec["width"] * spec["height"] for spec in specs)
        render_pixels = canvas_width * canvas_height + region_pixels
        if render_pixels > self.config.asset_render_max_pixels:
            raise KritaOperationError(
                "REGION_RENDER_TOO_LARGE",
                "Region export exceeds the configured total render-pixel limit",
                {"pixels": render_pixels, "limit": self.config.asset_render_max_pixels},
            )

        stage_dir = output_dir.with_name(f".{output_dir.name}.{uuid4().hex}.tmp")
        stage_regions_dir = stage_dir / "regions"
        stage_sheet_path = stage_dir / "spritesheet.png"
        stage_manifest_path = stage_dir / "manifest.json"
        final_sheet_path = output_dir / "spritesheet.png"
        final_manifest_path = output_dir / "manifest.json"
        clone = None
        total_bytes = 0
        manifest_regions: list[dict[str, Any]] = []
        try:
            stage_regions_dir.mkdir(parents=True)
            clone = doc.clone()
            if clone is None:
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Krita could not clone the source document"
                )
            clone.waitForDone()
            sheet = self._isolated_projection(
                clone, self._node_index_path(doc, node), canvas_rect
            )
            if not sheet.save(str(stage_sheet_path), "PNG"):
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Krita could not encode the region spritesheet"
                )
            sheet_bytes = self._checked_asset_file(
                stage_sheet_path, label="Region spritesheet"
            )
            total_bytes += sheet_bytes

            for source_index, spec in enumerate(specs):
                rect = QRect(spec["x"], spec["y"], spec["width"], spec["height"])
                image = sheet.copy(rect)
                if image.isNull():
                    raise KritaOperationError(
                        "ASSET_EXPORT_FAILED",
                        "Krita could not extract a requested spritesheet region",
                        {"region_index": source_index},
                    )
                empty = self._image_alpha_bounds(image).isEmpty()
                if skip_empty and empty:
                    continue
                filename = f"{source_index:04d}.png"
                path = stage_regions_dir / filename
                if not image.save(str(path), "PNG"):
                    raise KritaOperationError(
                        "ASSET_EXPORT_FAILED",
                        "Krita could not encode a spritesheet region",
                        {"region_index": source_index},
                    )
                region_bytes = self._checked_asset_file(
                    path, label="Spritesheet region", region_index=source_index
                )
                total_bytes += region_bytes
                self._check_asset_batch_bytes(total_bytes, "Region image files")
                manifest_regions.append(
                    {
                        "index": len(manifest_regions),
                        "source_index": source_index,
                        "name": spec["name"],
                        "file": f"regions/{filename}",
                        "empty": empty,
                        "sha256": _file_sha256(path),
                        "sheet_rect_png_top_left": {
                            "x": spec["x"],
                            "y": spec["y"],
                            "width": spec["width"],
                            "height": spec["height"],
                        },
                        "sprite_rect_unity_bottom_left": {
                            "x": spec["x"],
                            "y": canvas_height - spec["y"] - spec["height"],
                            "width": spec["width"],
                            "height": spec["height"],
                        },
                        "pivot": {
                            "x": pivot_x,
                            "y": pivot_y,
                            "space": "region_bottom_left_normalized",
                        },
                    }
                )
            if not manifest_regions:
                raise KritaOperationError(
                    "EMPTY_REGIONS",
                    "No non-empty regions remain after applying skip_empty",
                )

            manifest = {
                "schema": REGION_PACKAGE_SCHEMA,
                "schema_version": 1,
                "name": package_name,
                "source": {
                    "document_id": self._document_id(doc),
                    "document_name": doc.name(),
                    "document_file_name": doc.fileName(),
                    "layer_id": self._node_id(node),
                    "layer_name": node.name(),
                    "layer_type": node.type(),
                    "document_id_scope": "krita_session",
                },
                "canvas": {"width": canvas_width, "height": canvas_height},
                "layout": layout,
                "unity": {
                    "pixels_per_unit": pixels_per_unit,
                    "filter_mode": "Point",
                    "texture_type": "Sprite",
                    "sprite_mode": "Multiple",
                    "wrap_mode": "Clamp",
                    "mipmaps": False,
                    "alpha_is_transparency": True,
                    "mesh_type": "FullRect",
                },
                "sheet": {
                    "file": stage_sheet_path.name,
                    "width": canvas_width,
                    "height": canvas_height,
                    "png_coordinate_origin": "top_left",
                    "unity_coordinate_origin": "bottom_left",
                    "sha256": _file_sha256(stage_sheet_path),
                },
                "region_count": len(manifest_regions),
                "source_region_count": len(specs),
                "skip_empty": skip_empty,
                "regions": manifest_regions,
            }
            manifest_payload = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
            total_bytes += len(manifest_payload)
            self._check_asset_batch_bytes(total_bytes, "Region package")
            stage_manifest_path.write_bytes(manifest_payload)

            preview_width, preview_height = _fit_size(
                canvas_width,
                canvas_height,
                preview_max_width,
                preview_max_height,
            )
            preview_image = sheet.scaled(
                preview_width,
                preview_height,
                Qt.KeepAspectRatio,
                Qt.FastTransformation,
            )
            preview_png = self._encode_png_preview(preview_image)
            self._commit_asset_directory(
                stage_dir,
                output_dir,
                overwrite=overwrite,
                schema=REGION_PACKAGE_SCHEMA,
                item_directory="regions",
                package_label="region",
            )
        except KritaOperationError:
            raise
        except Exception as exc:
            raise KritaOperationError(
                "ASSET_EXPORT_FAILED",
                "Krita could not export the region package",
                {"reason": str(exc)},
            ) from exc
        finally:
            if clone is not None:
                with suppress(Exception):
                    clone.setModified(False)
                    clone.close()
            self._remove_asset_path(stage_dir)

        self.bridge_revision += 1
        return {
            "action": action,
            "document_id": self._document_id(doc),
            "document_file_name": doc.fileName(),
            "layer_id": self._node_id(node),
            "layer_name": node.name(),
            "layout": layout,
            "sheet": {
                "path": str(final_sheet_path),
                "width": canvas_width,
                "height": canvas_height,
            },
            "output": {
                "kind": "region_package",
                "path": str(output_dir),
                "manifest_path": str(final_manifest_path),
                "regions_path": str(output_dir / "regions"),
                "bytes": total_bytes,
            },
            "region_count": len(manifest_regions),
            "source_region_count": len(specs),
            "regions": manifest_regions,
            "render_pixels": render_pixels,
            "preview_width": int(preview_image.width()),
            "preview_height": int(preview_image.height()),
            "png_base64": base64.b64encode(preview_png).decode("ascii"),
        }

    def _grid_region_specs(
        self, arguments: Mapping[str, Any], canvas_width: int, canvas_height: int
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        tile_width = _bounded_int(
            arguments.get("tile_width"), "tile_width", 1, canvas_width
        )
        tile_height = _bounded_int(
            arguments.get("tile_height"), "tile_height", 1, canvas_height
        )
        origin_x = _bounded_int(
            arguments.get("origin_x", 0), "origin_x", 0, canvas_width - 1
        )
        origin_y = _bounded_int(
            arguments.get("origin_y", 0), "origin_y", 0, canvas_height - 1
        )
        spacing_x = _bounded_int(
            arguments.get("spacing_x", 0),
            "spacing_x",
            0,
            self.config.document_max_dimension,
        )
        spacing_y = _bounded_int(
            arguments.get("spacing_y", 0),
            "spacing_y",
            0,
            self.config.document_max_dimension,
        )
        columns_raw = arguments.get("grid_columns")
        rows_raw = arguments.get("grid_rows")
        if (columns_raw is None) != (rows_raw is None):
            raise KritaOperationError(
                "INVALID_ARGUMENT", "grid_columns and grid_rows must be supplied together"
            )
        if columns_raw is None:
            available_width = canvas_width - origin_x
            available_height = canvas_height - origin_y
            columns = (
                0
                if available_width < tile_width
                else 1 + (available_width - tile_width) // (tile_width + spacing_x)
            )
            rows = (
                0
                if available_height < tile_height
                else 1 + (available_height - tile_height) // (tile_height + spacing_y)
            )
        else:
            columns = _bounded_int(
                columns_raw, "grid_columns", 1, self.config.asset_max_frames
            )
            rows = _bounded_int(rows_raw, "grid_rows", 1, self.config.asset_max_frames)
        if columns <= 0 or rows <= 0:
            raise KritaOperationError(
                "EMPTY_REGIONS", "The tile grid does not contain a complete tile"
            )
        if columns * rows > self.config.asset_max_frames:
            raise KritaOperationError(
                "TOO_MANY_REGIONS",
                "Tile grid exceeds the configured region limit",
                {"regions": columns * rows, "limit": self.config.asset_max_frames},
            )
        final_x = origin_x + (columns - 1) * (tile_width + spacing_x) + tile_width
        final_y = origin_y + (rows - 1) * (tile_height + spacing_y) + tile_height
        if final_x > canvas_width or final_y > canvas_height:
            raise KritaOperationError(
                "REGION_OUT_OF_BOUNDS", "The requested tile grid exceeds the document canvas"
            )
        prefix = (
            _required_text(arguments["name_prefix"], "name_prefix", 100)
            if arguments.get("name_prefix") is not None
            else "tile"
        )
        specs = [
            {
                "name": f"{prefix}_{row:02d}_{column:02d}",
                "x": origin_x + column * (tile_width + spacing_x),
                "y": origin_y + row * (tile_height + spacing_y),
                "width": tile_width,
                "height": tile_height,
            }
            for row in range(rows)
            for column in range(columns)
        ]
        return specs, {
            "kind": "grid",
            "origin_x": origin_x,
            "origin_y": origin_y,
            "tile_width": tile_width,
            "tile_height": tile_height,
            "columns": columns,
            "rows": rows,
            "spacing_x": spacing_x,
            "spacing_y": spacing_y,
            "order": "row_major_top_left",
        }

    def _explicit_region_specs(
        self, arguments: Mapping[str, Any], canvas_width: int, canvas_height: int
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        raw_regions = arguments.get("regions")
        if (
            not isinstance(raw_regions, list)
            or not 1 <= len(raw_regions) <= self.config.asset_max_frames
        ):
            raise KritaOperationError(
                "INVALID_ARGUMENT",
                "regions must be a non-empty list within the configured region limit",
            )
        specs: list[dict[str, Any]] = []
        normalized_names: set[str] = set()
        for index, raw in enumerate(raw_regions):
            if not isinstance(raw, Mapping):
                raise KritaOperationError(
                    "INVALID_ARGUMENT", f"regions[{index}] must be an object"
                )
            name = _required_text(raw.get("name"), f"regions[{index}].name", 120)
            name_key = name.casefold()
            if name_key in normalized_names:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "region names must be unique ignoring case"
                )
            normalized_names.add(name_key)
            x = _bounded_int(raw.get("x"), f"regions[{index}].x", 0, canvas_width - 1)
            y = _bounded_int(raw.get("y"), f"regions[{index}].y", 0, canvas_height - 1)
            width = _bounded_int(raw.get("width"), f"regions[{index}].width", 1, canvas_width)
            height = _bounded_int(
                raw.get("height"), f"regions[{index}].height", 1, canvas_height
            )
            if x + width > canvas_width or y + height > canvas_height:
                raise KritaOperationError(
                    "REGION_OUT_OF_BOUNDS",
                    f"regions[{index}] exceeds the document canvas",
                )
            specs.append(
                {"name": name, "x": x, "y": y, "width": width, "height": height}
            )
        return specs, {"kind": "explicit", "order": "request_order"}

    def _checked_asset_file(self, path, *, label: str, region_index: int | None = None) -> int:
        size = path.stat().st_size
        if size <= 0 or size > self.config.asset_max_bytes:
            details: dict[str, Any] = {
                "bytes": size,
                "limit": self.config.asset_max_bytes,
            }
            if region_index is not None:
                details["region_index"] = region_index
            raise KritaOperationError(
                "ASSET_TOO_LARGE", f"{label} exceeds the configured byte limit", details
            )
        return size

    def _check_asset_batch_bytes(self, total_bytes: int, label: str) -> None:
        if total_bytes > self.config.asset_batch_max_bytes:
            raise KritaOperationError(
                "ASSET_BATCH_TOO_LARGE",
                f"{label} exceed the configured total byte limit",
                {"bytes": total_bytes, "limit": self.config.asset_batch_max_bytes},
            )
