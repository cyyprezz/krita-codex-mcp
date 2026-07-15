from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_ROOT = REPO_ROOT / "src" / "krita_codex_mcp"


class MCPContractTests(unittest.TestCase):
    def test_mcp_python_sources_compile(self) -> None:
        for path in MCP_ROOT.glob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_service_facade_and_operation_modules_stay_focused(self) -> None:
        facade_lines = (MCP_ROOT / "service.py").read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(facade_lines), 20)
        for name in (
            "asset_service.py",
            "core_service.py",
            "editing_service.py",
            "layer_service.py",
            "validation.py",
        ):
            path = MCP_ROOT / name
            self.assertTrue(path.is_file(), name)
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 400, name)

    def test_operation_modules_do_not_lazily_import_service_facade(self) -> None:
        for name in ("asset_service.py", "editing_service.py", "layer_service.py"):
            source = (MCP_ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("from .service import", source, name)


if __name__ == "__main__":
    unittest.main()
