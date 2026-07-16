from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path


class _Rect:
    def __init__(self, x=0, y=0, width=0, height=0) -> None:
        self._x = x
        self._y = y
        self._width = width
        self._height = height

    def x(self):
        return self._x

    def y(self):
        return self._y

    def width(self):
        return self._width

    def height(self):
        return self._height

    def isEmpty(self):
        return self._width <= 0 or self._height <= 0

    def intersected(self, other):
        left = max(self._x, other._x)
        top = max(self._y, other._y)
        right = min(self._x + self._width, other._x + other._width)
        bottom = min(self._y + self._height, other._y + other._height)
        return _Rect(left, top, max(0, right - left), max(0, bottom - top))

    def united(self, other):
        if self.isEmpty():
            return other
        if other.isEmpty():
            return self
        left = min(self._x, other._x)
        top = min(self._y, other._y)
        right = max(self._x + self._width, other._x + other._width)
        bottom = max(self._y + self._height, other._y + other._height)
        return _Rect(left, top, right - left, bottom - top)


class _Point:
    def __init__(self, x=0, y=0) -> None:
        self.x = x
        self.y = y


class _PainterPath:
    def __init__(self, *_args) -> None:
        pass


qt = types.ModuleType("PyQt5")
qt_core = types.ModuleType("PyQt5.QtCore")
qt_core.QPoint = _Point
qt_core.QPointF = _Point
qt_core.QRect = _Rect
qt_core.QRectF = _Rect
qt_gui = types.ModuleType("PyQt5.QtGui")
qt_gui.QColor = type("QColor", (), {})
qt_gui.QPainterPath = _PainterPath
sys.modules.setdefault("PyQt5", qt)
sys.modules.setdefault("PyQt5.QtCore", qt_core)
sys.modules.setdefault("PyQt5.QtGui", qt_gui)

krita = types.ModuleType("krita")
krita.Krita = type("Krita", (), {"instance": staticmethod(lambda: None)})
krita.ManagedColor = type("ManagedColor", (), {})
sys.modules.setdefault("krita", krita)

PLUGIN_ROOT = (
    Path(__file__).resolve().parents[1] / "krita-plugin" / "kritacodexbridge"
)
PACKAGE_NAME = "_krita_drawing_runtime_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PLUGIN_ROOT)]
sys.modules.setdefault(PACKAGE_NAME, package)
drawing_module = importlib.import_module(f"{PACKAGE_NAME}.drawing_service")
errors_module = importlib.import_module(f"{PACKAGE_NAME}.errors")

DrawingOperationsMixin = drawing_module.DrawingOperationsMixin
KritaOperationError = errors_module.KritaOperationError


class _Preset:
    @staticmethod
    def name():
        return "Basic-5 Size"


class _View:
    def __init__(self) -> None:
        self.preset = _Preset()
        self.brush_size = 5.0
        self.foreground = object()
        self.background = object()

    def currentBrushPreset(self):
        return self.preset

    def setCurrentBrushPreset(self, value) -> None:
        self.preset = value

    def brushSize(self):
        return self.brush_size

    def setBrushSize(self, value) -> None:
        self.brush_size = value

    def foregroundColor(self):
        return self.foreground

    def backgroundColor(self):
        return self.background

    def setForeGroundColor(self, value) -> None:
        self.foreground = value

    def setBackGroundColor(self, value) -> None:
        self.background = value


class _Node:
    def __init__(self, behavior: str) -> None:
        self.behavior = behavior
        self.extents = behavior in {"existing_noop", "same_bounds_change"}
        self.rect = _Rect(2, 2, 16, 16) if self.extents else _Rect()
        self.payload = b"before" if self.extents else b""

    @staticmethod
    def type():
        return "paintlayer"

    @staticmethod
    def locked():
        return False

    @staticmethod
    def paintAbility():
        return "PAINT"

    def hasExtents(self):
        return self.extents

    def bounds(self):
        return self.rect

    def pixelData(self, *_args):
        return self.payload

    def paintLine(self, *_args) -> None:
        if self.behavior == "empty_change":
            self.extents = True
            self.rect = _Rect(8, 8, 12, 4)
            self.payload = b"painted"
        elif self.behavior == "same_bounds_change":
            self.payload = b"after"


class _Document:
    def __init__(self, node) -> None:
        self.node = node

    @staticmethod
    def width():
        return 64

    @staticmethod
    def height():
        return 64

    def activeNode(self):
        return self.node

    def setActiveNode(self, node) -> None:
        self.node = node


class _DrawingService(DrawingOperationsMixin):
    def __init__(self, behavior: str) -> None:
        self.node = _Node(behavior)
        self.doc = _Document(self.node)
        self.view = _View()
        self.bridge_revision = 0
        self.undo_calls = 0

    def _target_document(self, _arguments, *, activate=False):
        return self.doc

    def _active_view(self):
        return self.view

    def _find_node(self, _doc, _layer_id):
        return self.node

    @staticmethod
    def _requested_preset(_arguments):
        return None

    @staticmethod
    def _requested_managed_color(_arguments, _field, _view):
        return None

    @staticmethod
    def _document_id(_doc):
        return "document-1"

    @staticmethod
    def _node_id(_node):
        return "layer-1"

    @staticmethod
    def _synchronize_projection(_doc) -> None:
        pass

    def _undo_segments(self, count, _doc):
        self.undo_calls += count
        return count


def _draw_line(service):
    return service.draw(
        {
            "shape": "line",
            "layer_id": "layer-1",
            "points": [
                {"x": 8, "y": 8, "pressure": 1.0},
                {"x": 20, "y": 8, "pressure": 1.0},
            ],
            "stroke_style": "ForegroundColor",
            "fill_style": "None",
        }
    )


class DrawingRuntimeTests(unittest.TestCase):
    def test_empty_layer_noop_returns_structured_error_without_unsafe_undo(self) -> None:
        service = _DrawingService("empty_noop")

        with self.assertRaises(KritaOperationError) as raised:
            _draw_line(service)

        self.assertEqual(raised.exception.code, "DRAW_NO_CHANGE")
        self.assertEqual(raised.exception.details["document_id"], "document-1")
        self.assertEqual(raised.exception.details["layer_id"], "layer-1")
        self.assertFalse(raised.exception.details["before_has_extents"])
        self.assertFalse(raised.exception.details["after_has_extents"])
        self.assertFalse(raised.exception.details["undo_attempted"])
        self.assertEqual(service.undo_calls, 0)
        self.assertEqual(service.bridge_revision, 0)

    def test_empty_layer_visible_change_is_verified(self) -> None:
        service = _DrawingService("empty_change")

        result = _draw_line(service)

        self.assertTrue(result["verified_content_change"])
        self.assertEqual(result["undo_steps"], 1)
        self.assertEqual(service.bridge_revision, 1)

    def test_pixel_change_inside_unchanged_bounds_is_verified(self) -> None:
        service = _DrawingService("same_bounds_change")

        result = _draw_line(service)

        self.assertTrue(result["verified_content_change"])
        self.assertEqual(service.bridge_revision, 1)

    def test_existing_layer_noop_is_rejected(self) -> None:
        service = _DrawingService("existing_noop")

        with self.assertRaises(KritaOperationError) as raised:
            _draw_line(service)

        self.assertEqual(raised.exception.code, "DRAW_NO_CHANGE")
        self.assertTrue(raised.exception.details["before_has_extents"])
        self.assertEqual(service.undo_calls, 0)


if __name__ == "__main__":
    unittest.main()
