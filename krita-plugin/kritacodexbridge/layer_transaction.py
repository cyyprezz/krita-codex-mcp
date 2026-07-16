from __future__ import annotations

from .errors import KritaOperationError


def reparent_layer_transactionally(
    service,
    doc,
    node,
    *,
    source_parent,
    source_anchor,
    destination_parent,
    destination_anchor,
):
    """Move a node with postcondition checks and a detached recovery copy."""
    original_id = service._node_id(node)
    recovery = node.duplicate()
    if recovery is None:
        raise KritaOperationError(
            "LAYER_REORDER_FAILED",
            "Krita could not create the safety copy required for a verified layer move",
            {"layer_id": original_id},
        )

    if not source_parent.removeChildNode(node):
        raise KritaOperationError(
            "LAYER_REORDER_FAILED", "Krita could not detach the layer from its parent"
        )
    service._synchronize_projection(doc)
    destination_parent.addChildNode(node, destination_anchor)
    service._synchronize_projection(doc)

    moved = service._find_node(doc, original_id)
    if moved is not None and service._same_node(
        moved.parentNode(), destination_parent
    ):
        return moved

    recovery_details = _restore_reparent_source(
        service,
        doc,
        node,
        recovery,
        original_id=original_id,
        source_parent=source_parent,
        source_anchor=source_anchor,
    )
    service.bridge_revision += 1
    recovered = bool(recovery_details["rolled_back"])
    raise KritaOperationError(
        "LAYER_REORDER_FAILED",
        (
            "Krita did not attach the layer to the requested destination; "
            + (
                "the layer content was recovered"
                if recovered
                else "automatic recovery also failed"
            )
        ),
        {
            "layer_id": original_id,
            "source_parent_id": service._node_id(source_parent),
            "destination_parent_id": service._node_id(destination_parent),
            **recovery_details,
        },
    )


def _restore_reparent_source(
    service,
    doc,
    node,
    recovery,
    *,
    original_id: str,
    source_parent,
    source_anchor,
) -> dict[str, object]:
    current = service._find_node(doc, original_id)
    current_parent = current.parentNode() if current is not None else node.parentNode()
    if current_parent is not None:
        current_parent.removeChildNode(current if current is not None else node)
        service._synchronize_projection(doc)

    source_parent.addChildNode(node, source_anchor)
    service._synchronize_projection(doc)
    restored = service._find_node(doc, original_id)
    if restored is not None and service._same_node(restored.parentNode(), source_parent):
        doc.setActiveNode(restored)
        return {
            "rolled_back": True,
            "recovered_from_backup": False,
            "recovery_layer_id": original_id,
        }

    recovery_id = service._node_id(recovery)
    source_parent.addChildNode(recovery, source_anchor)
    service._synchronize_projection(doc)
    restored = service._find_node(doc, recovery_id)
    recovered = restored is not None and service._same_node(
        restored.parentNode(), source_parent
    )
    if recovered:
        doc.setActiveNode(restored)
        return {
            "rolled_back": True,
            "recovered_from_backup": True,
            "recovery_layer_id": recovery_id,
            "recovery_parent_id": service._node_id(source_parent),
        }

    root = doc.rootNode()
    if not service._same_node(root, source_parent):
        root.addChildNode(recovery, None)
        service._synchronize_projection(doc)
        restored = service._find_node(doc, recovery_id)
        recovered = restored is not None and service._same_node(
            restored.parentNode(), root
        )
        if recovered:
            doc.setActiveNode(restored)
    return {
        "rolled_back": recovered,
        "recovered_from_backup": recovered,
        "recovery_layer_id": recovery_id if recovered else None,
        "recovery_parent_id": service._node_id(root) if recovered else None,
    }
