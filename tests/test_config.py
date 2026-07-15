from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from krita_codex_protocol import BridgeConfig, ConfigError, default_config_path


class BridgeConfigTests(unittest.TestCase):
    def test_accepts_safe_loopback_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = BridgeConfig.from_mapping(
                {"token": "x" * 32, "allowed_roots": [directory], "port": 17641}
            )
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.execute_url, "http://127.0.0.1:17641/v1/execute")

    def test_rejects_non_loopback_bind(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ConfigError, "127.0.0.1"),
        ):
            BridgeConfig.from_mapping(
                {
                    "token": "x" * 32,
                    "allowed_roots": [directory],
                    "host": "0.0.0.0",
                }
            )

    def test_rejects_relative_allowed_root(self) -> None:
        with self.assertRaisesRegex(ConfigError, "absolute"):
            BridgeConfig.from_mapping({"token": "x" * 32, "allowed_roots": [str(Path("relative"))]})

    def test_rejects_short_token(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ConfigError, "at least 32"),
        ):
            BridgeConfig.from_mapping({"token": "too-short", "allowed_roots": [directory]})

    def test_rejects_relative_config_override(self) -> None:
        with (
            patch.dict("os.environ", {"KRITA_CODEX_CONFIG": "relative.json"}),
            self.assertRaisesRegex(ConfigError, "absolute"),
        ):
            default_config_path()


if __name__ == "__main__":
    unittest.main()
