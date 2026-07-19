from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "krita-codex-mcp"


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class PluginDistributionTests(unittest.TestCase):
    def test_marketplace_points_at_matching_plugin(self) -> None:
        marketplace = load_json(ROOT / ".agents" / "plugins" / "marketplace.json")
        self.assertEqual(marketplace["name"], "krita-codex")
        self.assertEqual(marketplace["interface"], {"displayName": "Krita Codex"})

        entries = marketplace["plugins"]
        self.assertIsInstance(entries, list)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["name"], "krita-codex-mcp")
        self.assertEqual(
            entry["source"],
            {"source": "local", "path": "./plugins/krita-codex-mcp"},
        )
        self.assertEqual(
            entry["policy"],
            {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        )
        self.assertEqual(entry["category"], "Productivity")

    def test_plugin_mcp_pin_matches_registry_release(self) -> None:
        registry = load_json(ROOT / "server.json")
        manifest = load_json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")
        mcp_config = load_json(PLUGIN_ROOT / ".mcp.json")

        self.assertEqual(manifest["name"], PLUGIN_ROOT.name)
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertRegex(str(manifest["version"]), r"^\d+\.\d+\.\d+$")

        server = mcp_config["mcpServers"]["krita"]
        self.assertEqual(server["command"], "uvx")
        self.assertEqual(
            server["args"],
            [
                "--from",
                f"krita-codex-mcp=={registry['version']}",
                "krita-codex-mcp",
            ],
        )
        self.assertEqual(server["startup_timeout_sec"], 60)
        self.assertEqual(server["tool_timeout_sec"], 210)

    def test_bundled_skill_is_complete(self) -> None:
        skill = (PLUGIN_ROOT / "skills" / "krita-control" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("[TODO:", skill)
        self.assertRegex(skill, re.compile(r"^---\s+name: krita-control\s+description:", re.S))
        self.assertIn("krita_state", skill)
        self.assertIn("krita_preview", skill)
        self.assertIn("operation_may_complete=true", skill)


if __name__ == "__main__":
    unittest.main()
