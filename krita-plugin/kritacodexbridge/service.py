from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from krita_codex_protocol import BridgeConfig, PathPolicy

from .animation_service import AnimationOperationsMixin
from .asset_service import AssetOperationsMixin
from .canvas_service import CanvasOperationsMixin
from .document_service import DocumentOperationsMixin
from .editing_service import EditingOperationsMixin
from .errors import KritaOperationError
from .layer_service import LayerOperationsMixin
from .package_service import PackageOperationsMixin
from .painting_service import PaintingOperationsMixin
from .region_service import RegionOperationsMixin
from .service_context import ServiceContextMixin


class KritaService(
    DocumentOperationsMixin,
    CanvasOperationsMixin,
    EditingOperationsMixin,
    LayerOperationsMixin,
    PaintingOperationsMixin,
    PackageOperationsMixin,
    AnimationOperationsMixin,
    RegionOperationsMixin,
    AssetOperationsMixin,
    ServiceContextMixin,
):
    """All methods are called on Krita's Qt main thread by MainThreadDispatcher."""

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.path_policy = PathPolicy(config.allowed_roots)
        self.bridge_revision = 0
        self._document_ids: dict[int, str] = {}

    def execute(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        handlers = {
            "state": self.state,
            "preview": self.preview,
            "canvas": self.canvas,
            "edit": self.edit,
            "document": self.document,
            "layers": self.layers,
            "history": self.history,
            "brush": self.brush,
            "paint_path": self.paint_path,
            "asset": self.asset,
            "save_export": self.save_export,
        }
        handler = handlers.get(operation)
        if handler is None:
            raise KritaOperationError(
                "UNSUPPORTED_OPERATION", f"Unsupported operation: {operation}"
            )
        result = handler(arguments)
        result["bridge_revision"] = self.bridge_revision
        return result
