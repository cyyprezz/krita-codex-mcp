from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path

PLUGIN_ROOT = (
    Path(__file__).resolve().parents[1] / "krita-plugin" / "kritacodexbridge"
)
PACKAGE_NAME = "_krita_layer_transaction_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PLUGIN_ROOT)]
sys.modules.setdefault(PACKAGE_NAME, package)
transaction_module = importlib.import_module(f"{PACKAGE_NAME}.layer_transaction")
errors_module = importlib.import_module(f"{PACKAGE_NAME}.errors")

reparent_layer_transactionally = transaction_module.reparent_layer_transactionally
KritaOperationError = errors_module.KritaOperationError


class _FakeNode:
    def __init__(self, node_id: str, *, payload: str = "pixels") -> None:
        self.node_id = node_id
        self.payload = payload
        self.parent = None
        self.children = []
        self.reject_ids: set[str] = set()

    def duplicate(self):
        return _FakeNode(f"{self.node_id}-recovery", payload=self.payload)

    def parentNode(self):
        return self.parent

    def childNodes(self):
        return list(self.children)

    def addChildNode(self, child, above):
        # Libkis can return True even when its internal command rejects the add.
        if child.node_id in self.reject_ids or child.parent is not None:
            return True
        index = 0 if above is None else self.children.index(above) + 1
        self.children.insert(index, child)
        child.parent = self
        return True

    def removeChildNode(self, child):
        if child not in self.children:
            return False
        self.children.remove(child)
        child.parent = None
        return True


class _FakeDocument:
    def __init__(self, root) -> None:
        self.root = root
        self.active = None

    def setActiveNode(self, node) -> None:
        self.active = node

    def rootNode(self):
        return self.root


class _FakeService:
    def __init__(self, root) -> None:
        self.root = root
        self.bridge_revision = 0
        self.synchronizations = 0

    @staticmethod
    def _node_id(node):
        return node.node_id

    @staticmethod
    def _same_node(left, right):
        return left is not None and right is not None and left.node_id == right.node_id

    def _find_node(self, _doc, node_id):
        pending = [self.root]
        while pending:
            node = pending.pop()
            if node.node_id == node_id:
                return node
            pending.extend(node.children)
        return None

    def _synchronize_projection(self, _doc) -> None:
        self.synchronizations += 1


def _attach(parent, child) -> None:
    parent.children.append(child)
    child.parent = parent


class LayerTransactionTests(unittest.TestCase):
    def test_verified_move_preserves_original_identity_and_content(self) -> None:
        root = _FakeNode("root")
        source = _FakeNode("source")
        destination = _FakeNode("destination")
        layer = _FakeNode("layer", payload="important pixels")
        for node in (source, destination):
            _attach(root, node)
        _attach(source, layer)
        service = _FakeService(root)
        doc = _FakeDocument(root)

        moved = reparent_layer_transactionally(
            service,
            doc,
            layer,
            source_parent=source,
            source_anchor=None,
            destination_parent=destination,
            destination_anchor=None,
        )

        self.assertIs(moved, layer)
        self.assertEqual(moved.payload, "important pixels")
        self.assertIs(moved.parent, destination)
        self.assertNotIn(layer, source.children)

    def test_false_success_restores_original_source_layer(self) -> None:
        root = _FakeNode("root")
        source = _FakeNode("source")
        destination = _FakeNode("destination")
        layer = _FakeNode("layer")
        for node in (source, destination):
            _attach(root, node)
        _attach(source, layer)
        destination.reject_ids.add("layer")
        service = _FakeService(root)
        doc = _FakeDocument(root)

        with self.assertRaises(KritaOperationError) as raised:
            reparent_layer_transactionally(
                service,
                doc,
                layer,
                source_parent=source,
                source_anchor=None,
                destination_parent=destination,
                destination_anchor=None,
            )

        self.assertEqual(raised.exception.code, "LAYER_REORDER_FAILED")
        self.assertTrue(raised.exception.details["rolled_back"])
        self.assertFalse(raised.exception.details["recovered_from_backup"])
        self.assertIs(layer.parent, source)

    def test_recovery_copy_prevents_data_loss_when_original_cannot_be_restored(self) -> None:
        root = _FakeNode("root")
        source = _FakeNode("source")
        destination = _FakeNode("destination")
        layer = _FakeNode("layer", payload="important pixels")
        for node in (source, destination):
            _attach(root, node)
        _attach(source, layer)
        destination.reject_ids.add("layer")
        source.reject_ids.add("layer")
        service = _FakeService(root)
        doc = _FakeDocument(root)

        with self.assertRaises(KritaOperationError) as raised:
            reparent_layer_transactionally(
                service,
                doc,
                layer,
                source_parent=source,
                source_anchor=None,
                destination_parent=destination,
                destination_anchor=None,
            )

        self.assertTrue(raised.exception.details["rolled_back"])
        self.assertTrue(raised.exception.details["recovered_from_backup"])
        recovery_id = raised.exception.details["recovery_layer_id"]
        recovered = service._find_node(doc, recovery_id)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.payload, "important pixels")
        self.assertIs(recovered.parent, source)

    def test_recovery_copy_falls_back_to_document_root(self) -> None:
        root = _FakeNode("root")
        source = _FakeNode("source")
        destination = _FakeNode("destination")
        layer = _FakeNode("layer", payload="important pixels")
        for node in (source, destination):
            _attach(root, node)
        _attach(source, layer)
        destination.reject_ids.add("layer")
        source.reject_ids.update({"layer", "layer-recovery"})
        service = _FakeService(root)
        doc = _FakeDocument(root)

        with self.assertRaises(KritaOperationError) as raised:
            reparent_layer_transactionally(
                service,
                doc,
                layer,
                source_parent=source,
                source_anchor=None,
                destination_parent=destination,
                destination_anchor=None,
            )

        self.assertTrue(raised.exception.details["rolled_back"])
        self.assertEqual(raised.exception.details["recovery_parent_id"], "root")
        recovered = service._find_node(
            doc, raised.exception.details["recovery_layer_id"]
        )
        self.assertEqual(recovered.payload, "important pixels")
        self.assertIs(recovered.parent, root)


if __name__ == "__main__":
    unittest.main()
