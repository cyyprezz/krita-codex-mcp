from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from krita_codex_protocol import PathPolicy, PathPolicyError


class PathPolicyTests(unittest.TestCase):
    def test_accepts_new_file_inside_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = PathPolicy([root]).validate_output(
                str(root / "image.png"), extensions={".png"}, overwrite=False
            )
            self.assertEqual(result, (root / "image.png").resolve())

    def test_rejects_prefix_collision_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "allowed"
            sibling = Path(directory) / "allowed-escape"
            root.mkdir()
            sibling.mkdir()
            with self.assertRaisesRegex(PathPolicyError, "outside"):
                PathPolicy([root]).validate_output(
                    str(sibling / "image.png"), extensions={".png"}, overwrite=False
                )

    def test_rejects_wrong_extension(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(PathPolicyError, "extension"),
        ):
            PathPolicy([Path(directory)]).validate_output(
                str(Path(directory) / "image.jpg"),
                extensions={".png"},
                overwrite=False,
            )

    def test_requires_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing.kra"
            target.write_bytes(b"existing")
            with self.assertRaisesRegex(PathPolicyError, "overwrite=true"):
                PathPolicy([Path(directory)]).validate_output(
                    str(target), extensions={".kra"}, overwrite=False
                )

    def test_accepts_existing_input_inside_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing.kra"
            target.write_bytes(b"test")
            result = PathPolicy([Path(directory)]).validate_input(str(target), extensions={".kra"})
            self.assertEqual(result, target.resolve())

    def test_accepts_new_output_directory_inside_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "hero_idle"
            result = PathPolicy([root]).validate_output_directory(
                str(target), overwrite=False
            )
            self.assertEqual(result, target.resolve())

    def test_output_directory_cannot_replace_allowed_root(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(PathPolicyError, "allowed root"),
        ):
            root = Path(directory)
            PathPolicy([root]).validate_output_directory(str(root), overwrite=True)

    def test_existing_output_directory_requires_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "hero_idle"
            target.mkdir()
            with self.assertRaisesRegex(PathPolicyError, "overwrite=true"):
                PathPolicy([root]).validate_output_directory(str(target), overwrite=False)

    def test_rejects_input_outside_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "allowed"
            root.mkdir()
            target = Path(directory) / "outside.kra"
            target.write_bytes(b"test")
            with self.assertRaisesRegex(PathPolicyError, "outside"):
                PathPolicy([root]).validate_input(str(target), extensions={".kra"})

    def test_rejects_missing_input(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(PathPolicyError, "does not exist"),
        ):
            PathPolicy([Path(directory)]).validate_input(
                str(Path(directory) / "missing.kra"), extensions={".kra"}
            )


if __name__ == "__main__":
    unittest.main()
