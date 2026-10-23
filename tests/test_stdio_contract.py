from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from krita_codex_mcp import __version__

MODERN_VERSION = "2026-07-28"
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
TOOL_NAMES = {
    "krita_state",
    "krita_preview",
    "krita_canvas",
    "krita_edit",
    "krita_document",
    "krita_layers",
    "krita_history",
    "krita_brush",
    "krita_paint_path",
    "krita_draw",
    "krita_color",
    "krita_asset",
    "krita_save_export",
}


class _Bridge:
    """Only Krita is substituted; the MCP process and HTTP bridge client are real."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: dict[str, Any] | None = None
        self.slow_started = threading.Event()
        self.slow_finished = threading.Event()
        self.release_slow = threading.Event()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_POST(self) -> None:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                bridge.calls.append({"request": request, "auth": self.headers["Authorization"]})
                if request["arguments"].get("document_id") == "slow":
                    bridge.slow_started.set()
                    bridge.release_slow.wait(timeout=15)
                response: dict[str, Any] = {
                    "protocol_version": "1",
                    "request_id": request["request_id"],
                    "ok": bridge.error is None,
                }
                if bridge.error is not None:
                    response["error"] = bridge.error
                else:
                    result = {
                        "bridge_distribution_version": __version__,
                        "bridge_protocol_version": "1",
                        "document_id": request["arguments"].get("document_id"),
                    }
                    if request["operation"] in {"preview", "asset"}:
                        result.update({"png_base64": PNG_BASE64, "width": 1, "height": 1})
                    response["result"] = result
                encoded = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                if request["arguments"].get("document_id") == "slow":
                    bridge.slow_finished.set()

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.http.server_port

    def close(self) -> None:
        self.release_slow.set()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)


class _StdioServer:
    """Exercise the installed server process without an SDK client hiding the wire."""

    def __init__(self, root: Path, port: int = 17641, *, modern: bool = True) -> None:
        self.modern = modern
        config = root / "config.json"
        config.write_text(
            json.dumps({"token": "x" * 32, "port": port, "allowed_roots": [str(root)]}),
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment["KRITA_CODEX_CONFIG"] = str(config)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "krita_codex_mcp.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.errors: list[str] = []
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.error_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self.reader.start()
        self.error_reader.start()
        self.next_id = 0
        self.pending: dict[int, dict[str, Any]] = {}

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.messages.put(json.loads(line))

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        self.errors.extend(self.process.stderr)

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.receive(self.start_request(method, params))

    def start_request(self, method: str, params: dict[str, Any]) -> int:
        self.next_id += 1
        if self.modern:
            params = {
                **params,
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            }
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        return self.next_id

    def receive(self, request_id: int) -> dict[str, Any]:
        message = self.poll(request_id, timeout=20)
        if message is None:
            raise AssertionError("MCP server did not respond: " + "".join(self.errors))
        return message

    def poll(self, request_id: int, *, timeout: float) -> dict[str, Any] | None:
        if request_id in self.pending:
            return self.pending.pop(request_id)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                return None
            if message.get("id") == request_id:
                return message
            if isinstance(message.get("id"), int):
                self.pending[message["id"]] = message

    def connect(self) -> dict[str, Any]:
        if self.modern:
            return self.request("server/discover", {})
        response = self.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "krita-contract-test", "version": "1"},
            },
        )
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return response

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.reader.join(timeout=5)
        self.error_reader.join(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


class StdioContractTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bridge = _Bridge()
        self.addCleanup(self.bridge.close)

    def test_modern_client_can_discover_without_legacy_initialize(self) -> None:
        with closing(_StdioServer(self.root)) as server:
            response = server.connect()
            self.assertIn("result", response, response)
            self.assertIn(MODERN_VERSION, response["result"]["supportedVersions"])
            self.assertIn("tools", response["result"]["capabilities"])

    def test_both_protocols_preserve_tool_schemas_and_approval_hints(self) -> None:
        for modern in (True, False):
            with (
                self.subTest(modern=modern),
                closing(_StdioServer(self.root, self.bridge.port, modern=modern)) as server,
            ):
                connected = server.connect()["result"]
                if not modern:
                    self.assertEqual(connected["protocolVersion"], "2025-11-25")
                    self.assertEqual(connected["serverInfo"]["version"], __version__)
                response = server.request("tools/list", {})
                tools = {tool["name"]: tool for tool in response["result"]["tools"]}
                self.assertEqual(set(tools), TOOL_NAMES)
                for name, tool in tools.items():
                    hints = tool["annotations"]
                    self.assertFalse(hints["openWorldHint"], name)
                    read_only = name in {"krita_state", "krita_preview"}
                    self.assertEqual(hints["readOnlyHint"], read_only, name)
                    if not read_only:
                        destructive = name not in {"krita_brush", "krita_paint_path", "krita_draw"}
                        self.assertEqual(hints["destructiveHint"], destructive, name)
                preview = tools["krita_preview"]["inputSchema"]["properties"]["max_width"]
                self.assertEqual((preview["minimum"], preview["maximum"]), (64, 4096))
                self.assertEqual(
                    tools["krita_asset"]["inputSchema"]["required"], ["layer_id", "output_path"]
                )
                self.assertEqual(self.bridge.calls, [])

    def test_both_protocols_return_png_blocks_and_metadata_without_duplicate_base64(self) -> None:
        for modern in (True, False):
            with (
                self.subTest(modern=modern),
                closing(_StdioServer(self.root, self.bridge.port, modern=modern)) as server,
            ):
                server.connect()
                for name, arguments in (
                    ("krita_preview", {"max_width": 64, "max_height": 64}),
                    (
                        "krita_asset",
                        {"layer_id": "layer-1", "output_path": str(self.root / "frame.png")},
                    ),
                ):
                    response = server.request("tools/call", {"name": name, "arguments": arguments})
                    self.assertIn("result", response, response)
                    result = response["result"]
                    self.assertFalse(result.get("isError"), result)
                    self.assertEqual([c["type"] for c in result["content"]], ["text", "image"])
                    metadata = json.loads(result["content"][0]["text"])
                    self.assertEqual((metadata["width"], metadata["height"]), (1, 1))
                    self.assertNotIn("png_base64", metadata)
                    image = result["content"][1]
                    self.assertEqual(image["mimeType"], "image/png")
                    self.assertEqual(base64.b64decode(image["data"]), base64.b64decode(PNG_BASE64))
                    self.assertIsNone(result.get("structuredContent"))
                self.assertTrue(all(c["auth"] == "Bearer " + "x" * 32 for c in self.bridge.calls))

    def test_invalid_arguments_and_escaped_paths_do_not_reach_krita(self) -> None:
        for modern in (True, False):
            with (
                self.subTest(modern=modern),
                closing(_StdioServer(self.root, self.bridge.port, modern=modern)) as server,
            ):
                server.connect()
                for name, arguments in (
                    ("krita_preview", {"max_width": 1}),
                    ("krita_state", {"document_id": ""}),
                    ("krita_save_export", {"png_path": str(self.root.parent / "escape.png")}),
                ):
                    response = server.request("tools/call", {"name": name, "arguments": arguments})
                    self.assertIn("result", response, response)
                    self.assertTrue(response["result"]["isError"], response)
                    if name != "krita_preview":
                        text = response["result"]["content"][0]["text"]
                        error = json.loads(text[text.index("{") :])
                        self.assertEqual(error["code"], "INVALID_ARGUMENT")
                        self.assertFalse(error["retryable"])
                self.assertEqual(self.bridge.calls, [])

    def test_busy_and_timeout_remain_actionable_tool_errors(self) -> None:
        for modern in (True, False):
            with (
                self.subTest(modern=modern),
                closing(_StdioServer(self.root, self.bridge.port, modern=modern)) as server,
            ):
                server.connect()
                for code in ("BRIDGE_BUSY", "QT_TIMEOUT"):
                    error = {
                        "code": code,
                        "message": "Try again after checking state",
                        "details": {"operation": "state", "timeout_seconds": 30},
                        "retryable": True,
                    }
                    self.bridge.error = error
                    response = server.request(
                        "tools/call", {"name": "krita_state", "arguments": {}}
                    )
                    self.assertIn("result", response, response)
                    result = response["result"]
                    self.assertTrue(result["isError"], result)
                    text = result["content"][0]["text"]
                    self.assertIn(code, text)
                    self.assertEqual(json.loads(text[text.index("{") :]), error)

    def test_concurrent_calls_keep_request_ids_and_results_separate(self) -> None:
        for modern in (True, False):
            self.bridge.calls.clear()
            self.bridge.slow_started.clear()
            self.bridge.slow_finished.clear()
            self.bridge.release_slow.clear()
            with (
                self.subTest(modern=modern),
                closing(_StdioServer(self.root, self.bridge.port, modern=modern)) as server,
            ):
                server.connect()
                slow_id = server.start_request(
                    "tools/call",
                    {"name": "krita_state", "arguments": {"document_id": "slow"}},
                )
                self.assertTrue(self.bridge.slow_started.wait(timeout=10))
                try:
                    fast = server.request(
                        "tools/call", {"name": "krita_state", "arguments": {"document_id": "fast"}}
                    )
                    self.assertFalse(self.bridge.slow_finished.is_set(), "Calls were serialized")
                    self.assertFalse(fast["result"].get("isError"), fast)
                    self.assertEqual(fast["result"]["structuredContent"]["document_id"], "fast")
                finally:
                    self.bridge.release_slow.set()
                slow = server.receive(slow_id)
                self.assertEqual(slow["result"]["structuredContent"]["document_id"], "slow")
                ids = [call["request"]["request_id"] for call in self.bridge.calls]
                self.assertEqual(len(set(ids)), 2)

    def test_cancelled_call_does_not_break_following_requests(self) -> None:
        for modern in (True, False):
            with (
                self.subTest(modern=modern),
                closing(_StdioServer(self.root, self.bridge.port, modern=modern)) as server,
            ):
                server.connect()
                for cancel in (False, True):
                    self.bridge.slow_started.clear()
                    self.bridge.slow_finished.clear()
                    self.bridge.release_slow.clear()
                    slow_id = server.start_request(
                        "tools/call",
                        {"name": "krita_state", "arguments": {"document_id": "slow"}},
                    )
                    self.assertTrue(self.bridge.slow_started.wait(timeout=10))
                    try:
                        if cancel:
                            params: dict[str, Any] = {
                                "requestId": slow_id,
                                "reason": "Test cancelled",
                            }
                            if modern:
                                params["_meta"] = {
                                    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
                                    "io.modelcontextprotocol/clientCapabilities": {},
                                }
                            server.send(
                                {
                                    "jsonrpc": "2.0",
                                    "method": "notifications/cancelled",
                                    "params": params,
                                }
                            )
                        response = server.request(
                            "tools/call",
                            {
                                "name": "krita_state",
                                "arguments": {"document_id": "after-cancel"},
                            },
                        )
                        result = response["result"]
                        self.assertFalse(result.get("isError"), result)
                        self.assertEqual(result["structuredContent"]["document_id"], "after-cancel")
                    finally:
                        # Cancelling MCP cannot undo an operation already dispatched to Krita.
                        self.bridge.release_slow.set()
                    self.assertTrue(self.bridge.slow_finished.wait(timeout=10))
                    if cancel:
                        self.assertIsNone(server.poll(slow_id, timeout=1), "Cancelled call replied")
                    else:
                        control = server.receive(slow_id)["result"]["structuredContent"]
                        self.assertEqual(control["document_id"], "slow")
                    tools = server.request("tools/list", {})["result"]["tools"]
                    self.assertEqual({tool["name"] for tool in tools}, TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
