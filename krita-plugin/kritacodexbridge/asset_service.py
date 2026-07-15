from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from contextlib import suppress
from typing import Any
from uuid import uuid4

from PyQt5.QtCore import QRect, Qt

from krita_codex_protocol import PathPolicyError

from ._service_helpers import _bounded_int, _fit_size, _required_text
from .errors import KritaOperationError


class AssetOperationsMixin:
    def asset(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        action = arguments.get("action")
        if action not in {
            "export_layer",
            "export_animation",
            "export_tileset",
            "export_regions",
        }:
            raise KritaOperationError(
                "INVALID_ARGUMENT",
                "action must be export_layer, export_animation, export_tileset, or export_regions",
            )
        doc = self._target_document(arguments)
        layer_id = _required_text(arguments.get("layer_id"), "layer_id", 80)
        node = self._find_node(doc, layer_id)
        if node is None or self._same_node(node, doc.rootNode()):
            raise KritaOperationError("LAYER_NOT_FOUND", "Layer UUID was not found")
        overwrite = arguments.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise KritaOperationError("INVALID_ARGUMENT", "overwrite must be a boolean")
        preview_max_width = _bounded_int(
            arguments.get("preview_max_width", 1024),
            "preview_max_width",
            64,
            self.config.preview_max_dimension,
        )
        preview_max_height = _bounded_int(
            arguments.get("preview_max_height", 1024),
            "preview_max_height",
            64,
            self.config.preview_max_dimension,
        )
        if action == "export_animation":
            return self._export_animation_asset(
                doc,
                node,
                arguments,
                overwrite=overwrite,
                preview_max_width=preview_max_width,
                preview_max_height=preview_max_height,
            )
        if action in {"export_tileset", "export_regions"}:
            return self._export_region_asset(
                doc,
                node,
                arguments,
                action=action,
                overwrite=overwrite,
                preview_max_width=preview_max_width,
                preview_max_height=preview_max_height,
            )
        return self._export_layer_asset(
            doc,
            node,
            arguments,
            overwrite=overwrite,
            preview_max_width=preview_max_width,
            preview_max_height=preview_max_height,
        )

    def _export_layer_asset(
        self,
        doc,
        node,
        arguments: Mapping[str, Any],
        *,
        overwrite: bool,
        preview_max_width: int,
        preview_max_height: int,
    ) -> dict[str, Any]:
        crop = arguments.get("crop", "canvas")
        if crop not in {"canvas", "content"}:
            raise KritaOperationError(
                "INVALID_ARGUMENT", "export_layer crop must be canvas or content"
            )
        try:
            png_path = self.path_policy.validate_output(
                arguments.get("output_path"), extensions={".png"}, overwrite=overwrite
            )
        except PathPolicyError as exc:
            raise KritaOperationError("PATH_NOT_ALLOWED", str(exc)) from exc

        canvas_rect = QRect(0, 0, int(doc.width()), int(doc.height()))
        export_rect = canvas_rect if crop == "canvas" else node.bounds().intersected(canvas_rect)
        if export_rect.isEmpty():
            raise KritaOperationError(
                "EMPTY_LAYER_BOUNDS", "Layer has no content inside the document canvas"
            )
        node_path = self._node_index_path(doc, node)
        temp_path = png_path.with_name(f".{png_path.stem}.{uuid4().hex}.tmp.png")
        clone = None
        try:
            clone = doc.clone()
            if clone is None:
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Krita could not clone the source document"
                )
            clone.waitForDone()
            image = self._isolated_projection(clone, node_path, export_rect)
            if not image.save(str(temp_path), "PNG"):
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Krita could not encode the isolated layer PNG"
                )
            if not temp_path.is_file():
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Krita did not create the layer PNG"
                )
            output_bytes = temp_path.stat().st_size
            if output_bytes <= 0:
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Krita created an empty layer PNG"
                )
            if output_bytes > self.config.asset_max_bytes:
                raise KritaOperationError(
                    "ASSET_TOO_LARGE",
                    "Layer PNG exceeds the configured byte limit",
                    {"bytes": output_bytes, "limit": self.config.asset_max_bytes},
                )
            preview_width, preview_height = _fit_size(
                int(image.width()),
                int(image.height()),
                preview_max_width,
                preview_max_height,
            )
            preview_image = image.scaled(
                preview_width,
                preview_height,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            preview_png = self._encode_png_preview(preview_image)
            if overwrite:
                temp_path.replace(png_path)
            else:
                try:
                    os.link(temp_path, png_path)
                except FileExistsError as exc:
                    raise KritaOperationError(
                        "OUTPUT_EXISTS",
                        "Output file appeared after validation; overwrite remains disabled",
                        {"path": str(png_path)},
                    ) from exc
                temp_path.unlink()
        except KritaOperationError:
            raise
        except Exception as exc:
            raise KritaOperationError(
                "ASSET_EXPORT_FAILED",
                "Krita could not export the layer PNG",
                {"reason": str(exc)},
            ) from exc
        finally:
            if clone is not None:
                with suppress(Exception):
                    clone.setModified(False)
                    clone.close()
            if temp_path.exists():
                with suppress(OSError):
                    temp_path.unlink()

        self.bridge_revision += 1
        return {
            "action": "export_layer",
            "document_id": self._document_id(doc),
            "document_file_name": doc.fileName(),
            "layer_id": self._node_id(node),
            "layer_name": node.name(),
            "layer_type": node.type(),
            "crop": crop,
            "export_rect": {
                "x": int(export_rect.x()),
                "y": int(export_rect.y()),
                "width": int(export_rect.width()),
                "height": int(export_rect.height()),
            },
            "output": {"kind": "png", "path": str(png_path), "bytes": output_bytes},
            "preview_width": int(preview_image.width()),
            "preview_height": int(preview_image.height()),
            "png_base64": base64.b64encode(preview_png).decode("ascii"),
        }

    def _isolated_projection(self, clone, node_path: list[int], export_rect: QRect):
        clone_root = clone.rootNode()
        clone_target = self._node_at_index_path(clone_root, node_path)
        current = clone_root
        for child_index in node_path:
            children = list(current.childNodes())
            for index, child in enumerate(children):
                child.setVisible(index == child_index)
            current = children[child_index]
        clone_target.setVisible(True)
        clone.refreshProjection()
        clone.waitForDone()
        image = clone.projection(
            int(export_rect.x()),
            int(export_rect.y()),
            int(export_rect.width()),
            int(export_rect.height()),
        )
        if image is None or image.isNull():
            raise KritaOperationError(
                "ASSET_EXPORT_FAILED", "Krita returned an empty isolated layer projection"
            )
        return image
