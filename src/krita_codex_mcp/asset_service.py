from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

from krita_codex_protocol import PathPolicyError

from .bridge_client import BridgeCallError
from .validation import InputError, bounded_int, finite_number, required_text

ASSET_ACTIONS = {
    "export_layer",
    "export_animation",
    "export_tileset",
    "export_regions",
}


class AssetServiceMixin:
    async def asset(
        self,
        *,
        action: str,
        layer_id: str,
        output_path: str,
        crop: str = "canvas",
        overwrite: bool = False,
        document_id: str | None = None,
        preview_max_width: int = 1024,
        preview_max_height: int = 1024,
        frame_ids: list[str] | None = None,
        animation_name: str | None = None,
        columns: int = 8,
        padding: int = 0,
        frame_duration_ms: int = 100,
        pivot_x: float = 0.5,
        pivot_y: float = 0.5,
        pixels_per_unit: float = 100.0,
        loop: bool = True,
        regions: list[Mapping[str, Any]] | None = None,
        package_name: str | None = None,
        tile_width: int | None = None,
        tile_height: int | None = None,
        origin_x: int = 0,
        origin_y: int = 0,
        grid_columns: int | None = None,
        grid_rows: int | None = None,
        spacing_x: int = 0,
        spacing_y: int = 0,
        skip_empty: bool = False,
        name_prefix: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        if action not in ASSET_ACTIONS:
            raise InputError(
                "action must be export_layer, export_animation, export_tileset, or export_regions"
            )
        if not isinstance(overwrite, bool):
            raise InputError("overwrite must be a boolean")
        validated_output_path = self._asset_output_path(
            action=action,
            output_path=output_path,
            crop=crop,
            overwrite=overwrite,
        )
        arguments: dict[str, Any] = {
            "action": action,
            "layer_id": required_text(layer_id, "layer_id", maximum=80),
            "output_path": str(validated_output_path),
            "overwrite": overwrite,
            "preview_max_width": bounded_int(
                preview_max_width,
                "preview_max_width",
                64,
                self.config.preview_max_dimension,
            ),
            "preview_max_height": bounded_int(
                preview_max_height,
                "preview_max_height",
                64,
                self.config.preview_max_dimension,
            ),
        }
        if action in {"export_layer", "export_animation"}:
            arguments["crop"] = crop
        if action == "export_animation":
            self._add_animation_options(
                arguments,
                frame_ids=frame_ids,
                animation_name=animation_name,
                columns=columns,
                padding=padding,
                frame_duration_ms=frame_duration_ms,
                pivot_x=pivot_x,
                pivot_y=pivot_y,
                pixels_per_unit=pixels_per_unit,
                loop=loop,
            )
        elif action in {"export_tileset", "export_regions"}:
            self._add_region_options(
                arguments,
                action=action,
                regions=regions,
                package_name=package_name,
                tile_width=tile_width,
                tile_height=tile_height,
                origin_x=origin_x,
                origin_y=origin_y,
                grid_columns=grid_columns,
                grid_rows=grid_rows,
                spacing_x=spacing_x,
                spacing_y=spacing_y,
                skip_empty=skip_empty,
                name_prefix=name_prefix,
                pivot_x=pivot_x,
                pivot_y=pivot_y,
                pixels_per_unit=pixels_per_unit,
            )
        if document_id is not None:
            arguments["document_id"] = required_text(
                document_id, "document_id", maximum=80
            )
        result = await self.client.execute("asset", arguments)
        return self._extract_asset_preview(result)

    def _asset_output_path(
        self, *, action: str, output_path: str, crop: str, overwrite: bool
    ):
        try:
            raw_output_path = required_text(output_path, "output_path", maximum=32_767)
            if action == "export_layer":
                if crop not in {"canvas", "content"}:
                    raise InputError("export_layer crop must be canvas or content")
                return self.paths.validate_output(
                    raw_output_path, extensions={".png"}, overwrite=overwrite
                )
            if action == "export_animation" and crop not in {"canvas", "union"}:
                raise InputError("export_animation crop must be canvas or union")
            return self.paths.validate_output_directory(
                raw_output_path, overwrite=overwrite
            )
        except PathPolicyError as exc:
            raise InputError(str(exc)) from exc

    def _add_animation_options(
        self,
        arguments: dict[str, Any],
        *,
        frame_ids: list[str] | None,
        animation_name: str | None,
        columns: int,
        padding: int,
        frame_duration_ms: int,
        pivot_x: float,
        pivot_y: float,
        pixels_per_unit: float,
        loop: bool,
    ) -> None:
        if frame_ids is not None:
            if (
                not isinstance(frame_ids, list)
                or not 1 <= len(frame_ids) <= self.config.asset_max_frames
            ):
                raise InputError(
                    "frame_ids must contain between 1 and "
                    f"{self.config.asset_max_frames} entries"
                )
            normalized_ids = [
                required_text(value, f"frame_ids[{index}]", maximum=80)
                for index, value in enumerate(frame_ids)
            ]
            normalized_keys = {
                value.strip().strip("{}").lower() for value in normalized_ids
            }
            if len(normalized_keys) != len(normalized_ids):
                raise InputError("frame_ids must not contain duplicates")
            arguments["frame_ids"] = normalized_ids
        if animation_name is not None:
            arguments["animation_name"] = required_text(
                animation_name, "animation_name", maximum=120
            )
        if not isinstance(loop, bool):
            raise InputError("loop must be a boolean")
        arguments.update(
            {
                "columns": bounded_int(columns, "columns", 1, 64),
                "padding": bounded_int(padding, "padding", 0, 64),
                "frame_duration_ms": bounded_int(
                    frame_duration_ms, "frame_duration_ms", 1, 60_000
                ),
                "pivot_x": finite_number(pivot_x, "pivot_x", 0.0, 1.0),
                "pivot_y": finite_number(pivot_y, "pivot_y", 0.0, 1.0),
                "pixels_per_unit": finite_number(
                    pixels_per_unit, "pixels_per_unit", 0.01, 10_000.0
                ),
                "loop": loop,
            }
        )

    def _add_region_options(
        self,
        arguments: dict[str, Any],
        *,
        action: str,
        regions: list[Mapping[str, Any]] | None,
        package_name: str | None,
        tile_width: int | None,
        tile_height: int | None,
        origin_x: int,
        origin_y: int,
        grid_columns: int | None,
        grid_rows: int | None,
        spacing_x: int,
        spacing_y: int,
        skip_empty: bool,
        name_prefix: str | None,
        pivot_x: float,
        pivot_y: float,
        pixels_per_unit: float,
    ) -> None:
        if not isinstance(skip_empty, bool):
            raise InputError("skip_empty must be a boolean")
        arguments.update(
            {
                "pivot_x": finite_number(pivot_x, "pivot_x", 0.0, 1.0),
                "pivot_y": finite_number(pivot_y, "pivot_y", 0.0, 1.0),
                "pixels_per_unit": finite_number(
                    pixels_per_unit, "pixels_per_unit", 0.01, 10_000.0
                ),
                "skip_empty": skip_empty,
            }
        )
        if package_name is not None:
            arguments["package_name"] = required_text(
                package_name, "package_name", maximum=120
            )
        if action == "export_tileset":
            self._add_tileset_options(
                arguments,
                tile_width=tile_width,
                tile_height=tile_height,
                origin_x=origin_x,
                origin_y=origin_y,
                grid_columns=grid_columns,
                grid_rows=grid_rows,
                spacing_x=spacing_x,
                spacing_y=spacing_y,
                name_prefix=name_prefix,
            )
        else:
            arguments["regions"] = self._normalized_regions(regions)

    def _add_tileset_options(
        self,
        arguments: dict[str, Any],
        *,
        tile_width: int | None,
        tile_height: int | None,
        origin_x: int,
        origin_y: int,
        grid_columns: int | None,
        grid_rows: int | None,
        spacing_x: int,
        spacing_y: int,
        name_prefix: str | None,
    ) -> None:
        if tile_width is None or tile_height is None:
            raise InputError("export_tileset requires tile_width and tile_height")
        dimension_limit = self.config.document_max_dimension
        arguments.update(
            {
                "tile_width": bounded_int(
                    tile_width, "tile_width", 1, dimension_limit
                ),
                "tile_height": bounded_int(
                    tile_height, "tile_height", 1, dimension_limit
                ),
                "origin_x": bounded_int(origin_x, "origin_x", 0, 1_000_000),
                "origin_y": bounded_int(origin_y, "origin_y", 0, 1_000_000),
                "spacing_x": bounded_int(spacing_x, "spacing_x", 0, dimension_limit),
                "spacing_y": bounded_int(spacing_y, "spacing_y", 0, dimension_limit),
            }
        )
        if (grid_columns is None) != (grid_rows is None):
            raise InputError("grid_columns and grid_rows must be supplied together")
        if grid_columns is not None and grid_rows is not None:
            columns = bounded_int(
                grid_columns, "grid_columns", 1, self.config.asset_max_frames
            )
            rows = bounded_int(
                grid_rows, "grid_rows", 1, self.config.asset_max_frames
            )
            if columns * rows > self.config.asset_max_frames:
                raise InputError("tile grid exceeds the configured region limit")
            arguments.update({"grid_columns": columns, "grid_rows": rows})
        if name_prefix is not None:
            arguments["name_prefix"] = required_text(
                name_prefix, "name_prefix", maximum=100
            )

    def _normalized_regions(
        self, regions: list[Mapping[str, Any]] | None
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(regions, list)
            or not 1 <= len(regions) <= self.config.asset_max_frames
        ):
            raise InputError(
                "regions must contain between 1 and "
                f"{self.config.asset_max_frames} entries"
            )
        normalized: list[dict[str, Any]] = []
        normalized_names: set[str] = set()
        for index, region in enumerate(regions):
            if not isinstance(region, Mapping):
                raise InputError(f"regions[{index}] must be an object")
            name = required_text(
                region.get("name"), f"regions[{index}].name", maximum=120
            )
            if name.casefold() in normalized_names:
                raise InputError("region names must be unique ignoring case")
            normalized_names.add(name.casefold())
            normalized.append(
                {
                    "name": name,
                    "x": bounded_int(
                        region.get("x"), f"regions[{index}].x", 0, 1_000_000
                    ),
                    "y": bounded_int(
                        region.get("y"), f"regions[{index}].y", 0, 1_000_000
                    ),
                    "width": bounded_int(
                        region.get("width"),
                        f"regions[{index}].width",
                        1,
                        self.config.document_max_dimension,
                    ),
                    "height": bounded_int(
                        region.get("height"),
                        f"regions[{index}].height",
                        1,
                        self.config.document_max_dimension,
                    ),
                }
            )
        return normalized

    def _extract_asset_preview(
        self, result: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        png_base64 = result.pop("png_base64", None)
        if not isinstance(png_base64, str):
            raise BridgeCallError(
                "INVALID_PREVIEW", "bridge response contains no PNG preview", {}
            )
        try:
            png_size = len(base64.b64decode(png_base64, validate=True))
        except ValueError as exc:
            raise BridgeCallError(
                "INVALID_PREVIEW", "bridge returned malformed PNG preview base64", {}
            ) from exc
        if png_size > self.config.preview_max_bytes:
            raise BridgeCallError(
                "PREVIEW_TOO_LARGE", "preview exceeds configured byte limit", {}
            )
        return result, png_base64
