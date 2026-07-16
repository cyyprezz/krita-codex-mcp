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
        self.assertEqual(config.asset_max_bytes, 67_108_864)
        self.assertEqual(config.asset_batch_max_bytes, 268_435_456)
        self.assertEqual(config.asset_max_frames, 256)
        self.assertEqual(config.asset_render_max_pixels, 134_217_728)
        self.assertEqual(config.timeout_for("state", {}), 30.0)
        self.assertEqual(config.timeout_for("document", {"action": "open"}), 120.0)
        self.assertEqual(config.timeout_for("document", {"action": "save"}), 120.0)
        self.assertEqual(
            config.timeout_for("asset", {"action": "export_animation"}), 180.0
        )
        self.assertEqual(config.timeout_for("save_export", {}), 180.0)

    def test_rejects_timeout_classes_in_the_wrong_order(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ConfigError, "open_save_timeout_seconds"),
        ):
            BridgeConfig.from_mapping(
                {
                    "token": "x" * 32,
                    "allowed_roots": [directory],
                    "request_timeout_seconds": 60,
                    "open_save_timeout_seconds": 30,
                }
            )

    def test_enforces_minimum_long_operation_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, "open_save_timeout_seconds"):
                BridgeConfig.from_mapping(
                    {
                        "token": "x" * 32,
                        "allowed_roots": [directory],
                        "open_save_timeout_seconds": 119,
                    }
                )
            with self.assertRaisesRegex(ConfigError, "export_timeout_seconds"):
                BridgeConfig.from_mapping(
                    {
                        "token": "x" * 32,
                        "allowed_roots": [directory],
                        "export_timeout_seconds": 179,
                    }
                )

    def test_rejects_excessive_asset_byte_limit(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ConfigError, "asset_max_bytes"),
        ):
            BridgeConfig.from_mapping(
                {
                    "token": "x" * 32,
                    "allowed_roots": [directory],
                    "asset_max_bytes": 268_435_457,
                }
            )

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
