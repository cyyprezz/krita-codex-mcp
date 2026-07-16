from __future__ import annotations

import math
import xml.etree.ElementTree as ET

from krita import Krita

from ._service_helpers import (
    _bounded_float,
    _bounded_int,
    _required_text,
    _safe_call,
)
from .errors import KritaOperationError


class TransformMaskOperationsMixin:
    _MAX_TRANSFORM_MASKS = 3

    def _ensure_layer_transform_backend(self, requested: str) -> None:
        active = self._layer_transform_backend_session
        if active is not None and active != requested:
            raise KritaOperationError(
                "UNSAFE_TRANSFORM_BACKEND_MIX",
                "Krita 5.3.2.1 cannot safely mix direct layer transforms and transform masks",
                {
                    "active_backend": active,
                    "requested_backend": requested,
                    "recovery": "save all documents and restart Krita before switching backends",
                },
            )

    def _transform_layer_with_mask(self, node, doc, action, arguments, details) -> None:
        document_id = self._document_id(doc)
        creation_count = self._transform_mask_creation_counts.get(document_id, 0)
        existing_masks = [
            candidate
            for candidate in self._walk_nodes(doc.rootNode())
            if candidate.type().lower() == "transformmask"
        ]
        if (
            creation_count >= self._MAX_TRANSFORM_MASKS
            or len(existing_masks) >= self._MAX_TRANSFORM_MASKS
        ):
            raise KritaOperationError(
                "UNSAFE_TRANSFORM_MASK_SEQUENCE",
                "Krita 5.3.2.1 cannot safely add another transform mask to this document",
                {
                    "transform_masks": len(existing_masks),
                    "transform_masks_created_this_session": creation_count,
                    "transform_mask_limit": self._MAX_TRANSFORM_MASKS,
                    "recovery": "keep at most three transform masks in the KRA",
                },
            )
        bounds = node.bounds()
        source_width = int(bounds.width())
        source_height = int(bounds.height())
        if source_width < 1 or source_height < 1:
            raise KritaOperationError(
                "EMPTY_LAYER", "Transform masks require a layer with non-empty content"
            )
        if not callable(getattr(doc, "createTransformMask", None)):
            raise KritaOperationError(
                "UNSUPPORTED_FEATURE",
                "This Krita build does not expose Document.createTransformMask",
                {"krita_version": _safe_call(Krita.instance(), "version")},
            )

        center_x = float(bounds.x()) + source_width / 2.0
        center_y = float(bounds.y()) + source_height / 2.0
        updates: dict[str, float | str] = {
            "original_x": center_x,
            "original_y": center_y,
            "transformed_x": center_x,
            "transformed_y": center_y,
        }
        if action == "scale":
            width = _bounded_int(
                arguments.get("width"), "width", 1, self.config.document_max_dimension
            )
            height = _bounded_int(
                arguments.get("height"), "height", 1, self.config.document_max_dimension
            )
            if width * height > self.config.document_max_pixels:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "Scaled layer exceeds the configured pixel limit"
                )
            strategy = _required_text(arguments.get("strategy"), "strategy", 100)
            strategies = {str(value) for value in Krita.instance().filterStrategies()}
            if strategy not in strategies:
                raise KritaOperationError(
                    "INVALID_ARGUMENT",
                    "Krita does not provide the requested scaling strategy",
                    {"strategy": strategy, "available": sorted(strategies)},
                )
            x = _bounded_int(
                arguments.get("x", int(bounds.x())), "x", -1_000_000, 1_000_000
            )
            y = _bounded_int(
                arguments.get("y", int(bounds.y())), "y", -1_000_000, 1_000_000
            )
            updates.update(
                {
                    "transformed_x": x + width / 2.0,
                    "transformed_y": y + height / 2.0,
                    "scale_x": width / source_width,
                    "scale_y": height / source_height,
                    "filter_id": strategy,
                }
            )
            details.update(
                {
                    "origin": {"x": x, "y": y},
                    "width": width,
                    "height": height,
                    "strategy": strategy,
                }
            )
        elif action == "rotate":
            angle = _bounded_float(
                arguments.get("angle_degrees"), "angle_degrees", -360.0, 360.0
            )
            if angle == 0:
                raise KritaOperationError("INVALID_ARGUMENT", "angle_degrees must be non-zero")
            updates["rotation_delta"] = math.radians(angle)
            details["angle_degrees"] = angle
        else:
            angle_x = _bounded_float(
                arguments.get("shear_x_degrees", 0.0), "shear_x_degrees", -89.0, 89.0
            )
            angle_y = _bounded_float(
                arguments.get("shear_y_degrees", 0.0), "shear_y_degrees", -89.0, 89.0
            )
            if angle_x == 0 and angle_y == 0:
                raise KritaOperationError("INVALID_ARGUMENT", "shear requires a non-zero angle")
            updates["shear_x_delta"] = math.tan(math.radians(angle_x))
            updates["shear_y_delta"] = math.tan(math.radians(angle_y))
            details.update({"shear_x_degrees": angle_x, "shear_y_degrees": angle_y})

        mask = doc.createTransformMask(f"Codex {action.title()} Transform")
        if (
            mask is None
            or not hasattr(mask, "toXML")
            or not hasattr(mask, "fromXML")
            or not hasattr(mask, "finalAffineTransform")
        ):
            raise KritaOperationError(
                "UNSUPPORTED_FEATURE",
                "This Krita build does not expose TransformMask XML editing",
                {"krita_version": _safe_call(Krita.instance(), "version")},
            )
        if not node.addChildNode(mask, None):
            raise KritaOperationError(
                "TRANSFORM_MASK_FAILED", "Krita could not attach the transform mask"
            )
        try:
            template_xml = mask.toXML()
            configured_xml = self._configure_affine_transform_xml(
                template_xml, updates, initialize=True
            )
            if not mask.fromXML(configured_xml):
                raise KritaOperationError(
                    "TRANSFORM_MASK_FAILED", "Krita rejected the affine transform parameters"
                )
            doc.waitForDone()
            doc.refreshProjection()
            matrix = mask.finalAffineTransform()
            self._validate_affine_transform_matrix(matrix)
        except Exception:
            mask.remove()
            raise

        doc.setActiveNode(node)
        creation_count += 1
        self._transform_mask_creation_counts[document_id] = creation_count
        mask_id = self._node_id(mask)
        self._created_transform_mask_ids.setdefault(document_id, set()).add(mask_id)
        details.update(
            {
                "native_transform": True,
                "non_destructive": True,
                "transform_mode": "mask",
                "transform_mask_id": mask_id,
                "transform_mask_name": mask.name(),
                "transform_mask_created": True,
                "transform_mask_count": len(existing_masks) + 1,
                "transform_mask_limit": self._MAX_TRANSFORM_MASKS,
                "affine_matrix": {
                    "m11": float(matrix.m11()),
                    "m12": float(matrix.m12()),
                    "m21": float(matrix.m21()),
                    "m22": float(matrix.m22()),
                    "dx": float(matrix.dx()),
                    "dy": float(matrix.dy()),
                },
                "undo_steps": 0,
                "undo_behavior": (
                    "save, close, reopen, then remove the mask; source pixels stay unchanged"
                ),
            }
        )

    @staticmethod
    def _configure_affine_transform_xml(
        template_xml: str, updates, *, initialize: bool
    ) -> str:
        try:
            root = ET.fromstring(template_xml)
        except (ET.ParseError, TypeError) as exc:
            raise KritaOperationError(
                "TRANSFORM_MASK_FAILED", "Krita returned an invalid transform-mask template"
            ) from exc
        main = root.find("main")
        data = root.find("data")
        free_transform = root.find(".//free_transform")
        if (
            main is None
            or main.get("id") != "tooltransformparams"
            or data is None
            or free_transform is None
        ):
            raise KritaOperationError(
                "TRANSFORM_MASK_FAILED", "Krita returned an unsupported transform-mask template"
            )
        data.set("mode", "0")

        def require(tag: str):
            element = free_transform.find(tag)
            if element is None:
                raise KritaOperationError(
                    "TRANSFORM_MASK_FAILED",
                    f"Krita transform-mask template is missing {tag}",
                )
            return element

        def current_value(tag: str) -> float:
            try:
                value = float(require(tag).get("value", ""))
            except (TypeError, ValueError) as exc:
                raise KritaOperationError(
                    "TRANSFORM_MASK_FAILED",
                    f"Krita transform-mask template has an invalid {tag}",
                ) from exc
            if not math.isfinite(value):
                raise KritaOperationError(
                    "TRANSFORM_MASK_FAILED",
                    f"Krita transform-mask template has a non-finite {tag}",
                )
            return value

        if initialize:
            original = require("originalCenter")
            original.set("x", format(float(updates["original_x"]), ".17g"))
            original.set("y", format(float(updates["original_y"]), ".17g"))
            transformed = require("transformedCenter")
            transformed.set("x", format(float(updates["transformed_x"]), ".17g"))
            transformed.set("y", format(float(updates["transformed_y"]), ".17g"))
            for tag, value in (
                ("aX", 0.0),
                ("aY", 0.0),
                ("aZ", 0.0),
                ("scaleX", 1.0),
                ("scaleY", 1.0),
                ("shearX", 0.0),
                ("shearY", 0.0),
            ):
                require(tag).set("value", format(value, ".17g"))

        if "transformed_x" in updates and "scale_x" in updates:
            transformed = require("transformedCenter")
            transformed.set("x", format(float(updates["transformed_x"]), ".17g"))
            transformed.set("y", format(float(updates["transformed_y"]), ".17g"))
            require("scaleX").set("value", format(float(updates["scale_x"]), ".17g"))
            require("scaleY").set("value", format(float(updates["scale_y"]), ".17g"))
            require("filterId").set("value", str(updates["filter_id"]))
        if "rotation_delta" in updates:
            rotation = current_value("aZ") + float(updates["rotation_delta"])
            if abs(rotation) > math.tau * 100:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "Accumulated rotation exceeds the safe limit"
                )
            require("aZ").set("value", format(rotation, ".17g"))
        for tag, key in (
            ("shearX", "shear_x_delta"),
            ("shearY", "shear_y_delta"),
        ):
            if key not in updates:
                continue
            shear = current_value(tag) + float(updates[key])
            if abs(shear) > 64:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "Accumulated shear exceeds the safe limit"
                )
            require(tag).set("value", format(shear, ".17g"))
        return "<!DOCTYPE transform_params>" + ET.tostring(root, encoding="unicode")

    @staticmethod
    def _validate_affine_transform_matrix(matrix) -> None:
        values = (
            float(matrix.m11()),
            float(matrix.m12()),
            float(matrix.m21()),
            float(matrix.m22()),
            float(matrix.dx()),
            float(matrix.dy()),
            float(matrix.determinant()),
        )
        if any(not math.isfinite(value) or abs(value) > 1_000_000_000 for value in values):
            raise KritaOperationError(
                "TRANSFORM_MASK_FAILED", "Krita produced an invalid affine transform matrix"
            )
        if abs(values[-1]) < 1e-9:
            raise KritaOperationError(
                "TRANSFORM_MASK_FAILED", "Krita produced a singular affine transform matrix"
            )
