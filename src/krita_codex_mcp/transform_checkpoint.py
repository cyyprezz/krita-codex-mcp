from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Protocol

from krita_codex_protocol import PathPolicy, PathPolicyError

from .bridge_client import BridgeCallError

QT_SETTLE_SECONDS = 1.0


class BridgeExecutor(Protocol):
    async def execute(
        self, operation: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]: ...


async def checkpoint_reopen_transform(
    client: BridgeExecutor,
    paths: PathPolicy,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Yield between KRA close/open calls before one destructive transform."""
    previous_document_id = str(arguments["document_id"])
    previous_layer_id = str(arguments["layer_id"])

    await asyncio.sleep(QT_SETTLE_SECONDS)
    current = await client.execute(
        "layers", {"action": "read", "document_id": previous_document_id}
    )
    file_name = current.get("document_file_name")
    if not isinstance(file_name, str) or not file_name:
        raise BridgeCallError(
            "UNSAVED_DOCUMENT",
            "Safe transform continuation requires a KRA path",
            {"recovery": "save the document once with krita_save_export"},
        )
    try:
        path = paths.validate_input(file_name, extensions={".kra"})
    except PathPolicyError as exc:
        raise BridgeCallError("PATH_NOT_ALLOWED", str(exc), {}) from exc
    if not _tree_contains_layer(current.get("layers"), previous_layer_id):
        raise BridgeCallError(
            "LAYER_NOT_FOUND",
            "Layer UUID was not found before the checkpoint round trip",
            {"document_id": previous_document_id, "layer_id": previous_layer_id},
        )

    await client.execute(
        "document", {"action": "save", "document_id": previous_document_id}
    )
    await asyncio.sleep(QT_SETTLE_SECONDS)
    await client.execute(
        "document",
        {
            "action": "close",
            "document_id": previous_document_id,
            "discard_changes": False,
        },
    )
    await asyncio.sleep(QT_SETTLE_SECONDS)
    try:
        reopened = await client.execute("document", {"action": "open", "path": str(path)})
    except BridgeCallError as exc:
        raise BridgeCallError(
            "DOCUMENT_REOPEN_FAILED",
            "Krita saved and closed the KRA but could not reopen it",
            {
                "path": str(path),
                "document_closed": True,
                "cause_code": exc.code,
                "cause_message": exc.message,
            },
            exc.retryable,
        ) from exc
    document_id = reopened.get("affected_document_id")
    if not isinstance(document_id, str) or not document_id:
        raise BridgeCallError(
            "INVALID_BRIDGE_RESPONSE",
            "Krita reopened the KRA without returning a document ID",
            {"path": str(path), "document_closed": True},
        )

    await asyncio.sleep(QT_SETTLE_SECONDS)
    refreshed = await client.execute(
        "layers", {"action": "read", "document_id": document_id}
    )
    if not _tree_contains_layer(refreshed.get("layers"), previous_layer_id):
        raise BridgeCallError(
            "LAYER_REMAP_FAILED",
            "The KRA reopened but the target layer UUID could not be resolved",
            {
                "path": str(path),
                "previous_document_id": previous_document_id,
                "document_id": document_id,
                "layer_id": previous_layer_id,
            },
        )

    transform_arguments = dict(arguments)
    transform_arguments["document_id"] = document_id
    result = await client.execute("layers", transform_arguments)
    await asyncio.sleep(QT_SETTLE_SECONDS)
    result["checkpoint_reopen"] = {
        "document_reopened": True,
        "path": str(path),
        "previous_document_id": previous_document_id,
        "document_id": document_id,
        "previous_layer_id": previous_layer_id,
        "layer_id": result.get("affected_layer_id", previous_layer_id),
        "qt_settle_seconds": QT_SETTLE_SECONDS,
    }
    return result


def _tree_contains_layer(raw_layers: object, layer_id: str) -> bool:
    if not isinstance(raw_layers, list):
        return False
    pending = list(raw_layers)
    while pending:
        layer = pending.pop()
        if not isinstance(layer, dict):
            continue
        if layer.get("id") == layer_id:
            return True
        children = layer.get("children")
        if isinstance(children, list):
            pending.extend(children)
    return False
