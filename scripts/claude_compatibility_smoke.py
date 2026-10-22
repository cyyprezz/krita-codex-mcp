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
    requests = 0

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.path.startswith("/v1/messages/count_tokens"):
            body = json.dumps({"input_tokens": 1}).encode()
            content_type = "application/json"
        else:
            type(self).requests += 1
            message = {
                "id": "msg_local_discovery", "type": "message", "role": "assistant",
                "model": request["model"], "content": [], "stop_reason": None,
                "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0},
            }
            answer = "Local MCP discovery stub."
            if request.get("stream"):
                events = [
                    {"type": "message_start", "message": message},
                    {"type": "content_block_start", "index": 0,
                     "content_block": {"type": "text", "text": ""}},
                    {"type": "content_block_delta", "index": 0,
                     "delta": {"type": "text_delta", "text": answer}},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn",
                     "stop_sequence": None}, "usage": {"output_tokens": 1}},
                    {"type": "message_stop"},
                ]
                body = "".join(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
                ).encode()
                content_type = "text/event-stream"
            else:
                message.update(content=[{"type": "text", "text": answer}], stop_reason="end_turn")
                message["usage"]["output_tokens"] = 1
                body = json.dumps(message).encode()
                content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
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
        unrelated = {"type": "stdio", "command": uvx, "args": ["--version"]}
        (profile / ".claude.json").write_text(
            json.dumps({"mcpServers": {"unrelated": unrelated}}), encoding="utf-8"
        )
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
        if saved.get("mcpServers", {}).get("unrelated") != unrelated:
            raise RuntimeError("Claude Code changed an unrelated MCP server entry")
        diagnostics = root / "claude-debug.log"
        with ThreadingHTTPServer(("127.0.0.1", 0), LocalModelStub) as endpoint:
            environment["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{endpoint.server_port}"
            thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
            thread.start()
            try:
                try:
                    result = subprocess.run(
                        [claude, "--bare", "--print", "--verbose", "--output-format", "stream-json",
                         "--strict-mcp-config", "--mcp-config", str(config),
                         "--setting-sources", "", "--debug-file", str(diagnostics),
                         "--max-turns", "1", "Reply with OK."],
                        cwd=root, env=environment, stdin=subprocess.DEVNULL,
                        capture_output=True, text=True, timeout=90,
                    )
                except subprocess.TimeoutExpired as exc:
                    print(f"Claude timed out; stdout: {exc.stdout!r}; stderr: {exc.stderr!r}")
                    if diagnostics.is_file():
                        print(diagnostics.read_text(encoding="utf-8")[-10000:])
                    raise
            finally:
                endpoint.shutdown()
                thread.join(timeout=5)
        try:
            if result.returncode != 0 or LocalModelStub.requests == 0:
                raise RuntimeError("Claude did not complete against the local model stub")
            names = discovered_tools(result.stdout)
            if names != EXPECTED_TOOLS:
                raise RuntimeError(f"Unexpected Claude MCP tools: {sorted(names)}")
        except RuntimeError:
            print(result.stdout[-6000:])
            print(result.stderr[-4000:])
            if diagnostics.is_file():
                print(diagnostics.read_text(encoding="utf-8")[-10000:])
            raise
    print("Claude Code: generated JSON accepted and 13 wheel tools discovered; no model/Krita test")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    smoke(args.wheel)


if __name__ == "__main__":
    main()
