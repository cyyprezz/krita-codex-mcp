from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from uuid import uuid4

from krita import Krita
from PyQt5.QtCore import QBuffer, QByteArray, QIODevice

from ._service_helpers import _required_text
from .errors import KritaOperationError


class ServiceContextMixin:
    def _active_document(self):
        doc = Krita.instance().activeDocument()
        if doc is None:
            raise KritaOperationError("NO_ACTIVE_DOCUMENT", "Krita has no active document")
        return doc

    def _target_document(self, arguments: Mapping[str, Any], *, activate: bool = False):
        app = Krita.instance()
        raw_document_id = arguments.get("document_id")
        if raw_document_id is None:
            return self._active_document()
        document_id = _required_text(raw_document_id, "document_id", 80)
        doc = self._find_document(app, document_id)
        if doc is None:
            raise KritaOperationError("DOCUMENT_NOT_FOUND", "Document ID was not found")
        if activate:
            self._activate_document_view(doc)
        return doc

    def _activate_document_view(self, doc):
        app = Krita.instance()
        for window in app.windows():
            for view in window.views():
                if not self._same_document(view.document(), doc):
                    continue
                window.activate()
                window.showView(view)
                app.setActiveDocument(doc)
                active_window = app.activeWindow()
                active_view = window.activeView()
                if (
                    active_window == window
                    and self._same_document(app.activeDocument(), doc)
                    and self._same_document(
                        active_view.document() if active_view is not None else None,
                        doc,
                    )
                ):
                    return view
        raise KritaOperationError(
            "DOCUMENT_ACTIVATE_FAILED", "Krita could not activate the target document view"
        )

    def _active_view(self):
        view = self._optional_view()
        if view is None:
            raise KritaOperationError("NO_ACTIVE_VIEW", "Krita has no active canvas view")
        return view

    def _optional_view(self):
        window = Krita.instance().activeWindow()
        return window.activeView() if window else None

    @staticmethod
    def _trigger_action(action_id: str, doc, *, scope: str) -> None:
        action = Krita.instance().action(action_id)
        if action is None:
            raise KritaOperationError(
                "UNSUPPORTED_FEATURE",
                f"Krita does not expose the required {scope} action",
                {"action": action_id},
            )
        if not action.isEnabled():
            raise KritaOperationError(
                "ACTION_DISABLED",
                f"Krita has disabled the required {scope} action",
                {"action": action_id},
            )
        action.trigger()
        doc.waitForDone()

    @staticmethod
    def _synchronize_projection(doc) -> None:
        """Wait for queued work and for the projection refresh it schedules."""
        doc.waitForDone()
        doc.refreshProjection()
        doc.waitForDone()

    def _encode_png_preview(self, image) -> bytes:
        payload = QByteArray()
        buffer = QBuffer(payload)
        if not buffer.open(QIODevice.WriteOnly):
            raise KritaOperationError("PREVIEW_FAILED", "Could not allocate the PNG buffer")
        try:
            if not image.save(buffer, "PNG"):
                raise KritaOperationError(
                    "PREVIEW_FAILED", "Krita could not encode the preview as PNG"
                )
        finally:
            buffer.close()
        png = bytes(payload)
        if len(png) > self.config.preview_max_bytes:
            raise KritaOperationError(
                "PREVIEW_TOO_LARGE",
                "Encoded preview exceeds the configured byte limit",
                {"bytes": len(png), "limit": self.config.preview_max_bytes},
            )
        return png

    def _show_document(self, app, doc) -> None:
        window = app.activeWindow()
        if window is None:
            doc.setModified(False)
            doc.close()
            raise KritaOperationError(
                "NO_ACTIVE_WINDOW", "Krita has no window in which to show the document"
            )
        try:
            window.addView(doc)
            app.setActiveDocument(doc)
        except Exception as exc:
            doc.setModified(False)
            doc.close()
            raise KritaOperationError(
                "DOCUMENT_VIEW_FAILED",
                "Krita created the document but could not show it in a view",
                {"reason": str(exc)},
            ) from exc

    def _document_id(self, doc) -> str:
        try:
            root = doc.rootNode()
            if root is not None:
                return self._node_id(root)
        except Exception:
            pass
        key = id(doc)
        value = self._document_ids.get(key)
        if value is None:
            value = str(uuid4())
            self._document_ids[key] = value
        return value

    def _find_document(self, app, document_id: str):
        expected = document_id.strip().lower()
        for doc in app.documents():
            if self._document_id(doc) == expected:
                return doc
        return None

    def _same_document(self, left, right) -> bool:
        return (
            left is not None
            and right is not None
            and self._document_id(left) == self._document_id(right)
        )

    def _node_id(self, node) -> str:
        return str(node.uniqueId().toString()).strip("{}").lower()

    def _find_node(self, doc, layer_id: Any):
        if not isinstance(layer_id, str):
            return None
        expected = layer_id.strip().strip("{}").lower()
        for node in self._walk_nodes(doc.rootNode()):
            if self._node_id(node) == expected:
                return node
        return None

    def _node_index_path(self, doc, node) -> list[int]:
        root = doc.rootNode()
        current = node
        reversed_path: list[int] = []
        while not self._same_node(current, root):
            parent = current.parentNode()
            if parent is None:
                raise KritaOperationError(
                    "LAYER_NOT_FOUND", "Layer is not attached to the source document"
                )
            index = next(
                (
                    index
                    for index, sibling in enumerate(parent.childNodes())
                    if self._same_node(sibling, current)
                ),
                -1,
            )
            if index < 0:
                raise KritaOperationError(
                    "LAYER_NOT_FOUND", "Layer path could not be resolved"
                )
            reversed_path.append(index)
            current = parent
        return list(reversed(reversed_path))

    def _node_at_index_path(self, root, path: list[int]):
        current = root
        for index in path:
            children = list(current.childNodes())
            if not 0 <= index < len(children):
                raise KritaOperationError(
                    "ASSET_EXPORT_FAILED", "Cloned layer structure does not match the source"
                )
            current = children[index]
        return current

    def _same_node(self, left, right) -> bool:
        return (
            left is not None and right is not None and self._node_id(left) == self._node_id(right)
        )

    def _is_descendant(self, candidate, ancestor) -> bool:
        current = candidate.parentNode() if candidate is not None else None
        while current is not None:
            if self._same_node(current, ancestor):
                return True
            current = current.parentNode()
        return False

    def _walk_nodes(self, root) -> Iterable[Any]:
        yield root
        for child in root.childNodes():
            yield from self._walk_nodes(child)
