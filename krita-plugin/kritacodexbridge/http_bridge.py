from __future__ import annotations

import threading

from PyQt5.QtCore import QObject, Qt, pyqtSignal, pyqtSlot

from krita_codex_protocol import (
    BridgeConfig,
    BridgeError,
    BridgeRequest,
    BridgeResponse,
)

from .errors import KritaOperationError
from .http_server import BridgeHTTPServer, DispatchResult
from .service import KritaService


class _DispatchEnvelope:
    def __init__(self, request: BridgeRequest) -> None:
        self.request = request
        self.done = threading.Event()
        self._lock = threading.Lock()
        self._state = "pending"
        self.response: BridgeResponse | None = None

    def begin(self) -> bool:
        with self._lock:
            if self._state != "pending":
                return False
            self._state = "running"
            return True

    def complete(self, response: BridgeResponse) -> None:
        with self._lock:
            self.response = response
            self._state = "done"
            self.done.set()

    def cancel_or_observe(self) -> tuple[str, BridgeResponse | None]:
        with self._lock:
            if self._state == "pending":
                self._state = "cancelled"
                self.done.set()
            return self._state, self.response


class MainThreadDispatcher(QObject):
    requested = pyqtSignal(object)

    def __init__(self, service: KritaService) -> None:
        super().__init__()
        self.service = service
        self.requested.connect(self._execute, Qt.QueuedConnection)

    def call(self, request: BridgeRequest, timeout_seconds: float) -> DispatchResult:
        envelope = _DispatchEnvelope(request)
        self.requested.emit(envelope)
        if envelope.done.wait(timeout_seconds):
            return DispatchResult(self._response_or_internal_error(envelope))

        state, response = envelope.cancel_or_observe()
        if state == "done":
            return DispatchResult(response or self._internal_error(request))
        operation_may_complete = state == "running"
        timeout_response = BridgeResponse(
            request_id=request.request_id,
            error=BridgeError(
                "BRIDGE_TIMEOUT",
                "Timed out while waiting for Krita",
                {"operation_may_complete": operation_may_complete},
                retryable=True,
            ),
        )
        return DispatchResult(
            timeout_response,
            completion=envelope.done if operation_may_complete else None,
        )

    @pyqtSlot(object)
    def _execute(self, envelope: _DispatchEnvelope) -> None:
        if not envelope.begin():
            return
        request = envelope.request
        try:
            result = self.service.execute(request.operation, request.arguments)
            response = BridgeResponse(request_id=request.request_id, result=result)
        except KritaOperationError as exc:
            response = BridgeResponse(
                request_id=request.request_id,
                error=BridgeError(exc.code, exc.message, exc.details, exc.retryable),
            )
        except Exception as exc:
            response = BridgeResponse(
                request_id=request.request_id,
                error=BridgeError(
                    "INTERNAL_ERROR",
                    "Unexpected error inside the Krita bridge",
                    {"type": type(exc).__name__, "reason": str(exc)},
                ),
            )
        envelope.complete(response)

    def _response_or_internal_error(self, envelope: _DispatchEnvelope) -> BridgeResponse:
        return envelope.response or self._internal_error(envelope.request)

    def _internal_error(self, request: BridgeRequest) -> BridgeResponse:
        return BridgeResponse(
            request_id=request.request_id,
            error=BridgeError("INTERNAL_ERROR", "Krita dispatcher returned no response"),
        )


class BridgeRuntime:
    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.service = KritaService(config)
        self.dispatcher = MainThreadDispatcher(self.service)
        self.server = BridgeHTTPServer(config, self.dispatcher)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.25},
            name="krita-codex-bridge",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)
