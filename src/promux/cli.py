"""Antigravity CLI profile multiplexer & quota failover daemon.

Argument parsing and command dispatch.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .adapters import get_default_registry
from .auth import (
    _get_last_refresh_method,
    _get_last_refresh_revoked,
    _refresh_token_fallback_agy,
    _refresh_token_file,
    _refresh_token_native,
    _set_last_refresh_method,
    _set_last_refresh_revoked,
)
from .auth import (
    atomic_copy_token as _atomic_copy_token,
)
from .auth import (
    extract_access_token as _extract_access_token,
)
from .auth import (
    extract_expiry as _extract_expiry,
)
from .auth import (
    extract_refresh_token as _extract_refresh_token,
)
from .auth import (
    get_oauth_credentials as _get_oauth_credentials,
)
from .auth import (
    is_token_expired as _is_token_expired,
)
from .auth import (
    parse_token_expiry_dt as _parse_token_expiry_dt,
)
from .auth import (
    read_token_data as _read_token_data,
)
from .auth import (
    sync_active_tokens as _sync_active_tokens,
)
from .commands.prime import cmd_prime
from .commands.profile import (
    cmd_list,
    cmd_next,
    cmd_remove,
    cmd_save,
    cmd_switch,
    cmd_whoami,
)
from .commands.quota import (
    _fetch_account_quota as _fetch_account_quota,
)
from .commands.quota import (
    _refresh_account_token as _refresh_account_token,
)
from .commands.quota import (
    cmd_quota,
    cmd_refresh,
)
from .commands.tools import cmd_completion, cmd_tools
from .commands.watch import cmd_watch
from .completion import SUPPORTED_SHELLS
from .constants import (
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_POLL_SECONDS,
    DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS,
)
from .failover import FailoverEngine
from .help import cmd_help
from .logging_config import configure_cli_logging
from .storage import StorageEngine
from .version import cmd_version

logger = logging.getLogger(__name__)

__all__ = [
    "build_parser",
    "main",
    "get_storage",
    "cmd_list",
    "cmd_save",
    "cmd_switch",
    "cmd_next",
    "cmd_whoami",
    "cmd_remove",
    "cmd_quota",
    "cmd_prime",
    "cmd_refresh",
    "cmd_watch",
    "cmd_tools",
    "cmd_completion",
    "cmd_help",
    "cmd_version",
    "_fetch_account_quota",
    "_refresh_account_token",
    "_get_or_refresh_access_token",
    "_refresh_token_file",
    "_refresh_token_native",
    "_refresh_token_fallback_agy",
    "_get_last_refresh_method",
    "_set_last_refresh_method",
    "_get_last_refresh_revoked",
    "_set_last_refresh_revoked",
    "_atomic_copy_token",
    "_extract_access_token",
    "_extract_expiry",
    "_extract_refresh_token",
    "_get_oauth_credentials",
    "_is_token_expired",
    "_parse_token_expiry_dt",
    "_read_token_data",
    "_sync_active_tokens",
]


def get_storage() -> StorageEngine:
    """Instantiate StorageEngine honoring runtime environment overrides."""
    from .adapters.agy import AgyAdapter

    return AgyAdapter().get_storage()


def _get_or_refresh_access_token(
    token_path: Path, storage: StorageEngine | None = None
) -> str | None:
    token_data = _read_token_data(token_path)
    if not token_data:
        return None
    access_token = _extract_access_token(token_data)
    if _is_token_expired(token_data, buffer_seconds=DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS):
        try:
            refreshed = _refresh_token_file(token_path, token_data, storage=storage)
        except TypeError:
            refreshed = _refresh_token_file(token_path, token_data)
        if refreshed:
            return refreshed
    return access_token


COMMAND_CAPABILITIES: dict[str, str] = {
    "list": "vault",
    "save": "vault",
    "switch": "vault",
    "next": "vault",
    "whoami": "vault",
    "remove": "vault",
    "quota": "quota",
    "prime": "quota",
    "refresh": "refresh",
    "watch": "watch",
}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="Output in JSON format"
    )
    common.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Disable ANSI color output",
    )
    common.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v for INFO, -vv for DEBUG)",
    )
    common.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress all output except errors",
    )

    parser = argparse.ArgumentParser(
        prog="promux",
        description="Antigravity CLI profile multiplexer & quota failover daemon",
        add_help=False,
        parents=[common],
    )
    parser.add_argument(
        "-h", "--help", action="store_true", default=False, help="Show help and exit"
    )
    parser.add_argument(
        "-V", "--version", action="store_true", default=False, help="Show version and exit"
    )

    sub = parser.add_subparsers(dest="command", required=False, help="Subcommand to execute")
    sub.add_parser("version", parents=[common], help="Show version information")
    sub.add_parser("list", parents=[common], help="List all accounts in the vault")

    save_p = sub.add_parser("save", parents=[common], help="Save active token as a named profile")
    save_p.add_argument("name", help="Profile name")
    save_p.add_argument("--email", default=None, help="Account email (auto-detected if omitted)")

    switch_p = sub.add_parser(
        "switch", parents=[common], help="Hot-swap active profile or auto-switch to best quota"
    )
    switch_p.add_argument(
        "name", nargs="?", default=None, help="Profile name (optional if --smart is set)"
    )
    switch_p.add_argument(
        "--smart", action="store_true", default=False, help="Auto-switch to best quota"
    )
    switch_p.add_argument(
        "--model", choices=["gemini", "claude", "gpt"], default="gemini", help="Target model tier"
    )
    switch_p.add_argument(
        "--no-cache", action="store_true", default=False, help="Bypass quota cache"
    )

    next_p = sub.add_parser("next", parents=[common], help="Rotate to next eligible standby")
    next_p.add_argument("--reason", default="manual", help="Reason for rotation (default: manual)")
    next_p.add_argument(
        "--cooldown", type=int, default=DEFAULT_COOLDOWN_MINUTES, help="Cooldown duration in min"
    )

    quota_p = sub.add_parser("quota", parents=[common], help="Check Cloud Code Assist quota")
    quota_p.add_argument(
        "name", nargs="?", default=None, help="Profile name (default: all profiles in vault)"
    )
    quota_p.add_argument(
        "--no-cache", action="store_true", default=False, help="Bypass quota cache"
    )
    quota_p.add_argument(
        "-p",
        "--prime",
        action="store_true",
        default=False,
        help="Prime accounts that have reset weekly quota before displaying",
    )

    prime_p = sub.add_parser(
        "prime",
        parents=[common],
        help="Prime weekly quota reset countdowns via minimal headless turns",
    )
    prime_p.add_argument(
        "account",
        nargs="?",
        default=None,
        help="Profile name (default: all profiles in vault)",
    )
    prime_p.add_argument(
        "-f",
        "--force",
        action="store_true",
        default=False,
        help="Force priming even if an active countdown is already ticking",
    )
    prime_p.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        default=False,
        help="Check eligibility and list accounts without sending tokens",
    )

    refresh_p = sub.add_parser("refresh", parents=[common], help="Refresh OAuth tokens")
    refresh_p.add_argument(
        "name", nargs="?", default=None, help="Profile name (default: all profiles in vault)"
    )
    refresh_p.add_argument(
        "--force", action="store_true", default=False, help="Force token refresh even if valid"
    )

    sub.add_parser("whoami", parents=[common], help="Show active profile and token status")

    remove_p = sub.add_parser("remove", parents=[common], help="Remove a profile from vault")
    remove_p.add_argument("name", help="Profile name to remove")

    watch_p = sub.add_parser("watch", parents=[common], help="Start reactive log tailer daemon")
    watch_p.add_argument(
        "--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS, help="Log polling interval"
    )
    watch_p.add_argument("--cooldown", type=int, default=None, help="Cooldown override in minutes")

    tools_p = sub.add_parser("tools", parents=[common], help="List supported developer CLI tools")
    tools_p.add_argument("action", nargs="?", default="list", choices=["list"], help="Action")

    p_comp = sub.add_parser("completion", help="Generate shell auto-completion script")
    p_comp.add_argument("shell", choices=SUPPORTED_SHELLS, help="Target shell (bash or zsh)")

    help_p = sub.add_parser("help", parents=[common], help="Show help for a command")
    help_p.add_argument("subcommand", nargs="?", default=None, help="Command to show help for")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point returning status code."""
    if argv is None:
        argv = sys.argv[1:]

    registry = get_default_registry()
    raw_args = list(argv)

    if "-h" in raw_args or "--help" in raw_args:
        return cmd_help(no_color="--no-color" in raw_args)

    if "-V" in raw_args or "--version" in raw_args:
        return cmd_version(json_out="--json" in raw_args, no_color="--no-color" in raw_args)

    first_pos_idx = None
    for i, arg in enumerate(raw_args):
        if not arg.startswith("-"):
            first_pos_idx = i
            break

    tool_name_used: str | None = None
    remaining_args = raw_args
    if first_pos_idx is not None and registry.has_tool(raw_args[first_pos_idx]):
        tool_name_used = raw_args[first_pos_idx]
        target_tool = registry.get(tool_name_used)
        remaining_args = raw_args[:first_pos_idx] + raw_args[first_pos_idx + 1 :]
        next_pos = None
        for arg in remaining_args:
            if not arg.startswith("-"):
                next_pos = arg
                break
        if next_pos == "help":
            return cmd_help(tool=tool_name_used, no_color="--no-color" in raw_args)
    else:
        target_tool = registry.default_tool()

    if not remaining_args or all(a.startswith("-") for a in remaining_args):
        return cmd_help(no_color="--no-color" in raw_args)

    parser = build_parser()
    try:
        args = parser.parse_args(remaining_args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    configure_cli_logging(
        verbose=getattr(args, "verbose", 0),
        quiet=getattr(args, "quiet", False),
    )

    json_out = getattr(args, "json", False)
    no_color = getattr(args, "no_color", False)

    try:
        if args.command == "help":
            return cmd_help(subcommand=getattr(args, "subcommand", None), no_color=no_color)
        if args.command == "version":
            return cmd_version(json_out=json_out, no_color=no_color)
        if args.command == "tools":
            return cmd_tools(registry, json_out)
        if args.command == "completion":
            return cmd_completion(args.shell)

        if args.command in COMMAND_CAPABILITIES:
            required_cap = COMMAND_CAPABILITIES[args.command]
            caps = target_tool.list_capabilities()
            if required_cap not in caps:
                caps_str = ", ".join(caps)
                raise RuntimeError(
                    f"Tool '{target_tool.name}' does not support '{required_cap}'. "
                    f"Supported capabilities: {caps_str}"
                )

        storage = target_tool.get_storage()
        failover = FailoverEngine(storage)

        if args.command == "list":
            return cmd_list(storage, json_out)
        elif args.command == "save":
            return cmd_save(storage, args.name, args.email, json_out)
        elif args.command == "switch":
            if args.smart:
                if "quota" not in target_tool.list_capabilities():
                    raise RuntimeError(
                        f"Tool '{target_tool.name}' does not support smart quota switching "
                        "('quota' capability required)."
                    )
            elif not args.name:
                if json_out:
                    print(
                        json.dumps(
                            {
                                "success": False,
                                "error": "must specify account name or pass --smart",
                            },
                            indent=2,
                        )
                    )
                else:
                    print("Error: must specify account name or pass --smart", file=sys.stderr)
                return 2
            return cmd_switch(
                storage,
                args.name,
                json_out,
                smart=args.smart,
                model=args.model,
                failover=failover,
                no_cache=getattr(args, "no_cache", False),
            )
        elif args.command == "next":
            return cmd_next(failover, args.reason, args.cooldown, json_out)
        elif args.command == "quota":
            return cmd_quota(
                storage,
                args.name,
                json_out,
                no_cache=getattr(args, "no_cache", False),
                prime=getattr(args, "prime", False),
            )
        elif args.command == "prime":
            return cmd_prime(
                storage,
                args.account,
                force=args.force,
                dry_run=args.dry_run,
                json_out=json_out,
                no_color=no_color,
            )
        elif args.command == "refresh":
            return cmd_refresh(storage, args.name, args.force, json_out)
        elif args.command == "whoami":
            return cmd_whoami(storage, json_out)
        elif args.command == "remove":
            return cmd_remove(storage, args.name, json_out)
        elif args.command == "watch":
            return cmd_watch(failover, args.poll_seconds, args.cooldown)
        else:
            return cmd_help(no_color=no_color)
    except Exception as e:
        if json_out:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
