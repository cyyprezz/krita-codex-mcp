from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._service_helpers import (
    _bounded_float,
    _bounded_int,
    _required_text,
    _safe_call,
)
from .errors import KritaOperationError


class LayerOperationsMixin:
    _SAFE_BLENDING_MODES = {
        "add",
        "color",
        "darken",
        "erase",
        "hard_light",
        "hue",
        "lighten",
        "luminize",
        "multiply",
        "normal",
        "overlay",
        "saturation",
        "screen",
        "soft_light",
        "subtract",
    }

    def layers(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        action = arguments.get("action")
        doc = self._target_document(arguments, activate=action != "read")
        if action == "read":
            return self._layer_result(doc, action)
        if action == "create":
            name = _required_text(arguments.get("name"), "name", 200)
            kind = arguments.get("kind")
            if kind not in {"paintlayer", "grouplayer"}:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "kind must be paintlayer or grouplayer"
                )
            parent_id = arguments.get("parent_id")
            parent = self._find_node(doc, parent_id) if parent_id else doc.rootNode()
            if parent is None:
                raise KritaOperationError("LAYER_NOT_FOUND", "Parent layer UUID was not found")
            parent_is_root = self._same_node(parent, doc.rootNode())
            if not parent_is_root and parent.type().lower() != "grouplayer":
                raise KritaOperationError(
                    "INVALID_PARENT", "New layers require the root or a group layer"
                )
            node = (
                doc.createGroupLayer(name)
                if kind == "grouplayer"
                else doc.createNode(name, "paintlayer")
            )
            if node is None or not parent.addChildNode(node, None):
                raise KritaOperationError("LAYER_CREATE_FAILED", "Krita could not create the layer")
            doc.setActiveNode(node)
            self._finish_mutation(doc)
            return self._layer_result(doc, action, affected=node)

        layer_id = _required_text(arguments.get("layer_id"), "layer_id", 80)
        node = self._find_node(doc, layer_id)
        if node is None or self._same_node(node, doc.rootNode()):
            raise KritaOperationError("LAYER_NOT_FOUND", "Layer UUID was not found")
        node_type = node.type().lower()
        if node_type == "transformmask":
            if action not in {"activate", "delete"}:
                raise KritaOperationError(
                    "UNSAFE_TRANSFORM_MASK_MUTATION",
                    "Transform masks may only be activated or safely removed through the bridge",
                )
            created_ids = self._created_transform_mask_ids.get(self._document_id(doc), set())
            if action == "delete" and layer_id in created_ids:
                raise KritaOperationError(
                    "UNSAFE_TRANSFORM_MASK_MUTATION",
                    "A newly created transform mask cannot be removed in the same open document",
                    {"recovery": "save, close, reopen, and then remove the transform mask"},
                )
        operation_details: dict[str, Any] = {}
        if action == "activate":
            doc.setActiveNode(node)
        elif action == "rename":
            node.setName(_required_text(arguments.get("name"), "name", 200))
        elif action == "duplicate":
            duplicate = node.duplicate()
            if duplicate is None:
                raise KritaOperationError(
                    "LAYER_DUPLICATE_FAILED", "Krita could not duplicate the layer"
                )
            parent = node.parentNode()
            if parent is None or not parent.addChildNode(duplicate, node):
                raise KritaOperationError(
                    "LAYER_DUPLICATE_FAILED", "Krita could not insert the duplicated layer"
                )
            if "name" in arguments:
                duplicate.setName(_required_text(arguments.get("name"), "name", 200))
            doc.setActiveNode(duplicate)
            self._finish_mutation(doc)
            result = self._layer_result(doc, action, affected=duplicate)
            result["source_layer_id"] = self._node_id(node)
            return result
        elif action == "delete":
            parent = node.parentNode()
            siblings = list(parent.childNodes()) if parent is not None else []
            index = next(
                (index for index, sibling in enumerate(siblings) if self._same_node(sibling, node)),
                -1,
            )
            fallback = None
            if index > 0:
                fallback = siblings[index - 1]
            elif 0 <= index < len(siblings) - 1:
                fallback = siblings[index + 1]
            deleted_id = self._node_id(node)
            if not node.remove():
                raise KritaOperationError("LAYER_DELETE_FAILED", "Krita could not delete the layer")
            if fallback is not None:
                doc.setActiveNode(fallback)
            self._finish_mutation(doc)
            result = self._layer_result(doc, action)
            result["deleted_layer_id"] = deleted_id
            return result
        elif action == "reorder":
            parent_id = arguments.get("parent_id")
            parent = self._find_node(doc, parent_id) if parent_id else doc.rootNode()
            if parent is None:
                raise KritaOperationError("LAYER_NOT_FOUND", "Parent layer UUID was not found")
            parent_is_root = self._same_node(parent, doc.rootNode())
            if not parent_is_root and parent.type().lower() != "grouplayer":
                raise KritaOperationError(
                    "INVALID_PARENT", "Layers can only be moved to the root or a group layer"
                )
            if self._same_node(parent, node) or self._is_descendant(parent, node):
                raise KritaOperationError(
                    "INVALID_PARENT", "A layer cannot be moved into itself or its descendant"
                )
            above = None
            above_id = arguments.get("above_id")
            if above_id is not None:
                above = self._find_node(doc, above_id)
                if above is None or self._same_node(above, doc.rootNode()):
                    raise KritaOperationError("LAYER_NOT_FOUND", "Above layer UUID was not found")
                if self._same_node(above, node):
                    raise KritaOperationError(
                        "INVALID_ARGUMENT", "above_id must refer to a different layer"
                    )
                if not self._same_node(above.parentNode(), parent):
                    raise KritaOperationError(
                        "INVALID_ARGUMENT", "above_id must be a direct child of the destination"
                    )
            source_parent = node.parentNode()
            if source_parent is None:
                raise KritaOperationError(
                    "LAYER_REORDER_FAILED", "Layer has no source parent"
                )
            source_siblings = list(source_parent.childNodes())
            source_index = next(
                (
                    index
                    for index, sibling in enumerate(source_siblings)
                    if self._same_node(sibling, node)
                ),
                -1,
            )
            source_anchor = source_siblings[source_index - 1] if source_index > 0 else None
            if source_index < 0 or not source_parent.removeChildNode(node):
                raise KritaOperationError(
                    "LAYER_REORDER_FAILED", "Krita could not detach the layer from its parent"
                )
            if not parent.addChildNode(node, above):
                rolled_back = bool(source_parent.addChildNode(node, source_anchor))
                raise KritaOperationError(
                    "LAYER_REORDER_FAILED",
                    "Krita could not insert the layer at the requested destination",
                    {"rolled_back": rolled_back},
                )
            doc.setActiveNode(node)
        elif action == "translate":
            offset_x = _bounded_int(arguments.get("offset_x"), "offset_x", -1_000_000, 1_000_000)
            offset_y = _bounded_int(arguments.get("offset_y"), "offset_y", -1_000_000, 1_000_000)
            if offset_x == 0 and offset_y == 0:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "translate requires a non-zero offset"
                )
            position = node.position()
            node.move(int(position.x()) + offset_x, int(position.y()) + offset_y)
            doc.setActiveNode(node)
        elif action in {"scale", "rotate", "shear", "crop", "flip"}:
            self._transform_layer(node, doc, action, arguments, operation_details)
        elif action == "set_properties":
            requested: dict[str, Any] = {}
            for field in ("visible", "locked", "alpha_locked", "inherit_alpha"):
                if field in arguments:
                    value = arguments[field]
                    if not isinstance(value, bool):
                        raise KritaOperationError(
                            "INVALID_ARGUMENT", f"{field} must be a boolean"
                        )
                    requested[field] = value
            if "opacity" in arguments:
                requested["opacity"] = round(
                    _bounded_float(arguments["opacity"], "opacity", 0.0, 1.0) * 255
                )
            if "blending_mode" in arguments:
                blending_mode = _required_text(
                    arguments["blending_mode"], "blending_mode", 100
                )
                if blending_mode not in self._SAFE_BLENDING_MODES:
                    raise KritaOperationError(
                        "INVALID_ARGUMENT",
                        "blending_mode is not in the safe supported-mode list",
                        {"supported": sorted(self._SAFE_BLENDING_MODES)},
                    )
                requested["blending_mode"] = blending_mode
            if not requested:
                raise KritaOperationError(
                    "INVALID_ARGUMENT", "set_properties requires at least one layer property"
                )
            layer_type = node.type().lower()
            if "alpha_locked" in requested and layer_type != "paintlayer":
                raise KritaOperationError(
                    "INVALID_LAYER_TYPE", "alpha_locked is only supported for paint layers"
                )
            if "inherit_alpha" in requested and layer_type not in {
                "paintlayer",
                "grouplayer",
            }:
                raise KritaOperationError(
                    "INVALID_LAYER_TYPE",
                    "inherit_alpha is only supported for paint and group layers",
                )
            getters = {
                "visible": node.visible,
                "opacity": node.opacity,
                "locked": node.locked,
                "alpha_locked": node.alphaLocked,
                "inherit_alpha": node.inheritAlpha,
                "blending_mode": node.blendingMode,
            }
            setters = {
                "visible": node.setVisible,
                "opacity": node.setOpacity,
                "locked": node.setLocked,
                "alpha_locked": node.setAlphaLocked,
                "inherit_alpha": node.setInheritAlpha,
                "blending_mode": node.setBlendingMode,
            }
            original_locked = bool(node.locked())
            previous = {field: getters[field]() for field in requested}
            try:
                ordered_fields = [
                    field
                    for field in requested
                    if field != "locked"
                ]
                if requested.get("locked") is False:
                    ordered_fields.insert(0, "locked")
                elif requested.get("locked") is True:
                    ordered_fields.append("locked")
                for field in ordered_fields:
                    value = requested[field]
                    setters[field](value)
                mismatched = {}
                for field, value in requested.items():
                    actual = getters[field]()
                    if actual != value:
                        mismatched[field] = {"requested": value, "actual": actual}
                if mismatched:
                    raise KritaOperationError(
                        "LAYER_PROPERTY_FAILED",
                        "Krita did not apply one or more layer properties",
                        {"mismatched": mismatched},
                    )
            except Exception as exc:
                rolled_back = True
                try:
                    node.setLocked(False)
                except Exception:
                    rolled_back = False
                for field, value in previous.items():
                    if field == "locked":
                        continue
                    try:
                        setters[field](value)
                    except Exception:
                        rolled_back = False
                try:
                    node.setLocked(original_locked)
                except Exception:
                    rolled_back = False
                if isinstance(exc, KritaOperationError):
                    exc.details["rolled_back"] = rolled_back
                    raise
                raise KritaOperationError(
                    "LAYER_PROPERTY_FAILED",
                    f"Krita could not apply the layer properties: {exc}",
                    {"rolled_back": rolled_back},
                ) from exc
        elif action == "merge_down":
            if node.type().lower() != "paintlayer":
                raise KritaOperationError(
                    "INVALID_LAYER_TYPE", "merge_down currently requires a paint layer"
                )
            parent = node.parentNode()
            siblings = list(parent.childNodes()) if parent is not None else []
            source_index = next(
                (index for index, sibling in enumerate(siblings) if self._same_node(sibling, node)),
                -1,
            )
            if source_index <= 0:
                raise KritaOperationError(
                    "NO_LAYER_BELOW", "The selected layer has no sibling layer below it"
                )
            below = siblings[source_index - 1]
            if below.type().lower() != "paintlayer":
                raise KritaOperationError(
                    "INVALID_LAYER_TYPE",
                    "merge_down currently requires the layer below to be a paint layer",
                )
            if not node.visible() or not below.visible():
                raise KritaOperationError(
                    "LAYER_NOT_VISIBLE",
                    "merge_down requires both adjacent paint layers to be visible",
                )
            if node.locked() or below.locked():
                raise KritaOperationError(
                    "LAYER_LOCKED",
                    "merge_down requires both adjacent paint layers to be unlocked",
                )
            source_id = self._node_id(node)
            below_id = self._node_id(below)
            node.mergeDown()
            doc.waitForDone()
            doc.refreshProjection()
            merged_siblings = list(parent.childNodes())
            if len(merged_siblings) != len(siblings) - 1:
                raise KritaOperationError(
                    "LAYER_MERGE_FAILED",
                    "Krita did not produce the expected single merged layer",
                    {
                        "before_count": len(siblings),
                        "after_count": len(merged_siblings),
                    },
                )
            merged = merged_siblings[source_index - 1]
            doc.setActiveNode(merged)
            self._finish_mutation(doc)
            result = self._layer_result(doc, action, affected=merged)
            result["source_layer_id"] = source_id
            result["merged_with_layer_id"] = below_id
            result["metadata_dropped"] = True
            return result
        else:
            raise KritaOperationError("INVALID_ARGUMENT", f"Unsupported layer action: {action}")
        self._finish_mutation(doc)
        result = self._layer_result(doc, str(action), affected=node)
        result.update(operation_details)
        return result

    def _layer_forest(self, doc) -> list[dict[str, Any]]:
        count = [0]
        root = doc.rootNode()
        return [
            self._serialize_node(
                child,
                depth=0,
                count=count,
                parent_id=self._node_id(root),
                stack_index=index,
            )
            for index, child in enumerate(root.childNodes())
        ]

    def _serialize_node(
        self,
        node,
        *,
        depth: int,
        count: list[int],
        parent_id: str,
        stack_index: int,
    ) -> dict[str, Any]:
        if depth > 64:
            raise KritaOperationError("LAYER_TREE_TOO_DEEP", "Layer tree exceeds 64 levels")
        count[0] += 1
        if count[0] > 2000:
            raise KritaOperationError("LAYER_TREE_TOO_LARGE", "Layer tree exceeds 2000 nodes")
        opacity_raw = int(node.opacity())
        position = node.position()
        bounds = node.bounds()
        node_id = self._node_id(node)
        return {
            "id": node_id,
            "parent_id": parent_id,
            "stack_index": stack_index,
            "name": node.name(),
            "type": node.type(),
            "position": {"x": int(position.x()), "y": int(position.y())},
            "bounds": {
                "x": int(bounds.x()),
                "y": int(bounds.y()),
                "width": int(bounds.width()),
                "height": int(bounds.height()),
            },
            "visible": bool(node.visible()),
            "opacity": round(opacity_raw / 255.0, 4),
            "opacity_raw": opacity_raw,
            "locked": bool(_safe_call(node, "locked", default=False)),
            "alpha_locked": bool(_safe_call(node, "alphaLocked", default=False)),
            "blending_mode": _safe_call(node, "blendingMode"),
            "inherit_alpha": bool(_safe_call(node, "inheritAlpha", default=False)),
            "children": [
                self._serialize_node(
                    child,
                    depth=depth + 1,
                    count=count,
                    parent_id=node_id,
                    stack_index=index,
                )
                for index, child in enumerate(node.childNodes())
            ],
        }

    def _layer_result(self, doc, action: str, affected=None) -> dict[str, Any]:
        active = doc.activeNode()
        result = {
            "document_id": self._document_id(doc),
            "document_file_name": doc.fileName(),
            "action": action,
            "active_layer_id": self._node_id(active) if active else None,
            "layers": self._layer_forest(doc),
        }
        if affected is not None:
            result["affected_layer_id"] = self._node_id(affected)
        return result

    def _finish_mutation(self, doc) -> None:
        doc.waitForDone()
        doc.refreshProjection()
        self.bridge_revision += 1
