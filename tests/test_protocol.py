from __future__ import annotations

import unittest

from krita_codex_protocol import (
    BridgeError,
    BridgeRequest,
    BridgeResponse,
    ProtocolError,
)


class ProtocolTests(unittest.TestCase):
    def test_request_round_trip(self) -> None:
        request = BridgeRequest(operation="state", arguments={})
        decoded = BridgeRequest.from_mapping(request.to_mapping())
        self.assertEqual(decoded, request)

    def test_error_response_round_trip(self) -> None:
        request = BridgeRequest(operation="preview", arguments={})
        response = BridgeResponse(
            request_id=request.request_id,
            error=BridgeError("NO_ACTIVE_DOCUMENT", "No document"),
        )
        decoded = BridgeResponse.from_mapping(response.to_mapping())
        self.assertFalse(decoded.ok)
        self.assertEqual(decoded.error.code, "NO_ACTIVE_DOCUMENT")

    def test_rejects_arbitrary_operation(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "unsupported operation"):
            BridgeRequest(operation="execute_python", arguments={})

    def test_response_requires_exactly_one_payload(self) -> None:
        request = BridgeRequest(operation="state", arguments={})
        with self.assertRaisesRegex(ProtocolError, "exactly one"):
            BridgeResponse(request_id=request.request_id)


if __name__ == "__main__":
    unittest.main()
