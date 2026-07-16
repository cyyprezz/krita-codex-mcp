from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from krita_codex_mcp.bridge_client import BridgeCallError
from krita_codex_mcp.transform_checkpoint import checkpoint_reopen_transform
from krita_codex_protocol import PathPolicy


class _FakeClient:
    def __init__(self, path: Path, *, missing_after: bool = False, fail_open: bool = False):
        self.path = path
        self.missing_after = missing_after
        self.fail_open = fail_open
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self, operation: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        copied = dict(arguments)
        self.calls.append((operation, copied))
        if operation == "layers" and copied["action"] == "read":
            after_reopen = copied["document_id"] == "new"
            layer_id = "missing" if after_reopen and self.missing_after else "layer-1"
            return {
                "document_file_name": str(self.path),
                "layers": [{"id": layer_id, "children": []}],
            }
        if operation == "document" and copied["action"] == "open":
            if self.fail_open:
                raise BridgeCallError("DOCUMENT_OPEN_FAILED", "boom", {})
            return {"affected_document_id": "new"}
        if operation == "layers":
            return {
                "document_id": copied["document_id"],
                "affected_layer_id": copied["layer_id"],
                "action": copied["action"],
            }
        return {"ok": True}


class TransformCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "asset.kra"
        self.path.write_bytes(b"test")
        self.paths = PathPolicy((self.root,))
        self.arguments = {
            "action": "rotate",
            "document_id": "old",
            "layer_id": "layer-1",
            "angle_degrees": 5.0,
            "transform_mode": "direct",
        }

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    @patch("krita_codex_mcp.transform_checkpoint.asyncio.sleep", new_callable=AsyncMock)
    async def test_yields_between_distinct_save_close_open_and_transform_calls(
        self, sleep: AsyncMock
    ) -> None:
        client = _FakeClient(self.path)

        result = await checkpoint_reopen_transform(client, self.paths, self.arguments)

        self.assertEqual(
            [(operation, arguments["action"]) for operation, arguments in client.calls],
            [
                ("layers", "read"),
                ("document", "save"),
                ("document", "close"),
                ("document", "open"),
                ("layers", "read"),
                ("layers", "rotate"),
            ],
        )
        self.assertEqual(client.calls[-1][1]["document_id"], "new")
        self.assertNotIn("checkpoint_reopen", client.calls[-1][1])
        self.assertEqual(sleep.await_count, 5)
        self.assertEqual(result["document_id"], "new")
        self.assertTrue(result["checkpoint_reopen"]["document_reopened"])
        self.assertEqual(result["checkpoint_reopen"]["previous_document_id"], "old")

    @patch("krita_codex_mcp.transform_checkpoint.asyncio.sleep", new_callable=AsyncMock)
    async def test_open_failure_reports_saved_closed_recovery_path(
        self, _sleep: AsyncMock
    ) -> None:
        client = _FakeClient(self.path, fail_open=True)

        with self.assertRaises(BridgeCallError) as raised:
            await checkpoint_reopen_transform(client, self.paths, self.arguments)

        self.assertEqual(raised.exception.code, "DOCUMENT_REOPEN_FAILED")
        self.assertTrue(raised.exception.details["document_closed"])
        self.assertEqual(raised.exception.details["path"], str(self.path))

    @patch("krita_codex_mcp.transform_checkpoint.asyncio.sleep", new_callable=AsyncMock)
    async def test_missing_stable_uuid_fails_before_transform(self, _sleep: AsyncMock) -> None:
        client = _FakeClient(self.path, missing_after=True)

        with self.assertRaises(BridgeCallError) as raised:
            await checkpoint_reopen_transform(client, self.paths, self.arguments)

        self.assertEqual(raised.exception.code, "LAYER_REMAP_FAILED")
        action_calls = [
            (operation, arguments["action"]) for operation, arguments in client.calls
        ]
        self.assertNotIn(("layers", "rotate"), action_calls)


if __name__ == "__main__":
    unittest.main()
