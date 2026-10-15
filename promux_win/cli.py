"""
Promux Windows CLI Interface
Full command line interface for Windows cmd and PowerShell.
Compatible with standard promux commands.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from .core import (
    OAuthRefresher,
    QuotaClient,
    SmartSwitchEngine,
    WindowsCredentialBridge,
    WindowsStorageEngine,
    format_relative_countdown,
)


def cmd_list(storage: WindowsStorageEngine, json_out: bool = False) -> int:
    accounts = storage.list_accounts()
    active_name = storage.get_active_account_name()

    if json_out:
        out = {
            "active": active_name,
            "accounts": [a.to_dict() for a in accounts],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    if not accounts:
        print("No accounts vaulted yet. Use 'promux save <name>' to save current session.")
        return 0

    print(f"{'STATUS':<10} {'NAME':<16} {'EMAIL':<30} {'TIER':<14} {'LAST USED'}")
    print("-" * 85)
    for a in accounts:
        is_active = a.name == active_name
        state_str = "* ACTIVE" if is_active else a.get_state(is_active=False).value.upper()
        email_str = a.email or "N/A"
        last_used = a.last_used_at.strftime("%Y-%m-%d %H:%M") if a.last_used_at else "Never"
        print(f"{state_str:<10} {a.name:<16} {email_str:<30} {a.plan_type:<14} {last_used}")

    return 0


def cmd_switch(storage: WindowsStorageEngine, name: str | None, smart: bool = False) -> int:
    if smart:
        engine = SmartSwitchEngine(storage)
        ok, msg, target = engine.smart_switch()
        if ok and target:
            print(f"[+] Smart Switch: {msg}")
            return 0
        else:
            print(f"[-] Smart Switch failed: {msg}", file=sys.stderr)
            return 1

    if not name:
        print("Error: Account name required for switch (or use --smart).", file=sys.stderr)
        return 1

    ok, msg = storage.switch_profile(name)
    if ok:
        print(f"[+] {msg}")
        return 0
    else:
        print(f"[-] Error: {msg}", file=sys.stderr)
        return 1


def cmd_save(storage: WindowsStorageEngine, name: str) -> int:
    try:
        acct = storage.save_profile(name)
        print(f"[+] Successfully saved profile '{acct.name}' ({acct.email or 'no email'}).")
        return 0
    except Exception as e:
        print(f"[-] Error saving profile: {e}", file=sys.stderr)
        return 1


def cmd_whoami(storage: WindowsStorageEngine) -> int:
    active = storage.get_active_account_name()
    if not active:
        print("No active profile set in Promux.")
    else:
        print(f"Active Promux Profile: {active}")

    live = storage.get_live_token_data()
    if live:
        acc_token = live.get("token", {}).get("access_token")
        if acc_token:
            try:
                qc = QuotaClient(acc_token)
                email = qc.fetch_email()
                meta = qc.load_metadata()
                print(f"Active Google Account: {email or 'Unknown'}")
                print(f"Project ID: {meta.get('project_id')}")
                print(f"Plan Tier: {meta.get('plan_name')}")
            except Exception as e:
                print(f"Could not reach Google API: {e}")
    else:
        print("No active Antigravity session found in Windows Credential Manager.")
    return 0


def cmd_quota(storage: WindowsStorageEngine, name: str | None = None) -> int:
    accounts = storage.list_accounts() if not name else [storage.load_state().get("accounts", {}).get(name)]
    if name:
        acct_dict = storage.load_state().get("accounts", {}).get(name)
        if not acct_dict:
            print(f"Account '{name}' not found.", file=sys.stderr)
            return 1
        from .core import AccountMeta
        accounts = [AccountMeta.from_dict(acct_dict)]

    if not accounts:
        print("No accounts to inspect.", file=sys.stderr)
        return 1

    print(f"{'ACCOUNT':<14} {'GEMINI 5H':<18} {'GEMINI WK':<18} {'CLAUDE 5H':<18} {'STATUS'}")
    print("-" * 80)

    for a in accounts:
        tok_data = storage.read_account_token(a.name)
        if not tok_data:
            print(f"{a.name:<14} {'Token Missing':<50}")
            continue

        OAuthRefresher.refresh(tok_data, force=False)
        acc = tok_data.get("token", {}).get("access_token")
        if not acc:
            print(f"{a.name:<14} {'No Access Token':<50}")
            continue

        try:
            qc = QuotaClient(acc)
            q = qc.fetch_quota(a.project_id or "aicode-consumers")
            g5_pct = f"{round(q.gemini_5h_remaining * 100)}%"
            g5_cd = format_relative_countdown(q.gemini_5h_reset)
            g5_str = f"{g5_pct} ({g5_cd})" if g5_cd else g5_pct

            gw_pct = f"{round(q.gemini_weekly_remaining * 100)}%"
            gw_cd = format_relative_countdown(q.gemini_weekly_reset)
            gw_str = f"{gw_pct} ({gw_cd})" if gw_cd else gw_pct

            c5_pct = f"{round(q.third_party_5h_remaining * 100)}%"
            c5_cd = format_relative_countdown(q.third_party_5h_reset)
            c5_str = f"{c5_pct} ({c5_cd})" if c5_cd else c5_pct

            status_str = "OK" if q.gemini_5h_remaining > 0.1 else "LOW QUOTA"
            print(f"{a.name:<14} {g5_str:<18} {gw_str:<18} {c5_str:<18} {status_str}")
        except Exception as e:
            print(f"{a.name:<14} Error: {str(e)[:40]}")

    return 0


def cmd_refresh(storage: WindowsStorageEngine, name: str | None = None) -> int:
    accounts = storage.list_accounts() if not name else []
    target_names = [name] if name else [a.name for a in accounts]

    for t in target_names:
        tok_data = storage.read_account_token(t)
        if not tok_data:
            print(f"[-] Token for '{t}' not found.")
            continue
        ok, msg = OAuthRefresher.refresh(tok_data, force=True)
        if ok:
            storage.write_account_token(t, tok_data)
            if storage.get_active_account_name() == t:
                WindowsCredentialBridge.write_token(tok_data)
            print(f"[+] Refreshed '{t}': {msg}")
        else:
            print(f"[-] Failed to refresh '{t}': {msg}")
    return 0


def cmd_remove(storage: WindowsStorageEngine, name: str) -> int:
    ok = storage.remove_profile(name)
    if ok:
        print(f"[+] Profile '{name}' removed.")
        return 0
    else:
        print(f"[-] Profile '{name}' not found.", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    storage = WindowsStorageEngine()
    parser = argparse.ArgumentParser(
        prog="promux",
        description="Promux Windows — Universal Antigravity Multi-Account Profile Multiplexer",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # list
    p_list = subparsers.add_parser("list", help="List all vaulted profiles")
    p_list.add_argument("--json", action="store_true", help="Output in JSON format")

    # switch
    p_switch = subparsers.add_parser("switch", help="Switch active profile")
    p_switch.add_argument("name", nargs="?", help="Target profile name")
    p_switch.add_argument("--smart", action="store_true", help="Smart quota-based switch")

    # save
    p_save = subparsers.add_parser("save", help="Save active session as profile")
    p_save.add_argument("name", help="Profile name")

    # whoami
    subparsers.add_parser("whoami", help="Show active account info")

    # quota
    p_quota = subparsers.add_parser("quota", help="Inspect live quotas")
    p_quota.add_argument("name", nargs="?", help="Specific profile to inspect")

    # refresh
    p_refresh = subparsers.add_parser("refresh", help="Force renew OAuth tokens")
    p_refresh.add_argument("name", nargs="?", help="Specific profile to renew")

    # remove
    p_remove = subparsers.add_parser("remove", help="Remove a profile")
    p_remove.add_argument("name", help="Profile name to remove")

    # gui
    p_gui = subparsers.add_parser("gui", help="Launch the modern desktop GUI")
    p_gui.add_argument("--browser", action="store_true", help="Open in default browser")
    p_gui.add_argument("--edge", action="store_true", help="Open in Edge standalone app mode")
    p_gui.add_argument("--no-window", action="store_true", help="Run background server only")
    p_gui.add_argument("--port", type=int, default=18420, help="Port to bind server (default: 18420)")

    args = parser.parse_args(argv)

    if not args.subcommand or args.subcommand == "gui":
        from .app import main as app_main
        gui_args = []
        if getattr(args, "browser", False):
            gui_args.append("--browser")
        if getattr(args, "edge", False):
            gui_args.append("--edge")
        if getattr(args, "no_window", False):
            gui_args.append("--no-window")
        if getattr(args, "port", None):
            gui_args.extend(["--port", str(args.port)])
        return app_main(gui_args)

    if args.subcommand == "list":
        return cmd_list(storage, json_out=args.json)
    elif args.subcommand == "switch":
        return cmd_switch(storage, name=args.name, smart=args.smart)
    elif args.subcommand == "save":
        return cmd_save(storage, name=args.name)
    elif args.subcommand == "whoami":
        return cmd_whoami(storage)
    elif args.subcommand == "quota":
        return cmd_quota(storage, name=args.name)
    elif args.subcommand == "refresh":
        return cmd_refresh(storage, name=args.name)
    elif args.subcommand == "remove":
        return cmd_remove(storage, name=args.name)

    return 0


if __name__ == "__main__":
    sys.exit(main())
