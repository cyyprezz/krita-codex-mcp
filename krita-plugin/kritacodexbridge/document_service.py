from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from krita import InfoObject, Krita

from krita_codex_protocol import KRITA_DOCUMENT_EXTENSIONS, PathPolicyError

from ._service_helpers import (
    _bounded_float,
    _bounded_int,
    _optional_text,
    _required_text,
    _safe_call,
)
from .errors import KritaOperationError


class DocumentOperationsMixin:
    def state(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        app = Krita.instance()
        doc = self._target_document(_arguments)
        active = doc.activeNode()
        active_document = app.activeDocument()
        is_active = self._same_document(doc, active_document)
        view = self._optional_view() if is_active else None
        return {
            "krita_version": _safe_call(app, "version"),
            "document_id": self._document_id(doc),
            "document_active": is_active,
            "document": {
                "name": doc.name(),
                "file_name": doc.fileName(),
                "modified": bool(doc.modified()),
                "width": int(doc.width()),
                "height": int(doc.height()),
                "resolution": int(doc.resolution()),
                "color_model": doc.colorModel(),
                "color_depth": doc.colorDepth(),
                "color_profile": doc.colorProfile(),
            },
            "active_layer_id": self._node_id(active) if active else None,
            "layer_stack_order": "bottom_to_top",
            "layers": self._layer_forest(doc),
            "painting": self._painting_state(view),
            "capabilities": {
                "native_paint_line": bool(active and hasattr(active, "paintLine")),
                "native_paint_path": bool(active and hasattr(active, "paintPath")),
            },
        }

    def document(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        app = Krita.instance()
        action = arguments.get("action")
        if action not in {"list", "create", "open", "activate", "save", "close"}:
            raise KritaOperationError("INVALID_ARGUMENT", f"Unsupported document action: {action}")
        if action == "list":
            return self._document_result(app, action)
        if action == "create":
            width = _bounded_int(
                arguments.get("width"), "width", 1, self.config.document_max_dimension
            )
            height = _bounded_int(
                arguments.get("height"), "height", 1, self.config.document_max_dimension
            )
            if width * height > self.config.document_max_pixels:
                raise KritaOperationError(
                    "DOCUMENT_TOO_LARGE",
                    "Requested document exceeds the configured pixel limit",
                    {
                        "pixels": width * height,
                        "limit": self.config.document_max_pixels,
                    },
                )
            name = _required_text(arguments.get("name"), "name", 200)
            color_model = _required_text(arguments.get("color_model"), "color_model", 32)
            color_depth = _required_text(arguments.get("color_depth"), "color_depth", 32)
            profile = _optional_text(arguments.get("profile", ""), "profile", 300)
            resolution = _bounded_float(arguments.get("resolution"), "resolution", 1.0, 2400.0)
            models = [str(value) for value in app.colorModels()]
            if color_model not in models:
                raise KritaOperationError(
                    "UNSUPPORTED_COLOR_MODEL",
                    "Krita does not provide the requested color model",
                    {"requested": color_model, "available": models},
                )
            depths = [str(value) for value in app.colorDepths(color_model)]
            if color_depth not in depths:
                raise KritaOperationError(
                    "UNSUPPORTED_COLOR_DEPTH",
                    "Krita does not provide the requested depth for this color model",
                    {"requested": color_depth, "available": depths},
                )
            profiles = [str(value) for value in app.profiles(color_model, color_depth)]
            if profile and profile not in profiles:
                raise KritaOperationError(
                    "UNSUPPORTED_COLOR_PROFILE",
                    "Krita does not provide the requested profile for this color space",
                    {"requested": profile, "available": profiles},
                )
            try:
                doc = app.createDocument(
                    width,
                    height,
                    name,
                    color_model,
                    color_depth,
                    profile,
                    resolution,
                )
            except Exception as exc:
                raise KritaOperationError(
                    "DOCUMENT_CREATE_FAILED",
                    "Krita could not create the document",
                    {"reason": str(exc)},
                ) from exc
            if doc is None:
                raise KritaOperationError(
                    "DOCUMENT_CREATE_FAILED", "Krita could not create the document"
                )
            self._show_document(app, doc)
            self.bridge_revision += 1
            return self._document_result(app, action, affected=doc)
        if action == "open":
            try:
                path = self.path_policy.validate_input(
                    arguments.get("path"), extensions=set(KRITA_DOCUMENT_EXTENSIONS)
                )
            except PathPolicyError as exc:
                raise KritaOperationError("PATH_NOT_ALLOWED", str(exc)) from exc
            previous_batchmode = bool(app.batchmode())
            app.setBatchmode(True)
            try:
                try:
                    doc = app.openDocument(str(path))
                except Exception as exc:
                    raise KritaOperationError(
                        "DOCUMENT_OPEN_FAILED",
                        "Krita could not open the document",
                        {"path": str(path), "reason": str(exc)},
                    ) from exc
            finally:
                app.setBatchmode(previous_batchmode)
            if doc is None:
                raise KritaOperationError(
                    "DOCUMENT_OPEN_FAILED",
                    "Krita could not open the document",
                    {"path": str(path)},
                )
            self._show_document(app, doc)
            doc.waitForDone()
            self.bridge_revision += 1
            return self._document_result(app, action, affected=doc)

        document_id = _required_text(arguments.get("document_id"), "document_id", 80)
        doc = self._find_document(app, document_id)
        if doc is None:
            raise KritaOperationError("DOCUMENT_NOT_FOUND", "Document ID was not found")
        if action == "activate":
            self._activate_document_view(doc)
            self.bridge_revision += 1
            return self._document_result(app, action, affected=doc)
        if action == "save":
            file_name = doc.fileName()
            if not file_name:
                raise KritaOperationError(
                    "UNSAVED_DOCUMENT",
                    "Document has no KRA path; save it once with krita_save_export",
                )
            try:
                path = self.path_policy.validate_output(
                    file_name, extensions={".kra"}, overwrite=True
                )
            except PathPolicyError as exc:
                raise KritaOperationError("PATH_NOT_ALLOWED", str(exc)) from exc
            previous_batchmode = bool(doc.batchmode())
            doc.setBatchmode(True)
            try:
                if not doc.save():
                    raise KritaOperationError(
                        "SAVE_FAILED",
                        "Krita could not checkpoint the KRA document",
                        {"path": str(path)},
                    )
                doc.waitForDone()
            finally:
                doc.setBatchmode(previous_batchmode)
            self.bridge_revision += 1
            result = self._document_result(app, action, affected=doc)
            result["saved_path"] = str(path)
            return result
        if action == "close":
            discard_changes = arguments.get("discard_changes", False)
            if not isinstance(discard_changes, bool):
                raise KritaOperationError("INVALID_ARGUMENT", "discard_changes must be a boolean")
            was_modified = bool(doc.modified())
            if was_modified and not discard_changes:
                raise KritaOperationError(
                    "UNSAVED_CHANGES",
                    "Document has unsaved changes; save it or pass discard_changes=true",
                    {"document_id": document_id},
                )
            if was_modified:
                doc.setModified(False)
            if not doc.close():
                if was_modified:
                    doc.setModified(True)
                raise KritaOperationError(
                    "DOCUMENT_CLOSE_FAILED", "Krita could not close the document"
                )
            self._document_ids.pop(id(doc), None)
            self.bridge_revision += 1
            result = self._document_result(app, action)
            result["closed_document_id"] = document_id
            return result

    def save_export(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        doc = self._target_document(arguments)
        overwrite = arguments.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise KritaOperationError("INVALID_ARGUMENT", "overwrite must be a boolean")
        kra_raw = arguments.get("kra_path")
        png_raw = arguments.get("png_path")
        if kra_raw is None and png_raw is None:
            raise KritaOperationError("INVALID_ARGUMENT", "Provide kra_path and/or png_path")
        try:
            kra_path = (
                self.path_policy.validate_output(kra_raw, extensions={".kra"}, overwrite=overwrite)
                if kra_raw is not None
                else None
            )
            png_path = (
                self.path_policy.validate_output(png_raw, extensions={".png"}, overwrite=overwrite)
                if png_raw is not None
                else None
            )
        except PathPolicyError as exc:
            raise KritaOperationError("PATH_NOT_ALLOWED", str(exc)) from exc

        completed: list[dict[str, str]] = []
        previous_batchmode = bool(doc.batchmode())
        doc.setBatchmode(True)
        try:
            if kra_path is not None:
                if not doc.saveAs(str(kra_path)):
                    raise KritaOperationError(
                        "SAVE_FAILED",
                        "Krita could not save the KRA file",
                        {"path": str(kra_path)},
                    )
                doc.waitForDone()
                completed.append({"kind": "kra", "path": str(kra_path)})
            if png_path is not None:
                options = InfoObject()
                options.setProperty("alpha", True)
                options.setProperty("compression", 6)
                options.setProperty("forceSRGB", False)
                options.setProperty("indexed", False)
                options.setProperty("interlaced", False)
                options.setProperty("saveSRGBProfile", True)
                if not doc.exportImage(str(png_path), options):
                    raise KritaOperationError(
                        "EXPORT_FAILED",
                        "Krita could not export the PNG file",
                        {"path": str(png_path), "completed": completed},
                    )
                doc.waitForDone()
                completed.append({"kind": "png", "path": str(png_path)})
        finally:
            doc.setBatchmode(previous_batchmode)
        self.bridge_revision += 1
        return {
            "document_id": self._document_id(doc),
            "outputs": completed,
            "document_file_name": doc.fileName(),
        }

    def _document_result(self, app, action: str, affected=None) -> dict[str, Any]:
        documents = list(app.documents())
        live_keys = {id(doc) for doc in documents}
        self._document_ids = {
            key: value for key, value in self._document_ids.items() if key in live_keys
        }
        active = app.activeDocument()
        active_id = self._document_id(active) if active is not None else None
        result = {
            "action": action,
            "active_document_id": active_id,
            "documents": [
                {
                    "document_id": self._document_id(doc),
                    "name": doc.name(),
                    "file_name": doc.fileName(),
                    "modified": bool(doc.modified()),
                    "width": int(doc.width()),
                    "height": int(doc.height()),
                    "color_model": doc.colorModel(),
                    "color_depth": doc.colorDepth(),
                    "active": self._document_id(doc) == active_id,
                }
                for doc in documents
            ],
        }
        if affected is not None:
            result["affected_document_id"] = self._document_id(affected)
        if action == "list":
            result["create_options"] = {
                str(model): [str(depth) for depth in app.colorDepths(str(model))]
                for model in app.colorModels()
            }
        return result
