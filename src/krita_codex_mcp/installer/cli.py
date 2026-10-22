from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from krita_codex_mcp import __version__

from .claude_config import claude_server_config
from .codex_config import apply_codex_config_change, plan_codex_config_change
from .errors import InstallerError
from .manager import InstallationManager
from .paths import InstallPaths
from .preflight import run_live_preflight, run_offline_preflight


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="krita-codex-install",
        description="Install and verify the wheel-bundled Krita Codex bridge.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    install = commands.add_parser("install", help="Install or migrate the Krita bridge.")
    install.add_argument("--allowed-root", action="append", default=[])
    install.add_argument("--port", type=_port)
    install.add_argument("--reset-config", action="store_true")

    commands.add_parser("update", help="Update while preserving configuration and token.")

    check = commands.add_parser("check", help="Verify the installed payload and bridge.")
    check.add_argument("--offline", action="store_true")

    uninstall = commands.add_parser("uninstall", help="Remove managed Krita bridge files.")
    uninstall.add_argument("--remove-config", action="store_true")

    codex = commands.add_parser(
        "codex-config", help="Preview or explicitly apply a user-wide Codex MCP block."
    )
    codex.add_argument("--apply", action="store_true")
    codex.add_argument("--yes", action="store_true")
    codex.add_argument("--remove", action="store_true")
    claude = commands.add_parser(
        "claude-config", help="Print Claude MCP JSON without changing any configuration."
    )
    claude.add_argument("--client", choices=("code", "desktop"), default="code")
    claude.add_argument(
        "--server-only", action="store_true", help="Print the add-json server entry."
    )
    claude.add_argument("--uvx", help="Absolute uvx executable path; defaults to PATH lookup.")
    return parser


def _confirm_codex_change(assume_yes: bool) -> bool:
    if assume_yes:
        return True
    answer = input("Apply this user-wide Codex configuration change? [y/N] ")
    return answer.strip().casefold() in {"y", "yes"}


def run(arguments: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(arguments)
    try:
        paths = InstallPaths.from_environment()
        manager = InstallationManager(paths)
        if args.command == "claude-config":
            server = claude_server_config(paths, __version__, client=args.client, uvx=args.uvx)
            result = server if args.server_only else {"mcpServers": {"krita": server}}
            print(json.dumps(result, indent=2, ensure_ascii=True))
        elif args.command == "install":
            manager.install(
                allowed_roots=args.allowed_root,
                port=args.port,
                reset_config=args.reset_config,
            )
            print(
                "Installation complete. Krita compatibility is pending; close and restart "
                "Krita before running check."
            )
        elif args.command == "update":
            manager.update()
            print("Update complete. Configuration and token were preserved.")
            print("Krita compatibility is pending; close and restart Krita before running check.")
        elif args.command == "check":
            if args.offline:
                run_offline_preflight(paths, __version__)
                print("Offline installation check: OK")
            else:
                result = run_live_preflight(paths, __version__)
                print(
                    f"Krita {result.krita_version}: {result.status} "
                    f"(compatibility profile {result.profile_version}, {result.validation})."
                )
                for issue in result.issues:
                    affected = ", ".join(str(item) for item in issue["affected_tools"])
                    print(
                        f"WARNING {issue['code']} [{affected}]: {issue['message']}",
                        file=sys.stderr,
                    )
                if result.status == "limited":
                    print("Ready for productive use with the reported limitations.")
                else:
                    print("Ready for productive use.")
        elif args.command == "uninstall":
            manager.uninstall(remove_config=args.remove_config)
            print("Uninstall complete. User artwork and allowed roots were not touched.")
        elif args.command == "codex-config":
            change = plan_codex_config_change(
                paths.codex_config_file,
                __version__,
                remove=args.remove,
            )
            print("Proposed user-wide Codex configuration change:")
            print(change.preview)
            if args.apply:
                if not _confirm_codex_change(args.yes):
                    raise InstallerError("The user-wide Codex configuration was not changed.")
                apply_codex_config_change(paths.codex_config_file, change)
                print(
                    "User-wide Codex configuration updated transactionally; "
                    "an atomic backup was used when a previous file existed."
                )
            else:
                print("Preview only; no Codex configuration was changed.")
        else:
            parser.error("unsupported command")
    except InstallerError as exc:
        print(f"krita-codex-install: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
