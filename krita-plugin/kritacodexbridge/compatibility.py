from __future__ import annotations

from collections.abc import Iterable
from typing import Any

COMPATIBILITY_PROFILE_VERSION = 1
TESTED_KRITA_VERSIONS = ("5.3.2.1",)


def build_runtime_compatibility(app, document=None) -> dict[str, Any]:
    """Describe the Krita API surface without silently substituting another backend."""
    version = _safe_text_call(app, "version") or "unknown"
    base_version = version.split(maxsplit=1)[0]
    view = _find_document_view(app, document) if document is not None else None
    root = _safe_call(document, "rootNode") if document is not None else None
    paint_node = _find_paint_node(document, root) if document is not None else None

    capabilities: dict[str, bool | None] = {}
    issues: list[dict[str, Any]] = []

    checks = (
        (
            "document_lifecycle",
            "critical",
            app,
            ("createDocument", "openDocument", "documents", "activeWindow"),
            ("krita_document",),
        ),
        (
            "document_view",
            "critical",
            view,
            ("document",),
            ("krita_document", "krita_brush", "krita_paint_path", "krita_draw"),
        ),
        (
            "document_projection",
            "critical",
            document,
            ("projection", "refreshProjection", "waitForDone"),
            ("krita_state", "krita_preview", "krita_canvas"),
        ),
        (
            "document_persistence",
            "critical",
            document,
            ("saveAs", "exportImage", "setBatchmode"),
            ("krita_document", "krita_save_export"),
        ),
        (
            "stable_layer_tree",
            "critical",
            root,
            ("uniqueId", "childNodes", "addChildNode"),
            ("krita_state", "krita_layers"),
        ),
        (
            "layer_creation",
            "critical",
            document,
            ("createNode", "createGroupLayer", "setActiveNode"),
            ("krita_layers",),
        ),
        (
            "native_paint_line",
            "warning",
            paint_node,
            ("paintLine",),
            ("krita_paint_path", "krita_draw"),
        ),
        (
            "native_paint_path",
            "warning",
            paint_node,
            ("paintPath",),
            ("krita_draw",),
        ),
        (
            "native_paint_polygon",
            "warning",
            paint_node,
            ("paintPolygon",),
            ("krita_draw",),
        ),
        (
            "native_paint_rectangle",
            "warning",
            paint_node,
            ("paintRectangle",),
            ("krita_draw",),
        ),
        (
            "native_paint_ellipse",
            "warning",
            paint_node,
            ("paintEllipse",),
            ("krita_draw",),
        ),
        (
            "native_brush_state",
            "warning",
            view,
            (
                "currentBrushPreset",
                "setCurrentBrushPreset",
                "brushSize",
                "setBrushSize",
                "foregroundColor",
                "setForeGroundColor",
            ),
            ("krita_brush", "krita_paint_path", "krita_draw"),
        ),
        (
            "raster_layer_import",
            "warning",
            paint_node,
            ("setPixelData",),
            ("krita_canvas",),
        ),
        (
            "asset_projection",
            "warning",
            document,
            ("clone", "projection"),
            ("krita_asset",),
        ),
        (
            "project_palette",
            "warning",
            document,
            ("annotation", "setAnnotation"),
            ("krita_color",),
        ),
        (
            "selection_editing",
            "warning",
            document,
            ("selection", "setSelection"),
            ("krita_edit", "krita_color"),
        ),
        (
            "transform_masks",
            "warning",
            document,
            ("createTransformMask",),
            ("krita_layers",),
        ),
        (
            "direct_layer_transforms",
            "warning",
            paint_node,
            (
                "scaleNode",
                "rotateNode",
                "shearNode",
                "cropNode",
            ),
            ("krita_layers",),
        ),
    )

    evaluation_complete = document is not None and paint_node is not None and view is not None
    for feature, severity, target, methods, affected_tools in checks:
        if target is None:
            capabilities[feature] = None
            continue
        missing = [name for name in methods if not callable(getattr(target, name, None))]
        available = not missing
        capabilities[feature] = available
        if not available:
            issues.append(
                {
                    "severity": severity,
                    "code": f"MISSING_{feature.upper()}",
                    "feature": feature,
                    "affected_tools": list(affected_tools),
                    "missing_methods": missing,
                    "message": (
                        "This Krita build does not expose the Python API methods required "
                        f"for {feature.replace('_', ' ')}."
                    ),
                }
            )

    action_checks = (
        (
            "native_history_actions",
            ("edit_undo", "edit_redo"),
            ("krita_history",),
        ),
        (
            "native_selection_actions",
            (
                "fill_selection_foreground_color",
                "clear",
                "edit_copy",
                "edit_cut",
                "edit_paste",
            ),
            ("krita_edit", "krita_color"),
        ),
        (
            "native_canvas_mirror_actions",
            ("mirrorImageHorizontal", "mirrorImageVertical"),
            ("krita_canvas",),
        ),
        (
            "native_layer_mirror_actions",
            ("mirrorNodeX", "mirrorNodeY"),
            ("krita_layers",),
        ),
    )
    for feature, action_ids, affected_tools in action_checks:
        missing = [action_id for action_id in action_ids if not _has_action(app, action_id)]
        capabilities[feature] = not missing
        if missing:
            issues.append(
                {
                    "severity": "warning",
                    "code": f"MISSING_{feature.upper()}",
                    "feature": feature,
                    "affected_tools": list(affected_tools),
                    "missing_actions": missing,
                    "message": (
                        "This Krita build does not expose the registered actions required "
                        f"for {feature.replace('_', ' ')}."
                    ),
                }
            )

    critical = [issue for issue in issues if issue["severity"] == "critical"]
    warnings = [issue for issue in issues if issue["severity"] == "warning"]
    if critical:
        status = "incompatible"
    elif warnings:
        status = "limited"
    elif not evaluation_complete:
        status = "unknown"
    else:
        status = "compatible"

    return {
        "profile_version": COMPATIBILITY_PROFILE_VERSION,
        "krita_version": version,
        "krita_base_version": base_version,
        "status": status,
        "evaluation_complete": evaluation_complete,
        "validation": (
            "tested_build"
            if base_version in TESTED_KRITA_VERSIONS
            else "capability_checked"
        ),
        "tested_versions": list(TESTED_KRITA_VERSIONS),
        "capabilities": capabilities,
        "issues": issues,
        "backend_policy": "native_only_no_silent_pixel_fallback",
    }


def _find_document_view(app, document):
    expected = _document_key(document)
    for window in _safe_iter_call(app, "windows"):
        for view in _safe_iter_call(window, "views"):
            candidate = _safe_call(view, "document")
            if candidate is document or (
                expected is not None and _document_key(candidate) == expected
            ):
                return view
    return None


def _find_paint_node(document, root):
    active = _safe_call(document, "activeNode")
    if _node_type(active) == "paintlayer":
        return active
    for node in _walk_nodes(root):
        if _node_type(node) == "paintlayer":
            return node
    return None


def _walk_nodes(root) -> Iterable[Any]:
    if root is None:
        return
    yield root
    for child in _safe_iter_call(root, "childNodes"):
        yield from _walk_nodes(child)


def _document_key(document) -> str | None:
    root = _safe_call(document, "rootNode")
    unique_id = _safe_call(root, "uniqueId")
    if unique_id is None:
        return None
    try:
        return str(unique_id.toString()).strip("{}").lower()
    except Exception:
        return str(unique_id).strip("{}").lower() or None


def _node_type(node) -> str:
    value = _safe_call(node, "type")
    return str(value).lower() if value is not None else ""


def _safe_call(target, method: str):
    if target is None:
        return None
    candidate = getattr(target, method, None)
    if not callable(candidate):
        return None
    try:
        return candidate()
    except Exception:
        return None


def _safe_text_call(target, method: str) -> str | None:
    value = _safe_call(target, method)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_iter_call(target, method: str) -> tuple[Any, ...]:
    value = _safe_call(target, method)
    if value is None:
        return ()
    try:
        return tuple(value)
    except TypeError:
        return ()


def _has_action(app, action_id: str) -> bool:
    candidate = getattr(app, "action", None)
    if not callable(candidate):
        return False
    try:
        return candidate(action_id) is not None
    except Exception:
        return False
