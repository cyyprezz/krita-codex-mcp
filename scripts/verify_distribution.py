from __future__ import annotations

import argparse
import json
import tarfile
import zipfile
from pathlib import Path

PAYLOAD_PREFIXES = ("krita_codex_mcp/", "krita_codex_protocol/")
MCP_REGISTRY_MARKER = "mcp-name: io.github.cyyprezz/krita-codex-mcp"


def wheel_payload(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {
            name: archive.read(name)
            for name in archive.namelist()
            if name.startswith(PAYLOAD_PREFIXES) and not name.endswith("/")
        }


def compare_wheel_payloads(first: Path, second: Path) -> None:
    first_payload = wheel_payload(first)
    second_payload = wheel_payload(second)
    if first_payload.keys() != second_payload.keys():
        missing = sorted(first_payload.keys() - second_payload.keys())
        added = sorted(second_payload.keys() - first_payload.keys())
        raise RuntimeError(
            "Wheel Python/plugin payload file sets differ: "
            f"missing={missing}, added={added}"
        )
    changed = sorted(
        name for name, content in first_payload.items() if second_payload[name] != content
    )
    if changed:
        raise RuntimeError(f"Wheel Python/plugin payload bytes differ: {changed}")


def verify_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        home_markers = {
            str(Path.home()).encode("utf-8"),
            str(Path.home()).replace("\\", "/").encode("utf-8"),
        }
        leaked_home = [
            name
            for name in names
            if any(marker in archive.read(name) for marker in home_markers)
        ]
    if leaked_home:
        raise RuntimeError("Wheel contains a local user-home path")
    required = {
        "krita_codex_mcp/server.py",
        "krita_codex_mcp/installer/cli.py",
        "krita_codex_mcp/_resources/krita_plugin/kritacodexbridge.desktop",
        "krita_codex_mcp/_resources/krita_plugin/kritacodexbridge/extension.py",
        "krita_codex_mcp/_resources/krita_plugin/kritacodexbridge/installation_info.py",
        "krita_codex_protocol/messages.py",
    }
    missing = sorted(required - names)
    if missing:
        raise RuntimeError(f"Wheel is missing required payload entries: {missing}")
    forbidden_parts = (
        "/scripts/",
        "/tests/",
        "/.codex/",
        "/config.json",
        ".token",
        "__pycache__",
    )
    forbidden = sorted(
        name for name in names if any(part in f"/{name}" for part in forbidden_parts)
    )
    if forbidden:
        raise RuntimeError(f"Wheel contains forbidden entries: {forbidden}")
    entry_points = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
    if len(entry_points) != 1:
        raise RuntimeError("Wheel must contain exactly one entry_points.txt file")
    metadata_files = [name for name in names if name.endswith(".dist-info/METADATA")]
    if len(metadata_files) != 1:
        raise RuntimeError("Wheel must contain exactly one METADATA file")
    with zipfile.ZipFile(path) as archive:
        scripts = archive.read(entry_points[0]).decode("utf-8")
        metadata = archive.read(metadata_files[0]).decode("utf-8")
    for command in ("krita-codex-mcp", "krita-codex-install"):
        if command not in scripts:
            raise RuntimeError(f"Wheel entry point is missing: {command}")
    if MCP_REGISTRY_MARKER not in metadata:
        raise RuntimeError("Wheel metadata is missing the MCP Registry ownership marker")


def verify_sdist(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        home_markers = {
            str(Path.home()).encode("utf-8"),
            str(Path.home()).replace("\\", "/").encode("utf-8"),
        }
        leaked_home = []
        for member in archive.getmembers():
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            data = extracted.read() if extracted is not None else b""
            if any(marker in data for marker in home_markers):
                leaked_home.append(member.name)
    if leaked_home:
        raise RuntimeError("Source distribution contains a local user-home path")
    suffixes = {
        "krita-plugin/kritacodexbridge/extension.py",
        "krita-plugin/kritacodexbridge.desktop",
        "scripts/install.ps1",
        "server.json",
        "src/krita_codex_mcp/installer/cli.py",
        "src/krita_codex_protocol/messages.py",
    }
    for suffix in suffixes:
        if not any(name.endswith(suffix) for name in names):
            raise RuntimeError(f"Source distribution is missing: {suffix}")
    forbidden = [name for name in names if "__pycache__" in name or name.endswith(".token")]
    if forbidden:
        raise RuntimeError(f"Source distribution contains forbidden entries: {forbidden}")
    with tarfile.open(path, "r:gz") as archive:
        readme_members = [
            member
            for member in archive.getmembers()
            if member.isfile() and member.name.endswith("/README.md")
        ]
        server_members = [
            member
            for member in archive.getmembers()
            if member.isfile() and member.name.endswith("/server.json")
        ]
        if len(readme_members) != 1 or len(server_members) != 1:
            raise RuntimeError(
                "Source distribution must contain one README.md and one server.json"
            )
        readme_file = archive.extractfile(readme_members[0])
        server_file = archive.extractfile(server_members[0])
        readme = readme_file.read().decode("utf-8") if readme_file is not None else ""
        server = json.loads(server_file.read()) if server_file is not None else {}
    if MCP_REGISTRY_MARKER not in readme:
        raise RuntimeError("Source distribution README is missing the MCP Registry marker")
    if server.get("name") != "io.github.cyyprezz/krita-codex-mcp":
        raise RuntimeError("Source distribution server.json has the wrong MCP Registry name")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()
    wheels: list[Path] = []
    for path in args.artifacts:
        if path.suffix == ".whl":
            verify_wheel(path)
            wheels.append(path)
        elif path.name.endswith(".tar.gz"):
            verify_sdist(path)
        else:
            raise RuntimeError(f"Unsupported distribution artifact: {path.name}")
        print(f"Verified distribution artifact: {path.name}")
    for wheel in wheels[1:]:
        compare_wheel_payloads(wheels[0], wheel)
        print(
            "Verified byte-identical Python/plugin payloads: "
            f"{wheels[0].name} == {wheel.name}"
        )


if __name__ == "__main__":
    main()
