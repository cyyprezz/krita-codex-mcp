from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from krita import Krita, ManagedColor, Selection

from ._service_helpers import (
    _bounded_int,
    _parse_qcolor,
    _required_text,
    _safe_call,
)
from .errors import KritaOperationError


class EditingOperationsMixin:
    def edit(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        action = arguments.get("action")
        supported = {
            "selection_status",
            "select_rect",
            "select_all",
            "select_opaque",
            "deselect",
            "invert",
            "grow",
            "shrink",
            "fill",
            "clear",
        }
        if action not in supported:
            raise KritaOperationError("INVALID_ARGUMENT", f"Unsupported edit action: {action}")
        doc = self._target_document(
            arguments, activate=action not in {"selection_status"}
        )
        if action == "selection_status":
            return self._selection_result(doc, action, changed=False)
        if action in {"select_rect", "select_all"}:
            selection = Selection(doc)
            if action == "select_rect":
                x = _bounded_int(
                    arguments.get("x"), "x", -1_000_000, 1_000_000
                )
                y = _bounded_int(
                    arguments.get("y"), "y", -1_000_000, 1_000_000
                )
                width = _bounded_int(
                    arguments.get("width"),
                    "width",
                    1,
                    self.config.document_max_dimension,
                )
                height = _bounded_int(
                    arguments.get("height"),
                    "height",
                    1,
                    self.config.document_max_dimension,
                )
                selection.select(x, y, width, height, 255)
            else:
                selection.select(0, 0, int(doc.width()), int(doc.height()), 255)
            combine = self._selection_combine(arguments)
            self._apply_selection(doc, selection, combine)
            self.bridge_revision += 1
            return self._selection_result(doc, action, changed=True, combine=combine)
        if action == "select_opaque":
            layer_id = _required_text(arguments.get("layer_id"), "layer_id", 80)
            node = self._paint_node(doc, layer_id, allow_locked=True)
            combine = self._selection_combine(arguments)
            action_id = {
                "replace": "selectopaque",
                "add": "selectopaque_add",
                "subtract": "selectopaque_subtract",
                "intersect": "selectopaque_intersect",
            }[combine]
            previous = doc.activeNode()
            doc.setActiveNode(node)
            try:
                self._trigger_edit_action(action_id, doc)
            finally:
                if previous is not None:
                    doc.setActiveNode(previous)
            self.bridge_revision += 1
            return self._selection_result(doc, action, changed=True, combine=combine)
        selection = doc.selection()
        if action == "deselect":
            changed = selection is not None and selection.width() > 0 and selection.height() > 0
            doc.setSelection(None)
            if changed:
                self.bridge_revision += 1
            return self._selection_result(doc, action, changed=changed)
        if selection is None or selection.width() <= 0 or selection.height() <= 0:
            raise KritaOperationError(
                "NO_SELECTION", "This edit action requires a non-empty selection"
            )
        if action == "invert":
            selection.invert()
            self.bridge_revision += 1
            return self._selection_result(doc, action, changed=True)
        if action in {"grow", "shrink"}:
            amount = _bounded_int(arguments.get("amount"), "amount", 1, 4096)
            if action == "grow":
                selection.grow(amount, amount)
            else:
                selection.shrink(amount, amount, False)
            self.bridge_revision += 1
            return self._selection_result(
                doc, action, changed=True, amount=amount
            )
        layer_id = _required_text(arguments.get("layer_id"), "layer_id", 80)
        node = self._paint_node(doc, layer_id, allow_locked=False)
        previous_node = doc.activeNode()
        doc.setActiveNode(node)
        previous_color = None
        view = None
        try:
            if action == "fill" and "color" in arguments:
                view = self._active_view()
                previous_color = view.foregroundColor()
                qcolor = _parse_qcolor(arguments["color"])
                managed = ManagedColor.fromQColor(qcolor, view.canvas())
                if managed is None:
                    raise KritaOperationError(
                        "COLOR_CONVERSION_FAILED",
                        "Krita could not convert the requested fill color",
                    )
                view.setForeGroundColor(managed)
            action_id = "fill_selection_foreground_color" if action == "fill" else "clear"
            self._trigger_edit_action(action_id, doc)
            doc.refreshProjection()
        finally:
            if view is not None and previous_color is not None:
                view.setForeGroundColor(previous_color)
            if previous_node is not None:
                doc.setActiveNode(previous_node)
        self.bridge_revision += 1
        result = self._selection_result(doc, action, changed=True)
        result.update(
            {
                "layer_id": self._node_id(node),
                "undo_steps": 1,
                "native_action": action_id,
            }
        )
        if action == "fill" and "color" in arguments:
            result["color"] = arguments["color"]
        return result

    def _apply_selection(self, doc, selection, combine: str) -> None:
        current = doc.selection()
        if combine == "replace":
            doc.setSelection(selection)
            return
        if current is None or current.width() <= 0 or current.height() <= 0:
            if combine == "add":
                doc.setSelection(selection)
                return
            raise KritaOperationError(
                "NO_SELECTION", f"Selection combine mode {combine} requires an existing selection"
            )
        if combine == "add":
            current.add(selection)
        elif combine == "subtract":
            current.subtract(selection)
        else:
            current.intersect(selection)

    def _paint_node(self, doc, layer_id: str, *, allow_locked: bool):
        node = self._find_node(doc, layer_id)
        if node is None or node.type().lower() != "paintlayer":
            raise KritaOperationError(
                "LAYER_NOT_PAINTABLE", "Target layer is not a paint layer"
            )
        if not allow_locked and bool(_safe_call(node, "locked", default=False)):
            raise KritaOperationError("LAYER_LOCKED", "Target paint layer is locked")
        return node

    def _selection_combine(self, arguments: Mapping[str, Any]) -> str:
        combine = arguments.get("combine", "replace")
        if combine not in {"replace", "add", "subtract", "intersect"}:
            raise KritaOperationError(
                "INVALID_ARGUMENT", "Unsupported selection combine mode"
            )
        return combine

    def _trigger_edit_action(self, action_id: str, doc) -> None:
        action = Krita.instance().action(action_id)
        if action is None:
            raise KritaOperationError(
                "UNSUPPORTED_FEATURE",
                "Krita does not expose the required native edit action",
                {"action": action_id},
            )
        if not action.isEnabled():
            raise KritaOperationError(
                "ACTION_DISABLED",
                "Krita has disabled the required native edit action",
                {"action": action_id},
            )
        action.trigger()
        doc.waitForDone()

    def _selection_result(
        self, doc, action: str, *, changed: bool, **details: Any
    ) -> dict[str, Any]:
        selection = doc.selection()
        has_selection = bool(
            selection is not None and selection.width() > 0 and selection.height() > 0
        )
        result: dict[str, Any] = {
            "action": action,
            "document_id": self._document_id(doc),
            "changed": changed,
            "has_selection": has_selection,
            "selection_bounds": (
                {
                    "x": int(selection.x()),
                    "y": int(selection.y()),
                    "width": int(selection.width()),
                    "height": int(selection.height()),
                }
                if has_selection
                else None
            ),
            "document_modified": bool(doc.modified()),
        }
        result.update(details)
        return result
