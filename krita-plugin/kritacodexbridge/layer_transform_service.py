from __future__ import annotations

import math

from krita import Krita
from PyQt5.QtCore import QPointF

from ._service_helpers import (
    _bounded_float,
    _bounded_int,
    _required_text,
    _safe_call,
)
from .errors import KritaOperationError


class LayerTransformOperationsMixin:
    def _transform_layer(self, node, doc, action, arguments, details) -> None:
        if node.type().lower() not in {"paintlayer", "grouplayer"}:
            raise KritaOperationError(
                "INVALID_LAYER_TYPE", "Native transforms require a paint or group layer"
            )
        if bool(_safe_call(node, "locked", default=False)):
            raise KritaOperationError("LAYER_LOCKED", "Target layer is locked")
        document_id = self._document_id(doc)
        previous_action = self._destructive_transform_documents.get(document_id)
        if action != "flip" and previous_action is not None:
            raise KritaOperationError(
                "UNSAFE_TRANSFORM_SEQUENCE",
                "Krita 5.3.2.1 cannot safely apply a second destructive layer transform "
                "before the document is closed and reopened",
                {
                    "previous_action": previous_action,
                    "requested_action": action,
                    "recovery": "save, close, and reopen the KRA before transforming again",
                },
            )
        method_name = {
            "scale": "scaleNode",
            "rotate": "rotateNode",
            "shear": "shearNode",
            "crop": "cropNode",
            "flip": None,
        }[action]
        if method_name is not None and not hasattr(node, method_name):
            raise KritaOperationError(
                "UNSUPPORTED_FEATURE",
                f"This Krita build does not expose Node.{method_name}",
                {"krita_version": _safe_call(Krita.instance(), "version")},
            )
        bounds = node.bounds()
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
            backup = self._create_transform_backup(node, action)
            try:
                node.scaleNode(QPointF(float(x), float(y)), width, height, strategy)
            except Exception:
                backup.remove()
                raise
            self._record_transform_backup(details, backup)
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
            backup = self._create_transform_backup(node, action)
            try:
                node.rotateNode(math.radians(angle))
            except Exception:
                backup.remove()
                raise
            self._record_transform_backup(details, backup)
            details["angle_degrees"] = angle
        elif action == "shear":
            angle_x = _bounded_float(
                arguments.get("shear_x_degrees", 0.0), "shear_x_degrees", -89.0, 89.0
            )
            angle_y = _bounded_float(
                arguments.get("shear_y_degrees", 0.0), "shear_y_degrees", -89.0, 89.0
            )
            if angle_x == 0 and angle_y == 0:
                raise KritaOperationError("INVALID_ARGUMENT", "shear requires a non-zero angle")
            backup = self._create_transform_backup(node, action)
            try:
                node.shearNode(angle_x, angle_y)
            except Exception:
                backup.remove()
                raise
            self._record_transform_backup(details, backup)
            details.update({"shear_x_degrees": angle_x, "shear_y_degrees": angle_y})
        elif action == "crop":
            x = _bounded_int(arguments.get("x"), "x", -1_000_000, 1_000_000)
            y = _bounded_int(arguments.get("y"), "y", -1_000_000, 1_000_000)
            width = _bounded_int(
                arguments.get("width"), "width", 1, self.config.document_max_dimension
            )
            height = _bounded_int(
                arguments.get("height"), "height", 1, self.config.document_max_dimension
            )
            if width * height > self.config.document_max_pixels:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "Cropped layer exceeds the configured pixel limit"
                )
            backup = self._create_transform_backup(node, action)
            try:
                node.cropNode(x, y, width, height)
            except Exception:
                backup.remove()
                raise
            self._record_transform_backup(details, backup)
            details["rect"] = {"x": x, "y": y, "width": width, "height": height}
        else:
            direction = arguments.get("direction")
            action_id = {
                "horizontal": "mirrorNodeX",
                "vertical": "mirrorNodeY",
            }.get(direction)
            if action_id is None:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "direction must be horizontal or vertical"
                )
            doc.setActiveNode(node)
            self._trigger_action(action_id, doc, scope="layer")
            details.update(
                {
                    "direction": direction,
                    "native_action": action_id,
                    "undo_steps": 1,
                    "undo_behavior": "one native Krita mirror undo step",
                }
            )
        doc.setActiveNode(node)
        details["native_transform"] = True
        if action != "flip":
            self._destructive_transform_documents[document_id] = action
            details.update(
                {
                    "undo_steps": 0,
                    "undo_behavior": "restore from the hidden backup layer",
                }
            )

    def _create_transform_backup(self, node, action: str):
        backup = node.duplicate()
        parent = node.parentNode()
        if backup is None or parent is None or not parent.addChildNode(backup, node):
            raise KritaOperationError(
                "TRANSFORM_BACKUP_FAILED",
                "Krita could not create the required transform backup layer",
            )
        backup.setName(f"{node.name()} [before {action}]")
        backup.setVisible(False)
        return backup

    def _record_transform_backup(self, details, backup) -> None:
        details.update(
            {
                "backup_layer_id": self._node_id(backup),
                "backup_layer_name": backup.name(),
                "backup_visible": False,
            }
        )
