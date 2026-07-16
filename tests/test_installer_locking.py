from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from krita_codex_mcp.installer.errors import InstallerError
from krita_codex_mcp.installer.locking import InstallerLock


class InstallerLockTests(unittest.TestCase):
    def test_real_second_process_fails_fast_and_process_exit_releases_lock(self) -> None:
        child_code = "\n".join(
            [
                "import os, sys",
                "from pathlib import Path",
                "from krita_codex_mcp.installer.locking import InstallerLock",
                "with InstallerLock(Path(sys.argv[1])):",
                "    print('locked', flush=True)",
                "    sys.stdin.readline()",
                "    os._exit(0)",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / ".installer.lock"
            child = subprocess.Popen(
                [sys.executable, "-c", child_code, str(lock_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                assert child.stdout is not None
                self.assertEqual(child.stdout.readline().strip(), "locked")
                with (
                    self.assertRaisesRegex(InstallerError, "already running"),
                    InstallerLock(lock_path),
                ):
                    self.fail("second process unexpectedly acquired installer lock")
                assert child.stdin is not None
                child.stdin.write("exit\n")
                child.stdin.flush()
                child.wait(timeout=10)
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=10)
                for stream in (child.stdin, child.stdout, child.stderr):
                    if stream is not None:
                        stream.close()

            with InstallerLock(lock_path):
                pass


if __name__ == "__main__":
    unittest.main()
