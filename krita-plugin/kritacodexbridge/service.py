from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from krita_codex_protocol import BridgeConfig, PathPolicy

from .animation_service import AnimationOperationsMixin
from .asset_service import AssetOperationsMixin
from .canvas_service import CanvasOperationsMixin
from .color_adjustment_service import ColorAdjustmentOperationsMixin
from .color_service import ColorOperationsMixin
from .document_service import DocumentOperationsMixin
from .drawing_service import DrawingOperationsMixin
from .editing_service import EditingOperationsMixin
from .errors import KritaOperationError
from .installation_info import loaded_installation_info
from .layer_service import LayerOperationsMixin
from .layer_transform_service import LayerTransformOperationsMixin
from .package_service import PackageOperationsMixin
from .painting_service import PaintingOperationsMixin
from .region_service import RegionOperationsMixin
from .service_context import ServiceContextMixin
from .transform_mask_service import TransformMaskOperationsMixin


class KritaService(
    DocumentOperationsMixin,
    CanvasOperationsMixin,
    EditingOperationsMixin,
    LayerOperationsMixin,
    TransformMaskOperationsMixin,
    LayerTransformOperationsMixin,
    PaintingOperationsMixin,
    DrawingOperationsMixin,
    ColorOperationsMixin,
    ColorAdjustmentOperationsMixin,
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
        self._destructive_transform_documents: dict[str, str] = {}
        self._transform_mask_creation_counts: dict[str, int] = {}
        self._created_transform_mask_ids: dict[str, set[str]] = {}
        self._layer_transform_backend_session: str | None = None
        self._installation_info = loaded_installation_info()

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
            "draw": self.draw,
            "color": self.color,
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
        result["bridge_distribution_version"] = self._installation_info[
            "distribution_version"
        ]
        result["bridge_protocol_version"] = self._installation_info["protocol_version"]
        return result
