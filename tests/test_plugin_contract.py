from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "krita-plugin" / "kritacodexbridge"


class PluginContractTests(unittest.TestCase):
    def test_plugin_python_sources_compile(self) -> None:
        for path in PLUGIN_ROOT.glob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_painting_uses_native_api_and_no_pixel_writer(self) -> None:
        combined = "\n".join(path.read_text(encoding="utf-8") for path in PLUGIN_ROOT.glob("*.py"))
        self.assertIn("node.paintLine", combined)
        self.assertIn("QPoint(round(", combined)
        self.assertNotIn("QPointF", combined)
        self.assertNotIn("setPixelData", combined)

    def test_no_arbitrary_python_execution_surface(self) -> None:
        for path in PLUGIN_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            called_names = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            self.assertFalse({"eval", "exec", "compile"} & called_names, path.name)

    def test_http_server_binds_to_validated_config_host(self) -> None:
        source = (PLUGIN_ROOT / "http_server.py").read_text(encoding="utf-8")
        self.assertIn("(config.host, config.port)", source)
        self.assertNotIn("0.0.0.0", source)

    def test_qt_dispatch_is_queued_and_timeout_aware(self) -> None:
        source = (PLUGIN_ROOT / "http_bridge.py").read_text(encoding="utf-8")
        self.assertIn("Qt.QueuedConnection", source)
        self.assertIn("envelope.done.wait(timeout_seconds)", source)
        self.assertNotIn("BlockingQueuedConnection", source)

    def test_plugin_has_persistent_log_and_status_action(self) -> None:
        extension = (PLUGIN_ROOT / "extension.py").read_text(encoding="utf-8")
        logging_source = (PLUGIN_ROOT / "logging_utils.py").read_text(encoding="utf-8")
        self.assertIn("krita_codex_bridge_status", extension)
        self.assertIn("RotatingFileHandler", logging_source)
        self.assertNotIn("config.token", extension)

    def test_expanded_surface_uses_native_krita_apis(self) -> None:
        source = (PLUGIN_ROOT / "service.py").read_text(encoding="utf-8")
        self.assertIn("app.createDocument(", source)
        self.assertIn("app.openDocument(", source)
        self.assertIn('"undo": "edit_undo"', source)
        self.assertIn('"redo": "edit_redo"', source)
        self.assertIn("root = doc.rootNode()", source)
        self.assertIn("return self._node_id(root)", source)
        self.assertIn("view.setCurrentBrushPreset", source)
        self.assertIn("ManagedColor.fromQColor", source)

    def test_file_export_temporarily_suppresses_krita_dialogs(self) -> None:
        source = (PLUGIN_ROOT / "service.py").read_text(encoding="utf-8")
        self.assertIn("doc.setBatchmode(True)", source)
        self.assertIn("doc.setBatchmode(previous_batchmode)", source)


if __name__ == "__main__":
    unittest.main()
