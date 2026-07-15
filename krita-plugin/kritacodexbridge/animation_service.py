from __future__ import annotations

import base64
import json
import math
import re
from collections.abc import Mapping
from contextlib import suppress
from typing import Any
from uuid import uuid4

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QImage, QPainter

from krita_codex_protocol import PathPolicyError

from ._service_helpers import (
    _bounded_float,
    _bounded_int,
    _file_sha256,
    _fit_size,
    _required_text,
)
from .errors import KritaOperationError


class AnimationOperationsMixin:
    def _export_animation_asset(
        self,
        doc,
        group,
        arguments: Mapping[str, Any],
        *,
        overwrite: bool,
        preview_max_width: int,
        preview_max_height: int,
    ) -> dict[str, Any]:
        if group.type() != "grouplayer":
            raise KritaOperationError(
                "INVALID_FRAME_GROUP", "export_animation requires a group layer"
            )
        crop = arguments.get("crop", "canvas")
        if crop not in {"canvas", "union"}:
            raise KritaOperationError(
                "INVALID_ARGUMENT", "export_animation crop must be canvas or union"
            )
        try:
            output_dir = self.path_policy.validate_output_directory(
                arguments.get("output_path"), overwrite=overwrite
            )
        except PathPolicyError as exc:
            raise KritaOperationError("PATH_NOT_ALLOWED", str(exc)) from exc

        frames = list(group.childNodes())
        source_stack_indices = {
            self._node_id(frame): index for index, frame in enumerate(frames)
        }
        requested_ids = arguments.get("frame_ids")
        if requested_ids is not None:
            if (
                not isinstance(requested_ids, list)
                or not 1 <= len(requested_ids) <= self.config.asset_max_frames
            ):
                raise KritaOperationError(
                    "INVALID_ARGUMENT",
                    "frame_ids must be a non-empty list within the configured frame limit",
                )
            normalized_ids = [
                _required_text(value, f"frame_ids[{index}]", 80)
                for index, value in enumerate(requested_ids)
            ]
            normalized_keys = {
                value.strip().strip("{}").lower() for value in normalized_ids
            }
            if len(normalized_keys) != len(normalized_ids):
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "frame_ids must not contain duplicates"
                )
            direct_children = {self._node_id(frame): frame for frame in frames}
            try:
                frames = [
                    direct_children[frame_id.strip().strip("{}").lower()]
                    for frame_id in normalized_ids
                ]
            except KeyError as exc:
                raise KritaOperationError(
                    "FRAME_NOT_FOUND", "Every frame_id must identify a direct child of the group"
                ) from exc
        if not frames:
            raise KritaOperationError("EMPTY_FRAME_GROUP", "Frame group has no direct child layers")
        unsupported_frames = [
            {"layer_id": self._node_id(frame), "name": frame.name(), "type": frame.type()}
            for frame in frames
            if frame.type() not in {"paintlayer", "grouplayer"}
        ]
        if unsupported_frames:
            raise KritaOperationError(
                "INVALID_FRAME_LAYER",
                "Animation frames must be paint layers or group layers",
                {"layers": unsupported_frames},
            )
        if len(frames) > self.config.asset_max_frames:
            raise KritaOperationError(
                "TOO_MANY_FRAMES",
                "Frame group exceeds the configured frame limit",
                {"frames": len(frames), "limit": self.config.asset_max_frames},
            )

        columns = min(
            len(frames), _bounded_int(arguments.get("columns", 8), "columns", 1, 64)
        )
        padding = _bounded_int(arguments.get("padding", 0), "padding", 0, 64)
        frame_duration_ms = _bounded_int(
            arguments.get("frame_duration_ms", 100), "frame_duration_ms", 1, 60_000
        )
        pivot_x = _bounded_float(arguments.get("pivot_x", 0.5), "pivot_x", 0.0, 1.0)
        pivot_y = _bounded_float(arguments.get("pivot_y", 0.5), "pivot_y", 0.0, 1.0)
        pixels_per_unit = _bounded_float(
            arguments.get("pixels_per_unit", 100.0),
            "pixels_per_unit",
            0.01,
            10_000.0,
        )
        loop = arguments.get("loop", True)
        if not isinstance(loop, bool):
            raise KritaOperationError("INVALID_ARGUMENT", "loop must be a boolean")
        raw_animation_name = arguments.get("animation_name")
        animation_name = (
            _required_text(raw_animation_name, "animation_name", 120)
            if raw_animation_name is not None
            else output_dir.name
        )
        canvas_rect = QRect(0, 0, int(doc.width()), int(doc.height()))
        if crop == "canvas":
            export_rect = canvas_rect
            render_pixels = len(frames) * int(doc.width()) * int(doc.height())
        else:
            first_pass_pixels = len(frames) * int(doc.width()) * int(doc.height())
            if first_pass_pixels > self.config.asset_render_max_pixels:
                raise KritaOperationError(
                    "ANIMATION_RENDER_TOO_LARGE",
                    "Union-crop alpha analysis exceeds the configured render-pixel limit",
                    {
                        "pixels": first_pass_pixels,
                        "limit": self.config.asset_render_max_pixels,
                    },
                )
            export_rect = self._rendered_union_rect(doc, frames, canvas_rect)
            render_pixels = first_pass_pixels + len(frames) * int(export_rect.width()) * int(
                export_rect.height()
            )
        if export_rect.isEmpty():
            raise KritaOperationError(
                "EMPTY_FRAME_BOUNDS", "Animation frames have no content inside the canvas"
            )

        cell_width = int(export_rect.width())
        cell_height = int(export_rect.height())
        rows = math.ceil(len(frames) / columns)
        sheet_width = columns * cell_width + (columns - 1) * padding
        sheet_height = rows * cell_height + (rows - 1) * padding
        sheet_pixels = sheet_width * sheet_height
        if (
            sheet_width > self.config.document_max_dimension
            or sheet_height > self.config.document_max_dimension
            or sheet_pixels > self.config.document_max_pixels
        ):
            raise KritaOperationError(
                "SPRITESHEET_TOO_LARGE",
                "Spritesheet exceeds the configured dimension or pixel limit",
                {
                    "width": sheet_width,
                    "height": sheet_height,
                    "pixels": sheet_pixels,
                    "dimension_limit": self.config.document_max_dimension,
                    "pixel_limit": self.config.document_max_pixels,
                },
            )
        if render_pixels + sheet_pixels > self.config.asset_render_max_pixels:
            raise KritaOperationError(
                "ANIMATION_RENDER_TOO_LARGE",
                "Animation export exceeds the configured total render-pixel limit",
                {
                    "pixels": render_pixels + sheet_pixels,
                    "limit": self.config.asset_render_max_pixels,
                },
            )

        source_pivot = {"x": pivot_x, "y": pivot_y, "space": "canvas_bottom_left_normalized"}
        pivot_canvas_x = pivot_x * int(doc.width())
        pivot_canvas_y_top = (1.0 - pivot_y) * int(doc.height())
        cell_pivot_x = (pivot_canvas_x - int(export_rect.x())) / cell_width
        cell_pivot_y = 1.0 - ((pivot_canvas_y_top - int(export_rect.y())) / cell_height)
        cell_pivot = {
            "x": cell_pivot_x,
            "y": cell_pivot_y,
            "space": "cell_bottom_left_normalized",
        }
        warnings: list[str] = []
        if not 0.0 <= cell_pivot_x <= 1.0 or not 0.0 <= cell_pivot_y <= 1.0:
            warnings.append("The source-canvas pivot lies outside the cropped animation cell")

        stage_dir = output_dir.with_name(f".{output_dir.name}.{uuid4().hex}.tmp")
        stage_frames_dir = stage_dir / "frames"
        stage_sheet_path = stage_dir / "spritesheet.png"
        stage_manifest_path = stage_dir / "manifest.json"
        final_sheet_path = output_dir / stage_sheet_path.name
        final_manifest_path = output_dir / stage_manifest_path.name
        clone = None
        painter = None
        total_bytes = 0
        manifest_frames: list[dict[str, Any]] = []
        try:
            stage_frames_dir.mkdir(parents=True)
            clone = doc.clone()
            if clone is None:
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Krita could not clone the source document"
                )
            clone.waitForDone()
            sheet = QImage(sheet_width, sheet_height, QImage.Format_ARGB32)
            if sheet.isNull():
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Krita could not allocate the spritesheet image"
                )
            sheet.fill(Qt.transparent)
            painter = QPainter(sheet)
            if not painter.isActive():
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Krita could not initialize spritesheet painting"
                )
            for index, frame in enumerate(frames):
                frame_id = self._node_id(frame)
                frame_image = self._isolated_projection(
                    clone, self._node_index_path(doc, frame), export_rect
                )
                frame_filename = f"{index:04d}.png"
                frame_path = stage_frames_dir / frame_filename
                if not frame_image.save(str(frame_path), "PNG"):
                    raise KritaOperationError(
                        "ASSET_EXPORT_FAILED", "Krita could not encode an animation frame"
                    )
                frame_bytes = frame_path.stat().st_size
                if frame_bytes <= 0 or frame_bytes > self.config.asset_max_bytes:
                    raise KritaOperationError(
                        "ASSET_TOO_LARGE",
                        "Animation frame exceeds the configured byte limit",
                        {
                            "frame_index": index,
                            "bytes": frame_bytes,
                            "limit": self.config.asset_max_bytes,
                        },
                    )
                total_bytes += frame_bytes
                if total_bytes > self.config.asset_batch_max_bytes:
                    raise KritaOperationError(
                        "ASSET_BATCH_TOO_LARGE",
                        "Animation frames exceed the configured total byte limit",
                        {"bytes": total_bytes, "limit": self.config.asset_batch_max_bytes},
                    )
                alpha_bounds = self._image_alpha_bounds(frame_image)
                x = (index % columns) * (cell_width + padding)
                y = (index // columns) * (cell_height + padding)
                painter.drawImage(x, y, frame_image)
                manifest_frames.append(
                    {
                        "index": index,
                        "name": frame.name(),
                        "layer_id": frame_id,
                        "source_stack_index": source_stack_indices[frame_id],
                        "file": f"frames/{frame_filename}",
                        "duration_ms": frame_duration_ms,
                        "empty": alpha_bounds.isEmpty(),
                        "sha256": _file_sha256(frame_path),
                        "sheet_rect_png_top_left": {
                            "x": x,
                            "y": y,
                            "width": cell_width,
                            "height": cell_height,
                        },
                        "sprite_rect_unity_bottom_left": {
                            "x": x,
                            "y": sheet_height - y - cell_height,
                            "width": cell_width,
                            "height": cell_height,
                        },
                        "pivot": {
                            "x": cell_pivot_x,
                            "y": cell_pivot_y,
                            "space": "cell_bottom_left_normalized",
                        },
                    }
                )
            painter.end()
            painter = None
            if not sheet.save(str(stage_sheet_path), "PNG"):
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Krita could not encode the spritesheet PNG"
                )
            sheet_bytes = stage_sheet_path.stat().st_size
            if sheet_bytes <= 0 or sheet_bytes > self.config.asset_max_bytes:
                raise KritaOperationError(
                    "ASSET_TOO_LARGE",
                    "Spritesheet exceeds the configured byte limit",
                    {"bytes": sheet_bytes, "limit": self.config.asset_max_bytes},
                )
            total_bytes += sheet_bytes
            if total_bytes > self.config.asset_batch_max_bytes:
                raise KritaOperationError(
                    "ASSET_BATCH_TOO_LARGE",
                    "Animation frames and sheet exceed the configured total byte limit",
                    {"bytes": total_bytes, "limit": self.config.asset_batch_max_bytes},
                )
            sheet_sha256 = _file_sha256(stage_sheet_path)
            manifest = {
                "schema": "dev.desens.krita-codex.unity-animation",
                "schema_version": 1,
                "name": animation_name,
                "source": {
                    "document_id": self._document_id(doc),
                    "document_name": doc.name(),
                    "document_file_name": doc.fileName(),
                    "group_layer_id": self._node_id(group),
                    "group_layer_name": group.name(),
                    "frame_order": "explicit" if requested_ids is not None else "bottom_to_top",
                    "document_id_scope": "krita_session",
                },
                "canvas": {"width": int(doc.width()), "height": int(doc.height())},
                "crop": crop,
                "source_rect": {
                    "x": int(export_rect.x()),
                    "y": int(export_rect.y()),
                    "width": cell_width,
                    "height": cell_height,
                },
                "unity": {
                    "pixels_per_unit": pixels_per_unit,
                    "pivot_source": source_pivot,
                    "pivot_cell": cell_pivot,
                    "filter_mode": "Point",
                    "texture_type": "Sprite",
                    "sprite_mode": "Multiple",
                    "wrap_mode": "Clamp",
                    "mipmaps": False,
                    "alpha_is_transparency": True,
                    "mesh_type": "FullRect",
                },
                "animation": {
                    "frame_count": len(frames),
                    "default_frame_duration_ms": frame_duration_ms,
                    "fps": 1000.0 / frame_duration_ms,
                    "length_ms": len(frames) * frame_duration_ms,
                    "loop": loop,
                },
                "sheet": {
                    "file": stage_sheet_path.name,
                    "width": sheet_width,
                    "height": sheet_height,
                    "columns": columns,
                    "rows": rows,
                    "cell_width": cell_width,
                    "cell_height": cell_height,
                    "padding": padding,
                    "png_coordinate_origin": "top_left",
                    "unity_coordinate_origin": "bottom_left",
                    "sha256": sheet_sha256,
                },
                "frames": manifest_frames,
                "warnings": warnings,
            }
            manifest_payload = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
            total_bytes += len(manifest_payload)
            if total_bytes > self.config.asset_batch_max_bytes:
                raise KritaOperationError(
                    "ASSET_BATCH_TOO_LARGE",
                    "Animation package exceeds the configured total byte limit",
                    {"bytes": total_bytes, "limit": self.config.asset_batch_max_bytes},
                )
            stage_manifest_path.write_bytes(manifest_payload)
            preview_width, preview_height = _fit_size(
                sheet_width, sheet_height, preview_max_width, preview_max_height
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
                schema="dev.desens.krita-codex.unity-animation",
                item_directory="frames",
                package_label="animation",
            )
        except KritaOperationError:
            raise
        except Exception as exc:
            raise KritaOperationError(
                "ASSET_EXPORT_FAILED",
                "Krita could not export the animation package",
                {"reason": str(exc)},
            ) from exc
        finally:
            if painter is not None and painter.isActive():
                painter.end()
            if clone is not None:
                with suppress(Exception):
                    clone.setModified(False)
                    clone.close()
            self._remove_asset_path(stage_dir)

        self.bridge_revision += 1
        return {
            "action": "export_animation",
            "document_id": self._document_id(doc),
            "document_file_name": doc.fileName(),
            "group_layer_id": self._node_id(group),
            "group_layer_name": group.name(),
            "crop": crop,
            "frame_count": len(frames),
            "frame_order": "explicit" if requested_ids is not None else "bottom_to_top",
            "sheet": {
                "path": str(final_sheet_path),
                "width": sheet_width,
                "height": sheet_height,
                "columns": columns,
                "rows": rows,
                "cell_width": cell_width,
                "cell_height": cell_height,
                "padding": padding,
            },
            "output": {
                "kind": "animation_package",
                "path": str(output_dir),
                "manifest_path": str(final_manifest_path),
                "frames_path": str(output_dir / "frames"),
                "bytes": total_bytes,
            },
            "frames": manifest_frames,
            "warnings": warnings,
            "render_pixels": render_pixels + sheet_pixels,
            "preview_width": int(preview_image.width()),
            "preview_height": int(preview_image.height()),
            "png_base64": base64.b64encode(preview_png).decode("ascii"),
        }

    def _rendered_union_rect(self, doc, frames: list[Any], canvas_rect: QRect) -> QRect:
        clone = None
        union_rect = QRect()
        try:
            clone = doc.clone()
            if clone is None:
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Krita could not clone the source document"
                )
            clone.waitForDone()
            for frame in frames:
                image = self._isolated_projection(
                    clone, self._node_index_path(doc, frame), canvas_rect
                )
                alpha_rect = self._image_alpha_bounds(image)
                if alpha_rect.isEmpty():
                    continue
                alpha_rect.translate(int(canvas_rect.x()), int(canvas_rect.y()))
                union_rect = (
                    alpha_rect if union_rect.isEmpty() else union_rect.united(alpha_rect)
                )
            return union_rect
        finally:
            if clone is not None:
                with suppress(Exception):
                    clone.setModified(False)
                    clone.close()

    def _image_alpha_bounds(self, image) -> QRect:
        rgba = image.convertToFormat(QImage.Format_RGBA8888)
        width = int(rgba.width())
        height = int(rgba.height())
        pointer = rgba.constBits()
        pointer.setsize(int(rgba.byteCount()))
        payload = bytes(pointer)
        bytes_per_line = int(rgba.bytesPerLine())
        first_nonzero = re.compile(rb"[^\x00]")
        min_x = width
        min_y = height
        max_x = -1
        max_y = -1
        for y in range(height):
            row_start = y * bytes_per_line
            alpha = payload[row_start + 3 : row_start + width * 4 : 4]
            first = first_nonzero.search(alpha)
            if first is None:
                continue
            last = first_nonzero.search(alpha[::-1])
            min_x = min(min_x, first.start())
            max_x = max(max_x, width - 1 - last.start())
            min_y = min(min_y, y)
            max_y = max(max_y, y)
        if max_x < min_x or max_y < min_y:
            return QRect()
        return QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)
