import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

from .constants import (
    DEFAULT_POLL_SECONDS,
    DEFAULT_COOLDOWN_MINUTES,
    PROMUX_HOME,
    GEMINI_CLI_HOME,
    OAUTH_TOKEN_URL,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
)
from .failover import FailoverEngine
from .models import AccountMeta, QuotaSummary, RotationResult
from .quota import QuotaClient
from .storage import StorageEngine
from .watch import LogWatcher, LogMatch


def get_storage() -> StorageEngine:
    """Instantiate StorageEngine honoring runtime environment overrides."""
    promux_home = Path(os.environ["PROMUX_HOME"]) if "PROMUX_HOME" in os.environ else None
    gemini_home = Path(os.environ["PROMUX_GEMINI_HOME"]) if "PROMUX_GEMINI_HOME" in os.environ else None
    return StorageEngine(promux_home=promux_home, gemini_home=gemini_home)


def _read_token_data(token_path: Path) -> Optional[Dict[str, Any]]:
    """Safely read and parse OAuth token JSON."""
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _extract_access_token(token_data: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract access_token string from raw token dictionary."""
    if not token_data:
        return None
    tok = token_data.get("token")
    if isinstance(tok, dict):
        return tok.get("access_token")
    if "access_token" in token_data:
        return token_data.get("access_token")
    return None


def _extract_expiry(token_data: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract expiry timestamp from raw token dictionary."""
    if not token_data:
        return None
    tok = token_data.get("token")
    if isinstance(tok, dict):
        return tok.get("expiry")
    if "expiry" in token_data:
        return token_data.get("expiry")
    return None


def _get_oauth_credentials() -> tuple[str, str]:
    """Retrieve OAuth client ID and secret from environment or ~/.promux/oauth.json."""
    client_id = os.environ.get("PROMUX_OAUTH_CLIENT_ID", OAUTH_CLIENT_ID)
    client_secret = os.environ.get("PROMUX_OAUTH_CLIENT_SECRET", OAUTH_CLIENT_SECRET)
    if not client_id or not client_secret:
        cfg_file = PROMUX_HOME / "oauth.json"
        if cfg_file.exists():
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    client_id = client_id or data.get("client_id", "")
                    client_secret = client_secret or data.get("client_secret", "")
            except Exception:
                pass
    return client_id, client_secret


def _refresh_token_file(token_path: Path, token_data: Dict[str, Any]) -> Optional[str]:
    """Attempt to refresh an expired token using its refresh_token."""
    tok = token_data.get("token")
    if not isinstance(tok, dict):
        return None
    refresh_token = tok.get("refresh_token")
    if not refresh_token:
        return None
    client_id, client_secret = _get_oauth_credentials()
    if not client_id or not client_secret:
        return None
    try:
        data = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }).encode("utf-8")

        req = urllib.request.Request(OAUTH_TOKEN_URL, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        new_acc = res.get("access_token")
        if new_acc:
            tok["access_token"] = new_acc
            expires_in = res.get("expires_in", 3600)
            now = datetime.now(timezone.utc)
            tok["expiry"] = (now + timedelta(seconds=expires_in)).isoformat().replace("+00:00", "Z")
            tmp_path = token_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(token_data, f, indent=2)
            tmp_path.chmod(0o600)
            os.replace(tmp_path, token_path)
            return new_acc
    except Exception:
        pass
    return None


def _get_or_refresh_access_token(token_path: Path) -> Optional[str]:
    """Get access token, refreshing it if expired and refresh_token is present."""
    token_data = _read_token_data(token_path)
    if not token_data:
        return None
    access_token = _extract_access_token(token_data)
    expiry_str = _extract_expiry(token_data)
    is_expired = False
    if expiry_str:
        try:
            clean_exp = expiry_str.replace("Z", "").split("+")[0]
            exp_dt = datetime.fromisoformat(clean_exp).replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= exp_dt:
                is_expired = True
        except Exception:
            pass
    if is_expired:
        refreshed = _refresh_token_file(token_path, token_data)
        if refreshed:
            return refreshed
    return access_token


def cmd_list(storage: StorageEngine, json_out: bool) -> int:
    state = storage.load_state()
    active = state.get("active")
    accounts_dict = state.get("accounts", {})

    rows = []
    for name, data in accounts_dict.items():
        acct = AccountMeta.from_dict(data)
        is_active = (name == active)
        rows.append({
            "name": name,
            "active": is_active,
            "state": acct.state.value,
            "email": acct.email,
            "cooldown_until": acct.cooldown_until.isoformat() if acct.cooldown_until else None,
            "last_used": acct.last_used_at.isoformat() if acct.last_used_at else None,
            "last_used_at": acct.last_used_at.isoformat() if acct.last_used_at else None,
        })

    if json_out:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print("No accounts found. Use 'promux save <name>' to save your active profile.")
        return 0

    header = f"{'ACTIVE':<8}{'NAME':<18}{'STATE':<12}{'EMAIL':<32}{'COOLDOWN':<24}{'LAST USED'}"
    divider = "-" * 110
    print(header)
    print(divider)
    for r in rows:
        star = "*" if r["active"] else " "
        name_str = r["name"]
        state_str = r["state"]
        email_str = r["email"] or "-"
        cool_str = r["cooldown_until"] or "-"
        used_str = r["last_used"] or "-"
        print(f"{star:<8}{name_str:<18}{state_str:<12}{email_str:<32}{cool_str:<24}{used_str}")
    return 0


def cmd_save(storage: StorageEngine, name: str, email: Optional[str], json_out: bool) -> int:
    if not storage.live_token.exists():
        msg = f"Error: Active token file not found at {storage.live_token}"
        print(msg, file=sys.stderr)
        return 1

    project_id = None
    plan_type = "STANDARD"

    token_data = _read_token_data(storage.live_token)
    access_token = _get_or_refresh_access_token(storage.live_token)

    if access_token:
        client = QuotaClient(token=access_token)
        if not email:
            try:
                email = client.fetch_email()
            except Exception:
                pass
        try:
            meta = client.load_metadata()
            project_id = meta.get("project_id")
            plan_type = meta.get("plan_name", "STANDARD")
        except Exception:
            pass

    try:
        acct = storage.save_profile(
            name=name,
            email=email,
            project_id=project_id,
            plan_type=plan_type,
        )
    except Exception as e:
        print(f"Error saving profile: {e}", file=sys.stderr)
        return 1

    if json_out:
        print(json.dumps(acct.to_dict(), indent=2))
    else:
        print(f"Saved active profile as '{name}' ({acct.email or 'no email'}).")
    return 0


def cmd_switch(storage: StorageEngine, name: str, json_out: bool) -> int:
    success = storage.switch_profile(name)
    if success:
        if json_out:
            print(json.dumps({"success": True, "active": name}, indent=2))
        else:
            print(f"Switched active profile to '{name}'.")
        return 0
    else:
        if json_out:
            print(json.dumps({"success": False, "error": f"Account '{name}' not found or token missing"}, indent=2))
        print(f"Error: Account '{name}' not found or token missing", file=sys.stderr)
        return 1


def cmd_next(failover: FailoverEngine, reason: str, cooldown: int, json_out: bool) -> int:
    res = failover.rotate_next(reason=reason, cooldown_minutes=cooldown)
    result_dict = {
        "success": res.success,
        "from_account": res.from_account,
        "to_account": res.to_account,
        "reason": res.reason,
        "cooldown_until": res.cooldown_until.isoformat() if res.cooldown_until else None,
    }
    if json_out:
        print(json.dumps(result_dict, indent=2))
    else:
        if res.success:
            print(f"Rotated from '{res.from_account}' to '{res.to_account}' (reason: {res.reason}).")
        else:
            print(f"Rotation failed: {res.reason}", file=sys.stderr)

    return 0 if res.success else 1


def cmd_whoami(storage: StorageEngine, json_out: bool) -> int:
    active_name = storage.get_active_profile()
    if not active_name:
        if json_out:
            print(json.dumps({"active": None, "email": None, "expiry": None}, indent=2))
        else:
            print("No active profile.", file=sys.stderr)
        return 1

    acct = storage.get_account(active_name)
    token_data = _read_token_data(storage.live_token)
    if not token_data:
        token_path = storage.accounts_dir / active_name / "antigravity-oauth-token"
        token_data = _read_token_data(token_path)

    expiry = _extract_expiry(token_data)
    email = acct.email if acct else None

    if json_out:
        payload = {
            "active": active_name,
            "email": email,
            "expiry": expiry,
            "project_id": acct.project_id if acct else None,
            "plan_type": acct.plan_type if acct else None,
            "state": acct.state.value if acct else None,
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Active Profile: {active_name}")
    print(f"Email:          {email or '-'}")
    print(f"Token Expiry:   {expiry or '-'}")
    if acct:
        print(f"State:          {acct.state.value}")
        print(f"Project ID:     {acct.project_id or '-'}")
        print(f"Plan Type:      {acct.plan_type}")
    return 0


def cmd_remove(storage: StorageEngine, name: str, json_out: bool) -> int:
    success = storage.remove_profile(name)
    if success:
        if json_out:
            print(json.dumps({"success": True, "removed": name}, indent=2))
        else:
            print(f"Removed profile '{name}' from vault.")
        return 0
    else:
        if json_out:
            print(json.dumps({"success": False, "error": f"Account '{name}' not found"}, indent=2))
        print(f"Error: Account '{name}' not found", file=sys.stderr)
        return 1


def cmd_quota(storage: StorageEngine, name: Optional[str], json_out: bool) -> int:
    target_name = name or storage.get_active_profile()
    if not target_name:
        msg = "No account specified and no active profile."
        if json_out:
            print(json.dumps({"error": msg}, indent=2))
        print(f"Error: {msg}", file=sys.stderr)
        return 1

    acct = storage.get_account(target_name)
    if not acct:
        msg = f"Account '{target_name}' not found in vault."
        if json_out:
            print(json.dumps({"error": msg}, indent=2))
        print(f"Error: {msg}", file=sys.stderr)
        return 1

    # Locate token file
    if target_name == storage.get_active_profile() and storage.live_token.exists():
        token_path = storage.live_token
    else:
        token_path = storage.accounts_dir / target_name / "antigravity-oauth-token"

    token_data = _read_token_data(token_path)
    access_token = _get_or_refresh_access_token(token_path)
    if not access_token:
        access_token = _extract_access_token(token_data)
    if not access_token:
        msg = f"Could not extract access token for account '{target_name}'."
        if json_out:
            print(json.dumps({"error": msg}, indent=2))
        print(f"Error: {msg}", file=sys.stderr)
        return 1

    client = QuotaClient(token=access_token)
    project_id = acct.project_id
    if not project_id:
        try:
            meta = client.load_metadata()
            project_id = meta.get("project_id")
            if project_id:
                state = storage.load_state()
                if target_name in state.get("accounts", {}):
                    state["accounts"][target_name]["project_id"] = project_id
                    storage.save_state(state)
        except Exception as e:
            msg = f"Failed to load project metadata for '{target_name}': {e}"
            if json_out:
                print(json.dumps({"error": msg}, indent=2))
            print(f"Error: {msg}", file=sys.stderr)
            return 1

    if not project_id:
        msg = f"Could not determine project ID for account '{target_name}'."
        if json_out:
            print(json.dumps({"error": msg}, indent=2))
        print(f"Error: {msg}", file=sys.stderr)
        return 1

    try:
        qs = client.get_quota(project_id)
    except Exception as e:
        if "401" in str(e):
            td = _read_token_data(token_path)
            refreshed = _refresh_token_file(token_path, td) if td else None
            if refreshed:
                client.token = refreshed
                try:
                    qs = client.get_quota(project_id)
                except Exception as retry_e:
                    msg = f"Error retrieving quota: {retry_e}"
                    if json_out:
                        print(json.dumps({"error": msg}, indent=2))
                    print(f"Error: {msg}", file=sys.stderr)
                    return 1
            else:
                msg = f"Error retrieving quota: {e}"
                if json_out:
                    print(json.dumps({"error": msg}, indent=2))
                print(f"Error: {msg}", file=sys.stderr)
                return 1
        else:
            msg = f"Error retrieving quota: {e}"
            if json_out:
                print(json.dumps({"error": msg}, indent=2))
            print(f"Error: {msg}", file=sys.stderr)
            return 1

    if json_out:
        res = {
            "account": target_name,
            "project_id": project_id,
            "gemini": {
                "5h_remaining": qs.gemini_5h_remaining,
                "5h_reset": qs.gemini_5h_reset,
                "weekly_remaining": qs.gemini_weekly_remaining,
                "weekly_reset": qs.gemini_weekly_reset,
            },
            "third_party": {
                "5h_remaining": qs.third_party_5h_remaining,
                "5h_reset": qs.third_party_5h_reset,
                "weekly_remaining": qs.third_party_weekly_remaining,
                "weekly_reset": qs.third_party_weekly_reset,
            },
        }
        print(json.dumps(res, indent=2))
        return 0

    print(f"Quota for account '{target_name}' (project: {project_id}):\n")
    header = f"{'MODEL GROUP':<24} {'WINDOW':<10} {'REMAINING':<12} {'RESET TIME'}"
    divider = "-" * 70
    print(header)
    print(divider)
    g_5h = f"{qs.gemini_5h_remaining * 100:.1f}%"
    g_wk = f"{qs.gemini_weekly_remaining * 100:.1f}%"
    tp_5h = f"{qs.third_party_5h_remaining * 100:.1f}%"
    tp_wk = f"{qs.third_party_weekly_remaining * 100:.1f}%"

    print(f"{'Gemini Models':<24} {'5h':<10} {g_5h:<12} {qs.gemini_5h_reset or '-'}")
    print(f"{'Gemini Models':<24} {'weekly':<10} {g_wk:<12} {qs.gemini_weekly_reset or '-'}")
    print(f"{'Claude & GPT Models':<24} {'5h':<10} {tp_5h:<12} {qs.third_party_5h_reset or '-'}")
    print(f"{'Claude & GPT Models':<24} {'weekly':<10} {tp_wk:<12} {qs.third_party_weekly_reset or '-'}")
    return 0


def cmd_watch(failover: FailoverEngine, poll_seconds: float, cooldown: Optional[int]) -> int:
    gemini_home = None
    if "PROMUX_GEMINI_HOME" in os.environ:
        gemini_home = Path(os.environ["PROMUX_GEMINI_HOME"])
    elif hasattr(failover, "storage") and hasattr(failover.storage, "gemini_home"):
        gemini_home = failover.storage.gemini_home

    watcher = LogWatcher(
        failover=failover,
        poll_seconds=poll_seconds,
        gemini_home=gemini_home,
    )

    def on_match(match: LogMatch):
        print(f"[promux-watch] Quota exhausted: {match.pattern} in {match.file_path}")
        if match.reset_hint:
            print(f"[promux-watch] Reset hint detected: {match.reset_hint}")

    cool_str = f"{cooldown}m" if cooldown else "auto"
    print(f"[promux-watch] Starting log watcher (poll: {poll_seconds}s, cooldown: {cool_str})...")
    print("[promux-watch] Press Ctrl+C to stop.")

    try:
        watcher.run_forever(on_match=on_match, cooldown_minutes=cooldown)
    except KeyboardInterrupt:
        pass
    print("[promux-watch] Watcher stopped.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output in JSON format")

    parser = argparse.ArgumentParser(
        prog="promux",
        description="Antigravity CLI profile multiplexer & quota failover daemon",
        parents=[common_parser],
    )

    sub = parser.add_subparsers(dest="command", required=True, help="Subcommand to execute")

    # list
    sub.add_parser("list", parents=[common_parser], help="List all accounts in the vault")

    # save
    save_p = sub.add_parser("save", parents=[common_parser], help="Save active token as a named profile")
    save_p.add_argument("name", help="Profile name")
    save_p.add_argument("--email", default=None, help="Account email (auto-detected if omitted)")

    # switch
    switch_p = sub.add_parser("switch", parents=[common_parser], help="Hot-swap to a named profile")
    switch_p.add_argument("name", help="Profile name to switch to")

    # next
    next_p = sub.add_parser("next", parents=[common_parser], help="Rotate to next eligible standby profile")
    next_p.add_argument("--reason", default="manual", help="Reason for rotation (default: manual)")
    next_p.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN_MINUTES, help=f"Cooldown duration in minutes (default: {DEFAULT_COOLDOWN_MINUTES})")

    # quota
    quota_p = sub.add_parser("quota", parents=[common_parser], help="Check Cloud Code Assist quota")
    quota_p.add_argument("name", nargs="?", default=None, help="Profile name (default: active profile)")

    # whoami
    sub.add_parser("whoami", parents=[common_parser], help="Show active profile and token status")

    # remove
    remove_p = sub.add_parser("remove", parents=[common_parser], help="Remove a profile from the vault")
    remove_p.add_argument("name", help="Profile name to remove")

    # watch
    watch_p = sub.add_parser("watch", parents=[common_parser], help="Start reactive log tailer daemon")
    watch_p.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS, help=f"Log polling interval in seconds (default: {DEFAULT_POLL_SECONDS})")
    watch_p.add_argument("--cooldown", type=int, default=None, help="Cooldown override in minutes (default: auto-detected from log hints)")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entry point returning status code."""
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    storage = get_storage()
    failover = FailoverEngine(storage)
    json_out = getattr(args, "json", False)

    try:
        if args.command == "list":
            return cmd_list(storage, json_out)
        elif args.command == "save":
            return cmd_save(storage, args.name, args.email, json_out)
        elif args.command == "switch":
            return cmd_switch(storage, args.name, json_out)
        elif args.command == "next":
            return cmd_next(failover, args.reason, args.cooldown, json_out)
        elif args.command == "quota":
            return cmd_quota(storage, args.name, json_out)
        elif args.command == "whoami":
            return cmd_whoami(storage, json_out)
        elif args.command == "remove":
            return cmd_remove(storage, args.name, json_out)
        elif args.command == "watch":
            return cmd_watch(failover, args.poll_seconds, args.cooldown)
        else:
            parser.print_help(file=sys.stderr)
            return 2
    except Exception as e:
        if json_out:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
