from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_NAME = "io.github.cyyprezz/krita-codex-mcp"
MCP_MARKER = f"mcp-name: {MCP_NAME}"
SCHEMA_URL = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
VERSION_PATTERN = re.compile(r'__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')


class ReleaseContractError(RuntimeError):
    """Raised when versioned release metadata cannot be published safely."""


def _version(repo_root: Path) -> str:
    source = (repo_root / "src" / "krita_codex_mcp" / "__init__.py").read_text(
        encoding="utf-8"
    )
    match = VERSION_PATTERN.search(source)
    if match is None:
        raise ReleaseContractError("Cannot determine a stable semantic package version")
    return match.group(1)


def resolve_release_tag(version: str, *, ref: str, request: object = None) -> str:
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise ReleaseContractError("Release requests require a stable semantic version")
    tag = f"v{version}"
    if ref == f"refs/tags/{tag}":
        return tag
    if ref == "refs/heads/main" and request == {"version": version}:
        return tag
    raise ReleaseContractError(
        "Release requires a matching tag or an exact versioned request on main"
    )


def verify_release(repo_root: Path = REPO_ROOT, *, tag: str | None = None) -> str:
    version = _version(repo_root)
    expected_tag = f"v{version}"
    if tag is not None and tag != expected_tag:
        raise ReleaseContractError(
            f"Release tag {tag!r} does not match package version {version!r}"
        )

    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    if MCP_MARKER not in readme:
        raise ReleaseContractError("README.md is missing the MCP Registry ownership marker")

    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    if project["project"]["name"] != "krita-codex-mcp":
        raise ReleaseContractError("The PyPI project name changed unexpectedly")
    if project["project"]["readme"] != "README.md":
        raise ReleaseContractError("PyPI must publish README.md as its package description")
    sdist_includes = project["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    if "/server.json" not in sdist_includes:
        raise ReleaseContractError("The source distribution is missing server.json")

    server = json.loads((repo_root / "server.json").read_text(encoding="utf-8"))
    if server.get("$schema") != SCHEMA_URL:
        raise ReleaseContractError("server.json uses an unexpected schema version")
    if server.get("name") != MCP_NAME:
        raise ReleaseContractError("server.json and the README ownership marker do not match")
    if server.get("version") != version:
        raise ReleaseContractError("server.json version does not match the Python package")
    description = server.get("description")
    if not isinstance(description, str) or not 1 <= len(description) <= 100:
        raise ReleaseContractError("server.json description exceeds the Registry limit")
    if server.get("repository") != {
        "url": "https://github.com/cyyprezz/krita-codex-mcp",
        "source": "github",
    }:
        raise ReleaseContractError("server.json repository ownership metadata is invalid")
    packages = server.get("packages")
    if packages != [
        {
            "registryType": "pypi",
            "identifier": "krita-codex-mcp",
            "version": version,
            "runtimeHint": "uvx",
            "transport": {"type": "stdio"},
        }
    ]:
        raise ReleaseContractError("server.json does not describe the exact PyPI STDIO package")

    workflow = (repo_root / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    required_workflow_fragments = (
        '"v[0-9]+.[0-9]+.[0-9]+"',
        "persist-credentials: false",
        "git merge-base --is-ancestor",
        "python scripts/verify_release.py --tag",
        "environment:\n      name: pypi",
        "id-token: write",
        "pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086",
        "attestations: true",
        "needs: [publish-pypi, release-metadata]",
        "gh release create",
    )
    missing = [fragment for fragment in required_workflow_fragments if fragment not in workflow]
    if missing:
        raise ReleaseContractError(f"Release workflow is missing safety contracts: {missing}")
    forbidden_workflow_fragments = (
        "workflow_dispatch:",
        "PYPI_TOKEN",
        "password:",
        "skip-existing",
    )
    forbidden = [
        fragment for fragment in forbidden_workflow_fragments if fragment in workflow
    ]
    if forbidden:
        raise ReleaseContractError(
            f"Release workflow contains forbidden publishing configuration: {forbidden}"
        )

    return version


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tag")
    mode.add_argument("--release-ref")
    args = parser.parse_args()
    version = verify_release(tag=args.tag)
    if args.release_ref is not None:
        request = None
        if args.release_ref == "refs/heads/main":
            try:
                request = json.loads(
                    (REPO_ROOT / ".github" / "release-request.json").read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise ReleaseContractError("Cannot read the explicit release request") from exc
        print(resolve_release_tag(version, ref=args.release_ref, request=request))
        return
    print(f"Release contract OK for v{version}")


if __name__ == "__main__":
    main()
