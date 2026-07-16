from __future__ import annotations

import unittest

from krita_codex_mcp import __version__
from scripts.verify_release import ReleaseContractError, verify_release


class ReleaseContractTests(unittest.TestCase):
    def test_release_metadata_matches_package_version(self) -> None:
        self.assertEqual(verify_release(tag=f"v{__version__}"), __version__)

    def test_mismatched_release_tag_is_rejected(self) -> None:
        with self.assertRaisesRegex(ReleaseContractError, "does not match package version"):
            verify_release(tag="v999.0.0")


if __name__ == "__main__":
    unittest.main()
