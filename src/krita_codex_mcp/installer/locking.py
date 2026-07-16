from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType

from .errors import InstallerError

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - Windows is the supported installation target.
    import fcntl


class InstallerLock:
    """Non-blocking process lock whose ownership follows the open file handle."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def __enter__(self) -> InstallerLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - Windows is the supported installation target.
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise InstallerError(
                "Another Krita Codex installer process is already running. "
                "Wait for it to finish and retry."
            ) from exc
        self._handle = handle
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - Windows is the supported installation target.
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
