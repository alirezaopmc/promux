"""Profile management commands: list, save, switch, next, whoami, remove."""

from __future__ import annotations

import json
import logging
import sys

from ..auth import (
    extract_expiry as _extract_expiry,
)
from ..auth import (
    get_or_refresh_access_token as _get_or_refresh_access_token,
)
from ..auth import (
    read_token_data as _read_token_data,
)
from ..cache import QuotaCache
from ..failover import FailoverEngine
from ..models import AccountMeta, QuotaSummary
from ..quota import QuotaClient
from ..storage import StorageEngine
from .quota import _dispatch_fetch_quota

logger = logging.getLogger(__name__)


def cmd_list(storage: StorageEngine, json_out: bool) -> int:
    state = storage.load_state()
    active = state.get("active")
    accounts_dict = state.get("accounts", {})

    rows = []
    for name, data in accounts_dict.items():
        acct = AccountMeta.from_dict(data)
        is_active = name == active
        rows.append(
            {
                "name": name,
                "active": is_active,
                "state": acct.state.value,
                "email": acct.email,
                "cooldown_until": acct.cooldown_until.isoformat() if acct.cooldown_until else None,
                "last_used": acct.last_used_at.isoformat() if acct.last_used_at else None,
                "last_used_at": acct.last_used_at.isoformat() if acct.last_used_at else None,
            }
        )

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


def cmd_save(storage: StorageEngine, name: str, email: str | None, json_out: bool) -> int:
    if not storage.live_token.exists():
        msg = f"Error: Active token file not found at {storage.live_token}"
        print(msg, file=sys.stderr)
        return 1

    project_id = None
    plan_type = "STANDARD"

    access_token = _get_or_refresh_access_token(storage.live_token)

    if access_token:
        client = QuotaClient(token=access_token)
        if not email:
            try:
                email = client.fetch_email()
            except Exception:
                logger.debug("Failed to auto-fetch email", exc_info=True)
        try:
            meta = client.load_metadata()
            project_id = meta.get("project_id")
            plan_type = meta.get("plan_name", "STANDARD")
        except Exception:
            logger.debug("Failed to auto-fetch metadata", exc_info=True)

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


def cmd_switch(
    storage: StorageEngine,
    name: str | None,
    json_out: bool,
    smart: bool = False,
    model: str = "gemini",
    failover: FailoverEngine | None = None,
    no_cache: bool = False,
) -> int:
    if smart:
        if failover is None:
            failover = FailoverEngine(storage)
        cache = QuotaCache(storage.home)

        def _fetch_wrapper(acct: str) -> tuple[QuotaSummary | None, str | None, str | None]:
            res = _dispatch_fetch_quota(storage, acct, no_cache=no_cache, cache=cache)
            return res[0], res[1], res[2]

        res = failover.rotate_smart(
            quota_fetcher=_fetch_wrapper,
            model=model,
        )
        if res.success:
            if json_out:
                print(
                    json.dumps(
                        {
                            "success": True,
                            "from_account": res.from_account,
                            "to_account": res.to_account,
                            "model": res.model,
                            "quota": {
                                "5h_remaining": res.five_hour_remaining,
                                "weekly_remaining": res.weekly_remaining,
                            },
                            "cooldown_until": (
                                res.cooldown_until.isoformat() if res.cooldown_until else None
                            ),
                        },
                        indent=2,
                    )
                )
            else:
                five_pct = (
                    f"{res.five_hour_remaining * 100:.1f}%"
                    if res.five_hour_remaining is not None
                    else "-"
                )
                wk_pct = (
                    f"{res.weekly_remaining * 100:.1f}%"
                    if res.weekly_remaining is not None
                    else "-"
                )
                msg = (
                    f"Switched active profile to '{res.to_account}' "
                    f"({res.model} 5h: {five_pct}, weekly: {wk_pct})."
                )
                if res.cooldown_until and res.from_account:
                    cd_str = res.cooldown_until.strftime("%H:%M UTC")
                    msg += f" (Cooldown set for '{res.from_account}' until {cd_str}.)"
                print(msg)
            return 0
        else:
            if json_out:
                print(
                    json.dumps(
                        {"success": False, "error": res.reason},
                        indent=2,
                    )
                )
            else:
                print(f"Error: {res.reason}", file=sys.stderr)
            return 1

    if not name:
        if json_out:
            print(
                json.dumps(
                    {"success": False, "error": "must specify account name or pass --smart"},
                    indent=2,
                )
            )
        else:
            print("Error: must specify account name or pass --smart", file=sys.stderr)
        return 2

    success = storage.switch_profile(name)
    if success:
        if json_out:
            print(json.dumps({"success": True, "active": name}, indent=2))
        else:
            print(f"Switched active profile to '{name}'.")
        return 0
    else:
        if json_out:
            print(
                json.dumps(
                    {"success": False, "error": f"Account '{name}' not found or token missing"},
                    indent=2,
                )
            )
        else:
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
            print(
                f"Rotated from '{res.from_account}' to '{res.to_account}' (reason: {res.reason})."
            )
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
        QuotaCache(storage.home).invalidate(name)
        if json_out:
            print(json.dumps({"success": True, "removed": name}, indent=2))
        else:
            print(f"Removed profile '{name}' from vault.")
        return 0
    else:
        if json_out:
            print(json.dumps({"success": False, "error": f"Account '{name}' not found"}, indent=2))
        else:
            print(f"Error: Account '{name}' not found", file=sys.stderr)
        return 1
