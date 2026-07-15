from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from krita_codex_mcp.bridge_client import BridgeCallError, BridgeClient
from krita_codex_protocol import BridgeConfig, BridgeResponse


class _FakeHTTPResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


class BridgeClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = BridgeConfig.from_mapping(
            {"token": "s" * 32, "allowed_roots": [self.temp.name]}
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_sends_bearer_token_and_checks_request_id(self) -> None:
        observed = {}

        def fake_urlopen(request, timeout):
            observed["authorization"] = request.headers["Authorization"]
            observed["timeout"] = timeout
            raw = json.loads(request.data.decode("utf-8"))
            response = BridgeResponse(request_id=raw["request_id"], result={"ready": True})
            return _FakeHTTPResponse(json.dumps(response.to_mapping()).encode("utf-8"))

        client = BridgeClient(self.config)
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = client._execute_sync(
                __import__("krita_codex_protocol").BridgeRequest(operation="state", arguments={})
            )
        self.assertEqual(result, {"ready": True})
        self.assertEqual(observed["authorization"], f"Bearer {self.config.token}")
        self.assertEqual(observed["timeout"], self.config.request_timeout_seconds + 2.0)

    def test_rejects_mismatched_response(self) -> None:
        from krita_codex_protocol import BridgeRequest

        request = BridgeRequest(operation="state", arguments={})
        other = BridgeRequest(operation="state", arguments={})
        response = BridgeResponse(request_id=other.request_id, result={})
        client = BridgeClient(self.config)
        with (
            patch(
                "urllib.request.urlopen",
                return_value=_FakeHTTPResponse(json.dumps(response.to_mapping()).encode("utf-8")),
            ),
            self.assertRaisesRegex(BridgeCallError, "RESPONSE_MISMATCH"),
        ):
            client._execute_sync(request)

    def test_preserves_structured_http_authentication_error(self) -> None:
        from krita_codex_protocol import BridgeRequest

        request = BridgeRequest(operation="state", arguments={})
        body = json.dumps(
            {"ok": False, "error": {"code": "UNAUTHORIZED", "message": "Invalid token"}}
        ).encode("utf-8")
        error = urllib.error.HTTPError(
            self.config.execute_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(body),
        )
        client = BridgeClient(self.config)
        with (
            patch("urllib.request.urlopen", side_effect=error),
            self.assertRaises(BridgeCallError) as raised,
        ):
            client._execute_sync(request)
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")
        self.assertEqual(raised.exception.details["http_status"], 401)


if __name__ == "__main__":
    unittest.main()
