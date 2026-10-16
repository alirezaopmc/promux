"""Quota and token refresh commands."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import sys
from typing import Any

from ..auth import (
    _get_last_refresh_method,
    _get_last_refresh_revoked,
    _refresh_token_file,
    _set_last_refresh_revoked,
)
from ..auth import (
    atomic_copy_token as _atomic_copy_token,
)
from ..auth import (
    extract_access_token as _extract_access_token,
)
from ..auth import (
    extract_expiry as _extract_expiry,
)
from ..auth import (
    extract_refresh_token as _extract_refresh_token,
)
from ..auth import (
    get_or_refresh_access_token as _get_or_refresh_access_token,
)
from ..auth import (
    is_token_expired as _is_token_expired,
)
from ..auth import (
    parse_token_expiry_dt as _parse_token_expiry_dt,
)
from ..auth import (
    read_token_data as _read_token_data,
)
from ..auth import (
    sync_active_tokens as _sync_active_tokens,
)
from ..cache import QuotaCache
from ..formatters import (
    format_expiry_table,
    format_quota_cell,
    format_quota_detail,
    format_relative_countdown,
)
from ..models import AccountMeta, QuotaSummary
from ..quota import QuotaClient
from ..storage import StorageEngine

logger = logging.getLogger(__name__)


def _fetch_account_quota(
    storage: StorageEngine,
    target_name: str,
    no_cache: bool = False,
    cache: QuotaCache | None = None,
) -> tuple[QuotaSummary | None, str | None, str | None, bool]:
    """Fetch quota for an account, handling token resolution, refresh, and project ID discovery.

    Returns:
        tuple of (QuotaSummary or None, project_id or None, error_message or None, is_cached)
    """
    if cache is None:
        cache = QuotaCache(storage.home)

    _set_last_refresh_revoked(False)

    acct = storage.get_account(target_name)
    if not acct:
        return None, None, f"Account '{target_name}' not found in vault.", False

    if not no_cache:
        cached_qs = cache.get(target_name)
        if cached_qs is not None:
            return cached_qs, acct.project_id, None, True

    # Locate token file
    is_active = target_name == storage.get_active_profile()
    vault_token_path = storage.accounts_dir / target_name / "antigravity-oauth-token"

    if is_active and storage.live_token.exists():
        token_path = storage.live_token
    else:
        token_path = vault_token_path

    token_data = _read_token_data(token_path)
    orig_acc = _extract_access_token(token_data)
    orig_exp = _extract_expiry(token_data)

    access_token = _get_or_refresh_access_token(token_path, storage=storage)

    # If token was refreshed, sync between live and vault for active profile
    new_data = _read_token_data(token_path)
    new_acc = _extract_access_token(new_data)
    new_exp = _extract_expiry(new_data)
    token_refreshed = bool(
        new_acc and (orig_acc is None or new_acc != orig_acc or new_exp != orig_exp)
    )

    if is_active and token_refreshed:
        if token_path == storage.live_token:
            _sync_active_tokens(storage.live_token, vault_token_path)
        elif token_path == vault_token_path:
            _sync_active_tokens(vault_token_path, storage.live_token)

    if not access_token:
        access_token = new_acc or orig_acc

    if not access_token:
        if _get_last_refresh_revoked():
            cache.invalidate(target_name)
            return (
                None,
                None,
                f"Refresh token for '{target_name}' has expired or been revoked. "
                f"Please re-authenticate via 'agy' and save using 'promux save {target_name}'.",
                False,
            )
        return None, None, f"Could not extract access token for account '{target_name}'.", False

    client = QuotaClient(token=access_token)
    project_id = acct.project_id
    if not project_id:
        try:
            meta = client.load_metadata()
            project_id = meta.get("project_id")
            if project_id:
                with storage.transaction() as state:
                    if target_name in state.get("accounts", {}):
                        state["accounts"][target_name]["project_id"] = project_id
        except Exception as e:
            if _get_last_refresh_revoked():
                cache.invalidate(target_name)
                return (
                    None,
                    None,
                    f"Refresh token for '{target_name}' has expired or been revoked. "
                    f"Please re-authenticate via 'agy' and save using 'promux save {target_name}'.",
                    False,
                )
            return None, None, f"Failed to load project metadata for '{target_name}': {e}", False

    if not project_id:
        return None, None, f"Could not determine project ID for account '{target_name}'.", False

    try:
        qs = client.get_quota(project_id)
        cache.set(target_name, qs)
        return qs, project_id, None, False
    except Exception as e:
        if "401" in str(e):
            td = _read_token_data(token_path)
            refreshed = _refresh_token_file(token_path, td, storage=storage) if td else None
            if refreshed:
                if is_active:
                    if token_path == storage.live_token:
                        _sync_active_tokens(storage.live_token, vault_token_path)
                    elif token_path == vault_token_path:
                        _sync_active_tokens(vault_token_path, storage.live_token)
                client.token = refreshed
                try:
                    qs = client.get_quota(project_id)
                    cache.set(target_name, qs)
                    return qs, project_id, None, False
                except Exception as retry_e:
                    if "401" in str(retry_e):
                        cache.invalidate(target_name)
                    return None, project_id, f"Error retrieving quota: {retry_e}", False
            else:
                cache.invalidate(target_name)
                if _get_last_refresh_revoked():
                    err_msg = (
                        f"Authentication failed (401): Refresh token for '{target_name}' "
                        "has expired or been revoked. Please re-authenticate via 'agy' "
                        f"and save using 'promux save {target_name}'."
                    )
                    return (
                        None,
                        project_id,
                        err_msg,
                        False,
                    )
                return None, project_id, f"Authentication failed (401): {e}", False
        return None, project_id, f"Error retrieving quota: {e}", False


def _dispatch_fetch_quota(
    storage: StorageEngine,
    name: str,
    no_cache: bool = False,
    cache: QuotaCache | None = None,
) -> tuple[QuotaSummary | None, str | None, str | None, bool]:
    """Dispatch fetch quota respecting any monkeypatched promux.cli._fetch_account_quota."""
    cli_mod = sys.modules.get("promux.cli")
    fn = (
        getattr(cli_mod, "_fetch_account_quota", _fetch_account_quota)
        if cli_mod
        else _fetch_account_quota
    )
    if fn is _fetch_account_quota:
        return _fetch_account_quota(storage, name, no_cache=no_cache, cache=cache)
    try:
        res = fn(storage, name, no_cache=no_cache, cache=cache)
    except TypeError:
        res = fn(storage, name)
    if len(res) == 4:
        return res[0], res[1], res[2], res[3]
    return res[0], res[1], res[2], False


def _dispatch_refresh_account(
    storage: StorageEngine,
    account_name: str,
    force: bool = False,
) -> tuple[bool, str]:
    """Dispatch refresh account respecting any monkeypatched promux.cli._refresh_account_token."""
    cli_mod = sys.modules.get("promux.cli")
    fn = (
        getattr(cli_mod, "_refresh_account_token", _refresh_account_token)
        if cli_mod
        else _refresh_account_token
    )
    return fn(storage, account_name, force=force)


def _refresh_account_token(
    storage: StorageEngine,
    account_name: str,
    force: bool = False,
) -> tuple[bool, str]:
    """Execute token refresh for a named account in storage.

    Returns:
        tuple of (success: bool, message: str)
    """
    if not storage.get_account(account_name):
        return False, f"Account '{account_name}' not found in vault"

    active_profile = storage.get_active_profile()
    is_active = account_name == active_profile
    vault_token_path = storage.accounts_dir / account_name / "antigravity-oauth-token"

    if is_active and storage.live_token.exists():
        if not vault_token_path.exists():
            _atomic_copy_token(storage.live_token, vault_token_path)
        else:
            live_data = _read_token_data(storage.live_token)
            vault_data = _read_token_data(vault_token_path)
            lr = _extract_refresh_token(live_data)
            vr = _extract_refresh_token(vault_data)
            if not (lr and vr and lr != vr):
                live_dt = _parse_token_expiry_dt(_extract_expiry(live_data))
                vault_dt = _parse_token_expiry_dt(_extract_expiry(vault_data))
                if live_dt and vault_dt and live_dt > vault_dt:
                    _atomic_copy_token(storage.live_token, vault_token_path)

    token_path = vault_token_path
    token_data = _read_token_data(token_path)
    if not token_data and is_active and storage.live_token.exists():
        token_data = _read_token_data(storage.live_token)

    if not token_data:
        return False, f"No token data found for account '{account_name}'."

    if not force and not _is_token_expired(token_data):
        return True, "Token is still valid (not expired)."

    refreshed = _refresh_token_file(token_path, token_data, storage=storage)
    if refreshed:
        new_data = _read_token_data(token_path)
        if is_active:
            try:
                should_sync_live = True
                if storage.live_token.exists():
                    curr_live_data = _read_token_data(storage.live_token)
                    curr_lr = _extract_refresh_token(curr_live_data)
                    ref_lr = _extract_refresh_token(new_data) or _extract_refresh_token(token_data)
                    if curr_lr and ref_lr and curr_lr != ref_lr:
                        should_sync_live = False
                if should_sync_live:
                    _atomic_copy_token(token_path, storage.live_token)
            except Exception:
                logger.debug("Failed to sync live token during refresh", exc_info=True)
        method = (
            _get_last_refresh_method()
            if _get_last_refresh_method() in ("native", "fallback (agy)")
            else "native"
        )
        return True, f"Token refreshed successfully ({method})."
    else:
        return False, f"Failed to refresh token ({_get_last_refresh_method()})."


def cmd_quota(
    storage: StorageEngine,
    name: str | None,
    json_out: bool,
    no_cache: bool = False,
) -> int:
    active_profile = storage.get_active_profile()
    cache = QuotaCache(storage.home)

    # Single-profile detail mode
    if name is not None:
        qs, project_id, err, is_cached = _dispatch_fetch_quota(
            storage, name, no_cache=no_cache, cache=cache
        )
        if err or not qs:
            msg = err or f"Could not retrieve quota for '{name}'."
            if json_out:
                print(json.dumps({"error": msg}, indent=2))
            else:
                print(f"Error: {msg}", file=sys.stderr)
            return 1

        if json_out:
            res_dict = {
                "account": name,
                "project_id": project_id,
                "cached": is_cached,
                "gemini": {
                    "5h_remaining": qs.gemini_5h_remaining,
                    "5h_reset": qs.gemini_5h_reset,
                    "5h_reset_relative": format_relative_countdown(qs.gemini_5h_reset),
                    "weekly_remaining": qs.gemini_weekly_remaining,
                    "weekly_reset": qs.gemini_weekly_reset,
                    "weekly_reset_relative": format_relative_countdown(qs.gemini_weekly_reset),
                },
                "third_party": {
                    "5h_remaining": qs.third_party_5h_remaining,
                    "5h_reset": qs.third_party_5h_reset,
                    "5h_reset_relative": format_relative_countdown(qs.third_party_5h_reset),
                    "weekly_remaining": qs.third_party_weekly_remaining,
                    "weekly_reset": qs.third_party_weekly_reset,
                    "weekly_reset_relative": format_relative_countdown(qs.third_party_weekly_reset),
                },
            }
            print(json.dumps(res_dict, indent=2))
            return 0

        active_tag = " [ACTIVE]" if name == active_profile else ""
        print(f"Quota for account '{name}' (project: {project_id}){active_tag}:\n")
        header = f"{'MODEL GROUP':<24} {'WINDOW':<10} {'REMAINING & RESET'}"
        print(header)
        print("-" * 80)

        g_5h = format_quota_detail(qs.gemini_5h_remaining, qs.gemini_5h_reset)
        g_wk = format_quota_detail(qs.gemini_weekly_remaining, qs.gemini_weekly_reset)
        c_5h = format_quota_detail(qs.third_party_5h_remaining, qs.third_party_5h_reset)
        c_wk = format_quota_detail(qs.third_party_weekly_remaining, qs.third_party_weekly_reset)

        print(f"{'Gemini Models':<24} {'5h':<10} {g_5h}")
        print(f"{'Gemini Models':<24} {'weekly':<10} {g_wk}")
        print(f"{'Claude & GPT Models':<24} {'5h':<10} {c_5h}")
        print(f"{'Claude & GPT Models':<24} {'weekly':<10} {c_wk}")
        return 0

    # Multi-profile overview mode
    accounts = storage.list_accounts()
    if not accounts:
        msg = "No accounts registered in vault."
        if json_out:
            print(json.dumps([], indent=2))
        else:
            print(msg)
        return 0

    json_records = []
    text_rows = []

    def fetch_for_account(
        acct_meta: AccountMeta,
    ) -> tuple[str, bool, QuotaSummary | None, str | None, str | None, bool]:
        acct_name = acct_meta.name
        is_active = acct_name == active_profile
        qs, project_id, err, is_cached = _dispatch_fetch_quota(
            storage, acct_name, no_cache=no_cache, cache=cache
        )
        return acct_name, is_active, qs, project_id, err, is_cached

    max_workers = min(20, max(1, len(accounts)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for (
            acct_name,
            is_active,
            qs,
            project_id,
            err,
            is_cached,
        ) in executor.map(fetch_for_account, accounts):
            if json_out:
                record: dict[str, Any] = {
                    "account": acct_name,
                    "active": is_active,
                    "project_id": project_id,
                    "cached": is_cached,
                }
                if qs:
                    record["gemini"] = {
                        "5h_remaining": qs.gemini_5h_remaining,
                        "5h_reset": qs.gemini_5h_reset,
                        "5h_reset_relative": format_relative_countdown(qs.gemini_5h_reset),
                        "weekly_remaining": qs.gemini_weekly_remaining,
                        "weekly_reset": qs.gemini_weekly_reset,
                        "weekly_reset_relative": format_relative_countdown(qs.gemini_weekly_reset),
                    }
                    record["third_party"] = {
                        "5h_remaining": qs.third_party_5h_remaining,
                        "5h_reset": qs.third_party_5h_reset,
                        "5h_reset_relative": format_relative_countdown(qs.third_party_5h_reset),
                        "weekly_remaining": qs.third_party_weekly_remaining,
                        "weekly_reset": qs.third_party_weekly_reset,
                        "weekly_reset_relative": format_relative_countdown(
                            qs.third_party_weekly_reset
                        ),
                    }
                else:
                    record["error"] = err
                json_records.append(record)
            else:
                active_mark = "*" if is_active else ""
                if qs:
                    g5 = format_quota_cell(qs.gemini_5h_remaining, qs.gemini_5h_reset)
                    gw = format_quota_cell(qs.gemini_weekly_remaining, qs.gemini_weekly_reset)
                    c5 = format_quota_cell(qs.third_party_5h_remaining, qs.third_party_5h_reset)
                    cw = format_quota_cell(
                        qs.third_party_weekly_remaining, qs.third_party_weekly_reset
                    )
                else:
                    g5 = "[AUTH ERROR]" if err and "401" in str(err) else "[ERROR]"
                    gw = "-"
                    c5 = "-"
                    cw = "-"
                text_rows.append((active_mark, acct_name, g5, gw, c5, cw))

    if json_out:
        print(json.dumps(json_records, indent=2))
        return 0

    header = (
        f"{'ACTIVE':<7} {'PROFILE':<17} {'GEMINI (5H)':<20} {'GEMINI (WK)':<20} "
        f"{'CLAUDE (5H)':<20} {'CLAUDE (WK)':<20}"
    )
    print(header)
    print("-" * len(header))
    for active_mark, acct_name, g5, gw, c5, cw in text_rows:
        print(f"{active_mark:<7} {acct_name:<17} {g5:<20} {gw:<20} {c5:<20} {cw:<20}")

    return 0


def cmd_refresh(
    storage: StorageEngine,
    name: str | None,
    force: bool,
    json_out: bool,
) -> int:
    """Refresh OAuth access token for vault accounts."""
    active_profile = storage.get_active_profile()

    if name is not None:
        if not storage.get_account(name):
            msg = f"Account '{name}' not found in vault"
            if json_out:
                print(json.dumps({"error": msg}, indent=2))
            else:
                print(f"Error: {msg}", file=sys.stderr)
            return 1
        targets = [name]
    else:
        accounts = storage.list_accounts()
        if not accounts:
            if json_out:
                print(json.dumps([], indent=2))
            else:
                print("No accounts registered in vault.")
            return 0
        targets = [acct.name for acct in accounts]

    results: list[dict[str, Any]] = []
    any_failed = False

    for target_name in targets:
        is_active = target_name == active_profile
        vault_token_path = storage.accounts_dir / target_name / "antigravity-oauth-token"

        if is_active and storage.live_token.exists():
            if not vault_token_path.exists():
                _atomic_copy_token(storage.live_token, vault_token_path)
            else:
                live_data = _read_token_data(storage.live_token)
                vault_data = _read_token_data(vault_token_path)
                lr = _extract_refresh_token(live_data)
                vr = _extract_refresh_token(vault_data)
                if not (lr and vr and lr != vr):
                    live_dt = _parse_token_expiry_dt(_extract_expiry(live_data))
                    vault_dt = _parse_token_expiry_dt(_extract_expiry(vault_data))
                    if live_dt and vault_dt and live_dt > vault_dt:
                        _atomic_copy_token(storage.live_token, vault_token_path)

        token_path = vault_token_path
        token_data = _read_token_data(token_path)
        if not token_data and is_active and storage.live_token.exists():
            token_data = _read_token_data(storage.live_token)

        if not token_data:
            results.append(
                {
                    "account": target_name,
                    "status": "FAILED",
                    "expiry": None,
                    "method": "failed",
                }
            )
            any_failed = True
            continue

        if not force and not _is_token_expired(token_data):
            results.append(
                {
                    "account": target_name,
                    "status": "UNCHANGED",
                    "expiry": _extract_expiry(token_data),
                    "method": "valid",
                }
            )
        else:
            refreshed = _refresh_token_file(token_path, token_data, storage=storage)
            if refreshed:
                method = (
                    _get_last_refresh_method()
                    if _get_last_refresh_method() in ("native", "fallback (agy)")
                    else "native"
                )
                new_data = _read_token_data(token_path)
                new_expiry = _extract_expiry(new_data) or _extract_expiry(token_data)
                results.append(
                    {
                        "account": target_name,
                        "status": "REFRESHED",
                        "expiry": new_expiry,
                        "method": method,
                    }
                )
                if is_active:
                    try:
                        should_sync_live = True
                        if storage.live_token.exists():
                            curr_live_data = _read_token_data(storage.live_token)
                            curr_lr = _extract_refresh_token(curr_live_data)
                            ref_lr = _extract_refresh_token(new_data) or _extract_refresh_token(
                                token_data
                            )
                            if curr_lr and ref_lr and curr_lr != ref_lr:
                                should_sync_live = False
                        if should_sync_live:
                            _atomic_copy_token(token_path, storage.live_token)
                    except Exception:
                        logger.debug("Failed to sync live token during refresh", exc_info=True)
            else:
                results.append(
                    {
                        "account": target_name,
                        "status": "FAILED",
                        "expiry": _extract_expiry(token_data),
                        "method": "failed",
                    }
                )
                any_failed = True

    if json_out:
        print(json.dumps(results, indent=2))
        return 1 if any_failed else 0

    header = f"{'ACCOUNT':<18}{'STATUS':<13}{'EXPIRY (UTC)':<26}{'METHOD'}"
    divider = "-" * 65
    print(header)
    print(divider)
    for r in results:
        disp_exp = format_expiry_table(r["expiry"])
        print(f"{r['account']:<18}{r['status']:<13}{disp_exp:<26}{r['method']}")

    return 1 if any_failed else 0
