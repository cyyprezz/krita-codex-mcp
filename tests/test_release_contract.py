from __future__ import annotations

import unittest

from krita_codex_mcp import __version__
from scripts.verify_release import ReleaseContractError, resolve_release_tag, verify_release


class ReleaseContractTests(unittest.TestCase):
    def test_release_metadata_matches_package_version(self) -> None:
        self.assertEqual(verify_release(tag=f"v{__version__}"), __version__)

    def test_mismatched_release_tag_is_rejected(self) -> None:
        with self.assertRaisesRegex(ReleaseContractError, "does not match package version"):
            verify_release(tag="v999.0.0")

    def test_existing_matching_tag_is_accepted(self) -> None:
        self.assertEqual(
            resolve_release_tag("0.2.1", ref="refs/tags/v0.2.1"), "v0.2.1"
        )

    def test_explicit_main_release_request_is_accepted(self) -> None:
        self.assertEqual(
            resolve_release_tag(
                "0.2.1", ref="refs/heads/main", request={"version": "0.2.1"}
            ),
            "v0.2.1",
        )

    def test_main_requires_an_exact_versioned_request(self) -> None:
        for request in (None, {}, [], {"version": "0.2.0"}, {"version": "0.2.1", "auto": True}):
            with self.subTest(request=request), self.assertRaises(ReleaseContractError):
                resolve_release_tag("0.2.1", ref="refs/heads/main", request=request)

    def test_other_branches_and_pull_requests_cannot_request_releases(self) -> None:
        for ref in ("refs/heads/feature", "refs/pull/16/merge", "main", "refs/heads/main/other"):
            with self.subTest(ref=ref), self.assertRaises(ReleaseContractError):
                resolve_release_tag("0.2.1", ref=ref, request={"version": "0.2.1"})

    def test_different_tags_are_rejected(self) -> None:
        for ref in ("refs/tags/v0.2.0", "refs/tags/0.2.1", "refs/tags/v0.2.1-rc1"):
            with self.subTest(ref=ref), self.assertRaises(ReleaseContractError):
                resolve_release_tag("0.2.1", ref=ref)

    def test_invalid_versions_cannot_be_used_in_release_output(self) -> None:
        for version in ("0.2", "0.2.1\ntag=other", "0.2.1-rc1"):
            with self.subTest(version=version), self.assertRaises(ReleaseContractError):
                resolve_release_tag(
                    version, ref="refs/heads/main", request={"version": version}
                )


if __name__ == "__main__":
    unittest.main()
