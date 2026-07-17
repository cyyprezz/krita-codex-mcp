from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "krita-plugin"
    / "kritacodexbridge"
    / "compatibility.py"
)
SPEC = importlib.util.spec_from_file_location("krita_compatibility_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
compatibility = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compatibility)


class _Uuid:
    @staticmethod
    def toString() -> str:
        return "{00000000-0000-0000-0000-000000000001}"


class _Node:
    def __init__(self, kind: str = "paintlayer") -> None:
        self.kind = kind
        self.children: list[_Node] = []

    def type(self):
        return self.kind

    @staticmethod
    def uniqueId():
        return _Uuid()

    def childNodes(self):
        return self.children

    @staticmethod
    def addChildNode(*_args):
        return True

    @staticmethod
    def paintLine(*_args):
        return None

    @staticmethod
    def paintPath(*_args):
        return None

    @staticmethod
    def paintPolygon(*_args):
        return None

    @staticmethod
    def paintRectangle(*_args):
        return None

    @staticmethod
    def paintEllipse(*_args):
        return None

    @staticmethod
    def setPixelData(*_args):
        return True

    @staticmethod
    def scaleNode(*_args):
        return None

    @staticmethod
    def rotateNode(*_args):
        return None

    @staticmethod
    def shearNode(*_args):
        return None

    @staticmethod
    def cropNode(*_args):
        return None

    @staticmethod
    def mirrorNodeX(*_args):
        return None

    @staticmethod
    def mirrorNodeY(*_args):
        return None


class _Document:
    def __init__(self) -> None:
        self.root = _Node("root")
        self.paint = _Node()
        self.root.children.append(self.paint)

    def rootNode(self):
        return self.root

    def activeNode(self):
        return self.paint

    @staticmethod
    def projection(*_args):
        return object()

    @staticmethod
    def refreshProjection():
        return None

    @staticmethod
    def waitForDone():
        return None

    @staticmethod
    def saveAs(*_args):
        return True

    @staticmethod
    def exportImage(*_args):
        return True

    @staticmethod
    def setBatchmode(*_args):
        return None

    @staticmethod
    def createNode(*_args):
        return _Node()

    @staticmethod
    def createGroupLayer(*_args):
        return _Node("grouplayer")

    @staticmethod
    def setActiveNode(*_args):
        return None

    @staticmethod
    def clone():
        return _Document()

    @staticmethod
    def annotation(*_args):
        return b""

    @staticmethod
    def setAnnotation(*_args):
        return None

    @staticmethod
    def selection():
        return None

    @staticmethod
    def setSelection(*_args):
        return None

    @staticmethod
    def createTransformMask(*_args):
        return _Node("transformmask")


class _View:
    def __init__(self, document: _Document) -> None:
        self._document = document

    def document(self):
        return self._document

    @staticmethod
    def currentBrushPreset():
        return object()

    @staticmethod
    def setCurrentBrushPreset(*_args):
        return None

    @staticmethod
    def brushSize():
        return 5.0

    @staticmethod
    def setBrushSize(*_args):
        return None

    @staticmethod
    def foregroundColor():
        return object()

    @staticmethod
    def setForeGroundColor(*_args):
        return None


class _Window:
    def __init__(self, view: _View) -> None:
        self._view = view

    def views(self):
        return [self._view]


class _App:
    def __init__(self, document: _Document, version: str = "5.3.2.1") -> None:
        self._version = version
        self.window = _Window(_View(document))

    def version(self):
        return self._version

    @staticmethod
    def createDocument(*_args):
        return _Document()

    @staticmethod
    def openDocument(*_args):
        return _Document()

    @staticmethod
    def documents():
        return []

    def activeWindow(self):
        return self.window

    def windows(self):
        return [self.window]

    @staticmethod
    def action(_action_id):
        return object()


class CompatibilityRuntimeTests(unittest.TestCase):
    def test_tested_build_with_complete_api_is_compatible(self) -> None:
        document = _Document()
        report = compatibility.build_runtime_compatibility(_App(document), document)

        self.assertEqual(report["status"], "compatible")
        self.assertTrue(report["evaluation_complete"])
        self.assertEqual(report["validation"], "tested_build")
        self.assertEqual(report["issues"], [])
        self.assertTrue(all(report["capabilities"].values()))

    def test_unknown_version_is_capability_checked_instead_of_rejected(self) -> None:
        document = _Document()
        report = compatibility.build_runtime_compatibility(
            _App(document, "6.0.0"), document
        )

        self.assertEqual(report["status"], "compatible")
        self.assertEqual(report["validation"], "capability_checked")

    def test_git_suffix_does_not_hide_a_tested_base_version(self) -> None:
        document = _Document()
        report = compatibility.build_runtime_compatibility(
            _App(document, "5.3.2.1 (git 0619060)"), document
        )

        self.assertEqual(report["krita_base_version"], "5.3.2.1")
        self.assertEqual(report["validation"], "tested_build")

    def test_missing_optional_native_shape_marks_only_affected_tool_limited(self) -> None:
        document = _Document()
        document.paint.paintEllipse = None
        report = compatibility.build_runtime_compatibility(_App(document), document)

        self.assertEqual(report["status"], "limited")
        issue = report["issues"][0]
        self.assertEqual(issue["severity"], "warning")
        self.assertEqual(issue["feature"], "native_paint_ellipse")
        self.assertEqual(issue["affected_tools"], ["krita_draw"])

    def test_missing_projection_is_critical(self) -> None:
        document = _Document()
        document.projection = None
        report = compatibility.build_runtime_compatibility(_App(document), document)

        self.assertEqual(report["status"], "incompatible")
        self.assertEqual(report["issues"][0]["severity"], "critical")
        self.assertEqual(report["issues"][0]["feature"], "document_projection")

    def test_no_document_reports_unknown_without_claiming_incompatibility(self) -> None:
        document = _Document()
        report = compatibility.build_runtime_compatibility(_App(document), None)

        self.assertEqual(report["status"], "unknown")
        self.assertFalse(report["evaluation_complete"])
        self.assertIsNone(report["capabilities"]["document_projection"])


if __name__ == "__main__":
    unittest.main()
