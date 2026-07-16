from __future__ import annotations

import os
import socket
from collections.abc import Iterable
from types import TracebackType

from .errors import InstallerError


class BridgePortReservation:
    """Hold deterministic loopback port reservations for a mutating installer run."""

    def __init__(self, ports: Iterable[int]) -> None:
        requested = tuple(ports)
        if not requested or any(
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1024 <= port <= 65535
            for port in requested
        ):
            raise InstallerError("The bridge port reservation request is invalid.")
        self.ports = tuple(sorted(set(requested)))
        self._sockets: list[socket.socket] = []

    def __enter__(self) -> BridgePortReservation:
        try:
            for port in self.ports:
                reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    if os.name == "nt":
                        reservation.setsockopt(
                            socket.SOL_SOCKET,
                            socket.SO_EXCLUSIVEADDRUSE,
                            1,
                        )
                    else:  # pragma: no cover - Windows is the supported target.
                        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
                    reservation.bind(("127.0.0.1", port))
                except OSError:
                    reservation.close()
                    raise
                self._sockets.append(reservation)
        except OSError as exc:
            self.close()
            ports = ", ".join(str(port) for port in self.ports)
            raise InstallerError(
                "The local Krita bridge port could not be reserved exclusively "
                f"({ports}). No installer files were changed by this run; close Krita or free the "
                "configured port and retry."
            ) from exc
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        while self._sockets:
            self._sockets.pop().close()
