from __future__ import annotations

import json
import os
import socket
import tempfile
import unittest
from collections.abc import Callable, Iterable
from pathlib import Path
from unittest.mock import patch

from krita_codex_mcp.installer.errors import InstallerError
from krita_codex_mcp.installer.manager import InstallationManager
from krita_codex_mcp.installer.paths import InstallPaths
from krita_codex_mcp.installer.port_reservation import BridgePortReservation
from krita_codex_mcp.installer.transaction import TransactionJournal


def _socket_for_exclusive_bind() -> socket.socket:
    candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:  # pragma: no cover - Windows is the supported target.
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    return candidate


def _unused_port() -> int:
    with _socket_for_exclusive_bind() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


class InstallerPortReservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.allowed = root / "allowed"
        self.allowed.mkdir()
        self.paths = InstallPaths.from_environment(
            {
                "APPDATA": str(root / "roaming"),
                "LOCALAPPDATA": str(root / "local"),
                "USERPROFILE": str(root / "profile"),
            }
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _assert_can_bind(self, port: int) -> None:
        with _socket_for_exclusive_bind() as candidate:
            candidate.bind(("127.0.0.1", port))

    def _assert_cannot_bind(self, ports: Iterable[int]) -> None:
        for port in ports:
            with (
                self.subTest(port=port),
                _socket_for_exclusive_bind() as candidate,
                self.assertRaises(OSError),
            ):
                candidate.bind(("127.0.0.1", port))

    def _run_with_remove_check(
        self,
        operation: Callable[[], None],
        ports: Iterable[int],
    ) -> None:
        expected_ports = tuple(ports)
        original_remove = TransactionJournal.remove
        checks = 0

        def checked_remove(journal: TransactionJournal) -> None:
            nonlocal checks
            self._assert_cannot_bind(expected_ports)
            checks += 1
            original_remove(journal)

        with patch.object(TransactionJournal, "remove", new=checked_remove):
            operation()
        self.assertEqual(checks, 1)

    def test_free_port_installs_even_when_connection_attempt_would_time_out(self) -> None:
        port = _unused_port()
        with patch(
            "krita_codex_mcp.installer.bridge.socket.create_connection",
            side_effect=TimeoutError("simulated Windows timeout"),
        ) as connect:
            InstallationManager(self.paths).install(
                allowed_roots=[self.allowed],
                port=port,
            )
        connect.assert_not_called()
        config = json.loads(self.paths.config_file.read_text(encoding="utf-8"))
        self.assertEqual(config["port"], port)
        self._assert_can_bind(port)

    def test_real_local_listener_blocks_reservation_and_all_managed_mutation(self) -> None:
        with _socket_for_exclusive_bind() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(InstallerError, "close Krita or free"):
                InstallationManager(self.paths).install(
                    allowed_roots=[self.allowed],
                    port=port,
                )
        self.assertFalse(self.paths.plugin_dir.exists())
        self.assertFalse(self.paths.protocol_dir.exists())
        self.assertFalse(self.paths.desktop_file.exists())
        self.assertFalse(self.paths.config_file.exists())
        self.assertFalse(self.paths.transaction_journal_file.exists())

    def test_second_bind_fails_while_exclusive_reservation_is_held(self) -> None:
        port = _unused_port()
        with BridgePortReservation([port]):
            self._assert_cannot_bind([port])
        self._assert_can_bind(port)

    def test_reservation_is_released_after_installer_success_and_error(self) -> None:
        success_port = _unused_port()
        InstallationManager(self.paths).install(
            allowed_roots=[self.allowed],
            port=success_port,
        )
        self._assert_can_bind(success_port)

        error_port = _unused_port()

        def fail_after_stage(step: str) -> None:
            if step == "after_stage":
                raise RuntimeError("injected failure")

        with self.assertRaisesRegex(InstallerError, "before any files were replaced"):
            InstallationManager(
                self.paths,
                fault_injector=fail_after_stage,
            ).install(allowed_roots=[], port=error_port)
        self._assert_can_bind(success_port)
        self._assert_can_bind(error_port)

    def test_install_update_and_uninstall_hold_port_through_transaction_cleanup(
        self,
    ) -> None:
        port = _unused_port()
        manager = InstallationManager(self.paths)
        self._run_with_remove_check(
            lambda: manager.install(allowed_roots=[self.allowed], port=port),
            [port],
        )
        self._run_with_remove_check(manager.update, [port])
        self._run_with_remove_check(manager.uninstall, [port])
        self._assert_can_bind(port)

    def test_port_change_reserves_old_and_new_ports_deterministically(self) -> None:
        first = _unused_port()
        second = _unused_port()
        while second == first:
            second = _unused_port()
        manager = InstallationManager(self.paths)
        manager.install(allowed_roots=[self.allowed], port=first)

        self.assertEqual(
            BridgePortReservation([second, first]).ports,
            tuple(sorted((first, second))),
        )
        self._run_with_remove_check(
            lambda: manager.install(allowed_roots=[], port=second),
            [first, second],
        )
        self._assert_can_bind(first)
        self._assert_can_bind(second)


if __name__ == "__main__":
    unittest.main()
