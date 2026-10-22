"""Discover wheel tools through real Claude Code without a paid model or Krita GUI.

The model endpoint is an isolated loopback stub. Discovery must appear in the
client's system/init event; SDK discovery alone cannot satisfy this test.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from stdio_surface_smoke import EXPECTED_TOOLS

from krita_codex_mcp import __version__
from krita_codex_mcp.installer.claude_config import claude_server_config
from krita_codex_mcp.installer.paths import InstallPaths


class LocalModelStub(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        # A failed model request is intentional: tools load before inference.
        body = json.dumps({
            "type": "error",
            "error": {"type": "authentication_error", "message": "Local discovery test only."},
        }).encode()
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def discovered_tools(output: str) -> set[str]:
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            servers = event.get("mcp_servers", [])
            if not any(s.get("name") == "krita" and s.get("status") == "connected"
                       for s in servers):
                raise RuntimeError("Claude Code did not connect the Krita server")
            prefix = "mcp__krita__"
            return {name.removeprefix(prefix) for name in event.get("tools", [])
                    if name.startswith(prefix)}
    raise RuntimeError("Claude Code did not emit a system/init discovery event")


def smoke(wheel: Path) -> None:
    claude, uvx = shutil.which("claude"), shutil.which("uvx")
    if claude is None or uvx is None:
        raise RuntimeError("Claude Code and uvx must be installed")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        profile = root / "claude-profile"
        profile.mkdir()
        environment = {
            name: value for name, value in os.environ.items()
            if not name.startswith(("ANTHROPIC_", "CLAUDE_", "AWS_", "GOOGLE_", "OPENAI_"))
        }
        environment.pop("KRITA_CODEX_CONFIG", None)
        environment.update({
            "CLAUDE_CONFIG_DIR": str(profile),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "ANTHROPIC_API_KEY": "sk-ant-local-discovery-test-not-a-real-key",
            "NO_PROXY": "127.0.0.1,localhost",
            "ENABLE_TOOL_SEARCH": "false",
        })
        bridge = root / "bridge.json"
        bridge.write_text(json.dumps({
            "token": secrets.token_urlsafe(32), "allowed_roots": [str(root)]
        }), encoding="utf-8")
        bridge.chmod(0o600)
        environment["KRITA_CODEX_CONFIG"] = str(bridge)
        launcher = claude_server_config(
            InstallPaths.from_environment(environment), __version__,
            uvx=uvx, environment=environment,
        )
        expected_args = ["--from", f"krita-codex-mcp=={__version__}", "krita-codex-mcp"]
        if launcher["args"] != expected_args:
            raise RuntimeError("The installer launcher is not pinned to this distribution")
        # Substitute only the package source so the candidate, not PyPI, is tested.
        launcher["args"] = ["--from", str(wheel.resolve()), "krita-codex-mcp"]
        config = root / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"krita": launcher}}), encoding="utf-8")
        config.chmod(0o600)
        version = subprocess.run(
            [claude, "--version"], cwd=root, env=environment, check=True,
            capture_output=True, text=True, timeout=30,
        )
        print(version.stdout.strip())
        # Exercise Claude's own JSON parser and user scope without a user's profile.
        subprocess.run(
            [claude, "mcp", "add-json", "--scope", "user", "krita", json.dumps(launcher)],
            cwd=root, env=environment, check=True, capture_output=True, text=True, timeout=30,
        )
        saved = json.loads((profile / ".claude.json").read_text(encoding="utf-8"))
        if saved.get("mcpServers", {}).get("krita") != launcher:
            raise RuntimeError("Claude Code did not preserve the supplied server entry")
        with ThreadingHTTPServer(("127.0.0.1", 0), LocalModelStub) as endpoint:
            environment["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{endpoint.server_port}"
            thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run(
                    [claude, "--print", "--verbose", "--output-format", "stream-json",
                     "--strict-mcp-config", "--mcp-config", str(config),
                     "--setting-sources", "", "--max-turns", "1", "Reply with OK."],
                    cwd=root, env=environment, capture_output=True, text=True, timeout=120,
                )
            finally:
                endpoint.shutdown()
                thread.join(timeout=5)
        try:
            names = discovered_tools(result.stdout)
            if names != EXPECTED_TOOLS:
                raise RuntimeError(f"Unexpected Claude MCP tools: {sorted(names)}")
        except RuntimeError:
            print(result.stdout[-6000:])
            print(result.stderr[-4000:])
            raise
    print("Claude Code: generated JSON accepted and 13 wheel tools discovered; no model/Krita test")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    smoke(args.wheel)


if __name__ == "__main__":
    main()
