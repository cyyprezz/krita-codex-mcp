from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

KRITA_DOCUMENT_EXTENSIONS = frozenset(
    {".kra", ".ora", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".psd"}
)


class PathPolicyError(ValueError):
    """Raised when a requested filesystem path leaves the configured roots."""


class PathPolicy:
    def __init__(self, allowed_roots: Iterable[Path]) -> None:
        self.allowed_roots = tuple(Path(root).resolve(strict=False) for root in allowed_roots)
        if not self.allowed_roots:
            raise PathPolicyError("at least one allowed root is required")

    def validate_output(
        self,
        value: str,
        *,
        extensions: set[str],
        overwrite: bool,
        require_parent: bool = True,
    ) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise PathPolicyError("output path must be a non-empty string")
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise PathPolicyError("output path must be absolute")
        resolved = candidate.resolve(strict=False)
        if resolved.suffix.lower() not in {item.lower() for item in extensions}:
            allowed = ", ".join(sorted(extensions))
            raise PathPolicyError(f"output extension must be one of: {allowed}")
        if not self._is_allowed(resolved):
            raise PathPolicyError("output path is outside the configured allowed roots")
        if require_parent and not resolved.parent.is_dir():
            raise PathPolicyError("output parent directory does not exist")
        if resolved.exists() and not overwrite:
            raise PathPolicyError("output already exists; pass overwrite=true to replace it")
        return resolved

    def validate_input(self, value: str, *, extensions: set[str]) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise PathPolicyError("input path must be a non-empty string")
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise PathPolicyError("input path must be absolute")
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PathPolicyError("input file does not exist or cannot be resolved") from exc
        if resolved.suffix.lower() not in {item.lower() for item in extensions}:
            allowed = ", ".join(sorted(extensions))
            raise PathPolicyError(f"input extension must be one of: {allowed}")
        if not self._is_allowed(resolved):
            raise PathPolicyError("input path is outside the configured allowed roots")
        if not resolved.is_file():
            raise PathPolicyError("input path must identify a file")
        return resolved

    def _is_allowed(self, candidate: Path) -> bool:
        candidate_key = os.path.normcase(str(candidate))
        for root in self.allowed_roots:
            root_key = os.path.normcase(str(root))
            try:
                if os.path.commonpath((candidate_key, root_key)) == root_key:
                    return True
            except ValueError:
                continue
        return False
