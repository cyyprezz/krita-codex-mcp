from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from typing import Any
from uuid import uuid4

from krita_codex_protocol import BridgeConfig, BridgeError, BridgeResponse

REPO_ROOT = Path(__file__).resolve().parents[1]
HTTP_SERVER_PATH = REPO_ROOT / "krita-plugin" / "kritacodexbridge" / "http_server.py"
MODULE_NAME = "_krita_codex_http_server_test"

spec = importlib.util.spec_from_file_location(MODULE_NAME, HTTP_SERVER_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot load {HTTP_SERVER_PATH}")
http_server = importlib.util.module_from_spec(spec)
sys.modules[MODULE_NAME] = http_server
spec.loader.exec_module(http_server)


class _FakeDispatcher:
    def __init__(self, callback=None) -> None:
        self.callback = callback
        self.calls: list[tuple[Any, float]] = []

    def call(self, request, timeout_seconds: float):
        self.calls.append((request, timeout_seconds))
        if self.callback is not None:
            return self.callback(request)
        return http_server.DispatchResult(
            BridgeResponse(request_id=request.request_id, result={"operation": request.operation})
        )


class HTTPServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.servers: list[tuple[Any, threading.Thread]] = []

    def tearDown(self) -> None:
        for server, thread in self.servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)
        self.temp.cleanup()

    def _start_server(self, dispatcher, **overrides):
        values = {
            "token": "t" * 32,
            "allowed_roots": (Path(self.temp.name),),
            "port": 0,
            "request_timeout_seconds": 0.1,
            "max_request_bytes": 4096,
            "max_response_bytes": 4096,
        }
        values.update(overrides)
        config = BridgeConfig(**values)
        server = http_server.BridgeHTTPServer(config, dispatcher)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return server

    def _request(
        self,
        server,
        body: bytes,
        *,
        token: str = "t" * 32,
        content_type: str = "application/json",
        path: str = "/v1/execute",
    ) -> tuple[int, dict[str, Any]]:
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=2.0)
        connection.request(
            "POST",
            path,
            body=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": content_type,
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        status = response.status
        connection.close()
        return status, payload

    def _body(self, request_id: str | None = None) -> bytes:
        return json.dumps(
            {
                "protocol_version": "1",
                "request_id": request_id or str(uuid4()),
                "operation": "state",
                "arguments": {},
            }
        ).encode("utf-8")

    def test_authenticated_request_returns_matching_structured_response(self) -> None:
        dispatcher = _FakeDispatcher()
        server = self._start_server(dispatcher)
        request_id = str(uuid4())

        status, payload = self._request(server, self._body(request_id))

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["result"], {"operation": "state"})
        self.assertEqual(len(dispatcher.calls), 1)

    def test_wrong_token_is_rejected_before_dispatch(self) -> None:
        dispatcher = _FakeDispatcher()
        server = self._start_server(dispatcher)

        status, payload = self._request(server, self._body(), token="wrong")

        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "UNAUTHORIZED")
        self.assertEqual(dispatcher.calls, [])

    def test_invalid_json_and_content_type_are_rejected(self) -> None:
        dispatcher = _FakeDispatcher()
        server = self._start_server(dispatcher)

        invalid_status, invalid_payload = self._request(server, b"{")
        type_status, type_payload = self._request(server, self._body(), content_type="text/plain")

        self.assertEqual(invalid_status, 400)
        self.assertEqual(invalid_payload["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(type_status, 415)
        self.assertEqual(type_payload["error"]["code"], "UNSUPPORTED_MEDIA_TYPE")
        self.assertEqual(dispatcher.calls, [])

    def test_oversized_request_is_rejected(self) -> None:
        dispatcher = _FakeDispatcher()
        server = self._start_server(dispatcher, max_request_bytes=32)

        status, payload = self._request(server, self._body())

        self.assertEqual(status, 413)
        self.assertEqual(payload["error"]["code"], "REQUEST_TOO_LARGE")
        self.assertEqual(dispatcher.calls, [])

    def test_oversized_response_becomes_structured_error(self) -> None:
        def large_response(request):
            return http_server.DispatchResult(
                BridgeResponse(request_id=request.request_id, result={"data": "x" * 1024})
            )

        dispatcher = _FakeDispatcher(large_response)
        server = self._start_server(dispatcher, max_response_bytes=256)

        status, payload = self._request(server, self._body())

        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "RESPONSE_TOO_LARGE")

    def test_running_timeout_retains_lock_until_completion(self) -> None:
        completion = threading.Event()

        def first_times_out(request):
            if len(dispatcher.calls) == 1:
                response = BridgeResponse(
                    request_id=request.request_id,
                    error=BridgeError(
                        "BRIDGE_TIMEOUT",
                        "Timed out while waiting for Krita",
                        {"operation_may_complete": True},
                        retryable=True,
                    ),
                )
                return http_server.DispatchResult(response, completion)
            return http_server.DispatchResult(
                BridgeResponse(request_id=request.request_id, result={"done": True})
            )

        dispatcher = _FakeDispatcher(first_times_out)
        server = self._start_server(dispatcher, request_timeout_seconds=0.05)

        first_status, first_payload = self._request(server, self._body())
        busy_status, busy_payload = self._request(server, self._body())
        completion.set()
        final_status, final_payload = self._request(server, self._body())

        self.assertEqual(first_status, 200)
        self.assertEqual(first_payload["error"]["code"], "BRIDGE_TIMEOUT")
        self.assertEqual(busy_status, 200)
        self.assertEqual(busy_payload["error"]["code"], "BRIDGE_BUSY")
        self.assertEqual(final_status, 200)
        self.assertTrue(final_payload["ok"])
        self.assertEqual(len(dispatcher.calls), 2)


if __name__ == "__main__":
    unittest.main()
