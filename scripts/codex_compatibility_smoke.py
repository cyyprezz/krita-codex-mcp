from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import tempfile
import tomllib
from pathlib import Path

from stdio_surface_smoke import EXPECTED_TOOLS

from krita_codex_mcp import __version__
from krita_codex_mcp.installer.codex_config import managed_block


async def response(process: asyncio.subprocess.Process, request: dict) -> dict:
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write((json.dumps(request) + "\n").encode())
    await process.stdin.drain()
    async with asyncio.timeout(90):
        while line := await process.stdout.readline():
            message = json.loads(line)
            if message.get("id") == request["id"]:
                if "error" in message:
                    raise RuntimeError(f"Codex rejected {request['method']}: {message['error']}")
                return message["result"]
    raise RuntimeError(f"Codex exited before answering {request['method']}")


async def smoke(wheel: Path) -> None:
    codex = shutil.which("codex")
    uvx = shutil.which("uvx")
    if codex is None or uvx is None:
        raise RuntimeError("Codex CLI and uvx must be installed")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        codex_home = root / "codex-home"
        codex_home.mkdir()
        config_path = codex_home / "config.toml"
        environment = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("OPENAI_", "AZURE_OPENAI_", "CODEX_"))
        }
        environment.pop("KRITA_CODEX_CONFIG", None)
        environment["CODEX_HOME"] = str(codex_home)
        generated = managed_block(__version__)
        launcher = tomllib.loads(generated)["mcp_servers"]["krita"]
        config_path.write_text(generated + "\n", encoding="utf-8")
        for arguments in (
            ["--version"],
            ["mcp", "get", "krita", "--json"],
            ["plugin", "add", "--help"],
            ["plugin", "marketplace", "add", "--help"],
        ):
            process = await asyncio.create_subprocess_exec(
                codex,
                *arguments,
                cwd=root,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            if process.returncode != 0:
                raise RuntimeError(f"codex {arguments} failed: {stderr.decode()}")
            if arguments == ["--version"]:
                print(stdout.decode().strip())
            if arguments[:2] == ["mcp", "get"] and json.loads(stdout).get("name") != "krita":
                raise RuntimeError("Codex did not read the installer-generated server entry")

        bridge_config = root / "bridge-config.json"
        server_environment = ["env_vars = " + json.dumps(launcher["env_vars"])]
        if os.name == "nt":
            server_environment.extend(
                [
                    "[mcp_servers.krita.env]",
                    "KRITA_CODEX_CONFIG = " + json.dumps(str(bridge_config)),
                ]
            )
        else:
            xdg_config = root / "custom-xdg-config"
            bridge_config = xdg_config / "krita-codex-mcp" / "config.json"
            bridge_config.parent.mkdir(parents=True)
            environment["XDG_CONFIG_HOME"] = str(xdg_config)
        bridge_config.write_text(
            json.dumps({"token": secrets.token_urlsafe(32), "allowed_roots": [str(root)]}),
            encoding="utf-8",
        )
        config_path.write_text(
            "\n".join(
                [
                    "[mcp_servers.krita]",
                    "command = " + json.dumps(uvx),
                    "args = " + json.dumps(["--from", str(wheel.resolve()), "krita-codex-mcp"]),
                    "startup_timeout_sec = " + str(launcher["startup_timeout_sec"]),
                    "tool_timeout_sec = " + str(launcher["tool_timeout_sec"]),
                    *server_environment,
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        process = await asyncio.create_subprocess_exec(
            codex,
            "app-server",
            cwd=root,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stderr is not None
        diagnostics = asyncio.create_task(process.stderr.read())
        failed = False
        try:
            await response(
                process,
                {
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "krita_codex_compatibility", "version": __version__},
                        "capabilities": {"experimentalApi": True},
                    },
                },
            )
            assert process.stdin is not None
            process.stdin.write(b'{"method":"initialized"}\n')
            await process.stdin.drain()
            result = await response(
                process,
                {
                    "id": 1,
                    "method": "mcpServerStatus/list",
                    "params": {"serverName": "krita", "detail": "toolsAndAuthOnly", "limit": 100},
                },
            )
            servers = [entry for entry in result["data"] if entry["name"] == "krita"]
            if len(servers) != 1:
                raise RuntimeError(f"Expected one Krita MCP connection, got {len(servers)}")
            names = {tool["name"] for tool in servers[0]["tools"].values()}
            if names != EXPECTED_TOOLS:
                raise RuntimeError(f"Codex discovered an unexpected tool surface: {sorted(names)}")
        except Exception:
            failed = True
            raise
        finally:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            try:
                logs = (await asyncio.wait_for(diagnostics, timeout=5)).decode(errors="replace")
            except TimeoutError:
                logs = "Codex diagnostic stream did not close after shutdown."
            if failed or process.returncode not in (0, None):
                print(logs[-4000:])
        print("Codex compatibility: generated config, plugin commands and 13 MCP tools verified")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    asyncio.run(smoke(args.wheel))


if __name__ == "__main__":
    main()
