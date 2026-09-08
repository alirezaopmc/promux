import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .completion import (
    SUPPORTED_SHELLS,
    generate_bash_completion,
    generate_zsh_completion,
)
from .lock import file_lock
from .constants import (
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_POLL_SECONDS,
    DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_TOKEN_URL,
    PROMUX_HOME,
)
from .failover import FailoverEngine
from .models import AccountMeta, QuotaSummary
from .quota import QuotaClient
from .storage import StorageEngine
from .watch import LogMatch, LogWatcher


def get_storage() -> StorageEngine:
    """Instantiate StorageEngine honoring runtime environment overrides."""
    promux_home = Path(os.environ["PROMUX_HOME"]) if "PROMUX_HOME" in os.environ else None
    gemini_home = (
        Path(os.environ["PROMUX_GEMINI_HOME"]) if "PROMUX_GEMINI_HOME" in os.environ else None
    )
    return StorageEngine(promux_home=promux_home, gemini_home=gemini_home)


def _read_token_data(token_path: Path) -> dict[str, Any] | None:
    """Safely read and parse OAuth token JSON."""
    if not token_path.exists():
        return None
    try:
        with open(token_path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _extract_access_token(token_data: dict[str, Any] | None) -> str | None:
    """Extract access_token string from raw token dictionary."""
    if not token_data:
        return None
    tok = token_data.get("token")
    if isinstance(tok, dict):
        return tok.get("access_token")
    if "access_token" in token_data:
        return token_data.get("access_token")
    return None


def _extract_expiry(token_data: dict[str, Any] | None) -> str | None:
    """Extract expiry timestamp from raw token dictionary."""
    if not token_data:
        return None
    tok = token_data.get("token")
    if isinstance(tok, dict):
        return tok.get("expiry")
    if "expiry" in token_data:
        return token_data.get("expiry")
    return None


def _extract_refresh_token(token_data: dict[str, Any] | None) -> str | None:
    """Extract refresh_token string from raw token dictionary."""
    if not token_data:
        return None
    tok = token_data.get("token")
    if isinstance(tok, dict):
        return tok.get("refresh_token")
    if "refresh_token" in token_data:
        return token_data.get("refresh_token")
    return None


def _atomic_copy_token(src: Path, dst: Path) -> None:
    """Atomically copy a token file ensuring 0600 permissions."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_dst = dst.with_suffix(f".tmp_{os.getpid()}")
    shutil.copy2(src, tmp_dst)
    tmp_dst.chmod(0o600)
    os.replace(tmp_dst, dst)


def _sync_active_tokens(src: Path, dst: Path) -> None:
    """Atomically copy refreshed token from src to dst if tokens share identity or dst is missing."""
    try:
        if not src.exists():
            return
        if dst.exists():
            src_td = _read_token_data(src)
            dst_td = _read_token_data(dst)
            sr = _extract_refresh_token(src_td)
            dr = _extract_refresh_token(dst_td)
            if sr and dr and sr != dr:
                return
        _atomic_copy_token(src, dst)
    except Exception:
        pass


def _get_oauth_credentials() -> tuple[str, str]:
    """Retrieve OAuth client ID and secret from environment or ~/.promux/oauth.json."""
    client_id = os.environ.get("PROMUX_OAUTH_CLIENT_ID", OAUTH_CLIENT_ID)
    client_secret = os.environ.get("PROMUX_OAUTH_CLIENT_SECRET", OAUTH_CLIENT_SECRET)
    if not client_id or not client_secret:
        cfg_file = PROMUX_HOME / "oauth.json"
        if cfg_file.exists():
            try:
                with open(cfg_file, encoding="utf-8") as f:
                    data = json.load(f)
                    client_id = client_id or data.get("client_id", "")
                    client_secret = client_secret or data.get("client_secret", "")
            except Exception:
                pass
    return client_id, client_secret


def _parse_token_expiry_dt(expiry_str: str | None) -> datetime | None:
    """Parse ISO8601 expiry string into timezone-aware UTC datetime."""
    if not expiry_str or not isinstance(expiry_str, str):
        return None
    try:
        clean_exp = expiry_str.replace("Z", "+00:00")
        exp_dt = datetime.fromisoformat(clean_exp)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return exp_dt
    except Exception:
        return None


def _is_token_expired(
    token_data: dict[str, Any] | None,
    buffer_seconds: int = DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS,
) -> bool:
    """Check if token is expired or within buffer_seconds of expiration."""
    if not token_data or not isinstance(token_data, dict):
        return True
    expiry_str = _extract_expiry(token_data)
    exp_dt = _parse_token_expiry_dt(expiry_str)
    if exp_dt is None:
        return True
    now = datetime.now(timezone.utc)
    return now + timedelta(seconds=buffer_seconds) >= exp_dt


def _refresh_token_native(token_path: Path, token_data: dict[str, Any]) -> str | None:
    """Tier 1: Execute direct HTTP OAuth refresh using Google OAuth token endpoint."""
    global _last_refresh_revoked
    if not isinstance(token_data, dict):
        return None
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
        data = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")

        req = urllib.request.Request(OAUTH_TOKEN_URL, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        new_acc = res.get("access_token") if isinstance(res, dict) else None
        if isinstance(new_acc, str):
            tok["access_token"] = new_acc
            expires_in = res.get("expires_in", 3600) if isinstance(res, dict) else 3600
            now = datetime.now(timezone.utc)
            tok["expiry"] = (now + timedelta(seconds=expires_in)).isoformat().replace("+00:00", "Z")
            if isinstance(res, dict) and "refresh_token" in res and isinstance(res["refresh_token"], str):
                tok["refresh_token"] = res["refresh_token"]
            tmp_path = token_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(token_data, f, indent=2)
            tmp_path.chmod(0o600)
            os.replace(tmp_path, token_path)
            return new_acc
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            if isinstance(body, dict) and body.get("error") == "invalid_grant":
                _last_refresh_revoked = True
        except Exception:
            if "invalid_grant" in str(e).lower():
                _last_refresh_revoked = True
    except Exception as e:
        if "invalid_grant" in str(e).lower():
            _last_refresh_revoked = True
    return None


def _refresh_token_fallback_agy(token_path: Path, storage: StorageEngine) -> str | None:
    """Tier 2: Fallback renewal via headless agy CLI under storage file lock."""
    if not shutil.which("agy"):
        return None
    if not token_path.exists():
        return None

    try:
        lock_ctx = (
            nullcontext()
            if getattr(storage, "_tx_state", None) is not None
            else file_lock(storage.lock_file)
        )
        with lock_ctx:
            old_data = _read_token_data(token_path)
            old_acc = _extract_access_token(old_data)
            old_exp = _extract_expiry(old_data)

            is_same = False
            try:
                is_same = token_path.resolve() == storage.live_token.resolve()
            except Exception:
                pass

            if is_same:
                try:
                    subprocess.run(["agy", "models"], capture_output=True, timeout=10)
                except (subprocess.SubprocessError, OSError):
                    pass
                new_data = _read_token_data(storage.live_token)
                new_acc = _extract_access_token(new_data)
                new_exp = _extract_expiry(new_data)
                if new_acc and (old_acc is None or new_acc != old_acc or new_exp != old_exp):
                    return new_acc
                return None

            storage.live_token.parent.mkdir(parents=True, exist_ok=True)
            backup_path = storage.live_token.with_name(
                f"{storage.live_token.name}.fallback_bak_{os.getpid()}"
            )
            live_existed = storage.live_token.exists()
            if live_existed:
                shutil.copy2(storage.live_token, backup_path)
                backup_path.chmod(0o600)

            refreshed_acc: str | None = None
            try:
                tmp_live = storage.live_token.with_name(f".tmp_live_{os.getpid()}")
                shutil.copy2(token_path, tmp_live)
                tmp_live.chmod(0o600)
                os.replace(tmp_live, storage.live_token)

                try:
                    subprocess.run(["agy", "models"], capture_output=True, timeout=10)
                except (subprocess.SubprocessError, OSError):
                    pass

                new_data = _read_token_data(storage.live_token)
                new_acc = _extract_access_token(new_data)
                new_exp = _extract_expiry(new_data)
                if new_acc and (old_acc is None or new_acc != old_acc or new_exp != old_exp):
                    tmp_path = token_path.with_suffix(f".tmp_{os.getpid()}")
                    shutil.copy2(storage.live_token, tmp_path)
                    tmp_path.chmod(0o600)
                    os.replace(tmp_path, token_path)
                    refreshed_acc = new_acc
            finally:
                if live_existed:
                    if backup_path.exists():
                        os.replace(backup_path, storage.live_token)
                        storage.live_token.chmod(0o600)
                else:
                    if storage.live_token.exists():
                        storage.live_token.unlink()
                    if backup_path.exists():
                        backup_path.unlink()

            return refreshed_acc
    except Exception:
        return None


_last_refresh_method: str = "native"
_last_refresh_revoked: bool = False


def _refresh_token_file(
    token_path: Path,
    token_data: dict[str, Any],
    storage: StorageEngine | None = None,
) -> str | None:
    """Attempt to refresh an expired token using its refresh_token or agy fallback."""
    global _last_refresh_method, _last_refresh_revoked
    _last_refresh_method = "failed"
    _last_refresh_revoked = False
    # Tier 1: Native HTTP refresh
    try:
        refreshed = _refresh_token_native(token_path, token_data)
    except Exception as e:
        if "invalid_grant" in str(e).lower():
            _last_refresh_revoked = True
        refreshed = None

    if refreshed:
        _last_refresh_method = "native"
        _last_refresh_revoked = False
        return refreshed
    if _last_refresh_revoked:
        _last_refresh_method = "revoked"
        return None
    # Tier 2: Headless agy fallback
    if storage is not None:
        refreshed = _refresh_token_fallback_agy(token_path, storage)
        if refreshed:
            _last_refresh_method = "fallback (agy)"
            return refreshed
    return None


def _get_or_refresh_access_token(
    token_path: Path,
    storage: StorageEngine | None = None,
) -> str | None:
    """Get access token, refreshing it if expired and refresh_token is present."""
    token_data = _read_token_data(token_path)
    if not token_data:
        return None
    access_token = _extract_access_token(token_data)
    if _is_token_expired(token_data, buffer_seconds=DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS):
        if storage is not None:
            refreshed = _refresh_token_file(token_path, token_data, storage=storage)
        else:
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
            print(
                json.dumps(
                    {"success": False, "error": f"Account '{name}' not found or token missing"},
                    indent=2,
                )
            )
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


def _fetch_account_quota(
    storage: StorageEngine,
    target_name: str,
) -> tuple[QuotaSummary | None, str | None, str | None]:
    """Fetch quota for an account, handling token resolution, refresh, and project ID discovery.

    Returns:
        tuple of (QuotaSummary or None, project_id or None, error_message or None)
    """
    global _last_refresh_revoked
    _last_refresh_revoked = False

    acct = storage.get_account(target_name)
    if not acct:
        return None, None, f"Account '{target_name}' not found in vault."

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

    try:
        access_token = _get_or_refresh_access_token(token_path, storage=storage)
    except TypeError:
        access_token = _get_or_refresh_access_token(token_path)

    # If token was refreshed, sync between live and vault for active profile
    new_data = _read_token_data(token_path)
    new_acc = _extract_access_token(new_data)
    new_exp = _extract_expiry(new_data)
    token_refreshed = bool(new_acc and (orig_acc is None or new_acc != orig_acc or new_exp != orig_exp))

    if is_active and token_refreshed:
        if token_path == storage.live_token:
            _sync_active_tokens(storage.live_token, vault_token_path)
        elif token_path == vault_token_path:
            _sync_active_tokens(vault_token_path, storage.live_token)

    if not access_token:
        access_token = new_acc or orig_acc

    if not access_token:
        if _last_refresh_revoked:
            return (
                None,
                None,
                f"Refresh token for '{target_name}' has expired or been revoked. "
                f"Please re-authenticate via 'agy' and save using 'promux save {target_name}'.",
            )
        return None, None, f"Could not extract access token for account '{target_name}'."

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
            if _last_refresh_revoked:
                return (
                    None,
                    None,
                    f"Refresh token for '{target_name}' has expired or been revoked. "
                    f"Please re-authenticate via 'agy' and save using 'promux save {target_name}'.",
                )
            return None, None, f"Failed to load project metadata for '{target_name}': {e}"

    if not project_id:
        return None, None, f"Could not determine project ID for account '{target_name}'."

    try:
        qs = client.get_quota(project_id)
        return qs, project_id, None
    except Exception as e:
        if "401" in str(e):
            td = _read_token_data(token_path)
            try:
                refreshed = _refresh_token_file(token_path, td, storage=storage) if td else None
            except TypeError:
                refreshed = _refresh_token_file(token_path, td) if td else None
            if refreshed:
                if is_active:
                    if token_path == storage.live_token:
                        _sync_active_tokens(storage.live_token, vault_token_path)
                    elif token_path == vault_token_path:
                        _sync_active_tokens(vault_token_path, storage.live_token)
                client.token = refreshed
                try:
                    qs = client.get_quota(project_id)
                    return qs, project_id, None
                except Exception as retry_e:
                    return None, project_id, f"Error retrieving quota: {retry_e}"
            else:
                if _last_refresh_revoked:
                    return (
                        None,
                        project_id,
                        f"Authentication failed (401): Refresh token for '{target_name}' has expired or been revoked. "
                        f"Please re-authenticate via 'agy' and save using 'promux save {target_name}'.",
                    )
                return None, project_id, f"Authentication failed (401): {e}"
        return None, project_id, f"Error retrieving quota: {e}"


def cmd_quota(storage: StorageEngine, name: str | None, json_out: bool) -> int:
    active_profile = storage.get_active_profile()

    # Single-profile detail mode
    if name is not None:
        qs, project_id, err = _fetch_account_quota(storage, name)
        if err or not qs:
            msg = err or f"Could not retrieve quota for '{name}'."
            if json_out:
                print(json.dumps({"error": msg}, indent=2))
            print(f"Error: {msg}", file=sys.stderr)
            return 1

        if json_out:
            res = {
                "account": name,
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

        active_tag = " [ACTIVE]" if name == active_profile else ""
        print(f"Quota for account '{name}' (project: {project_id}){active_tag}:\n")
        header = f"{'MODEL GROUP':<24} {'WINDOW':<10} {'REMAINING':<12} {'RESET TIME'}"
        print(header)
        print("-" * len(header))

        def _fmt(val: float | None) -> str:
            return f"{val * 100:.1f}%" if val is not None else "-"

        g_5h = _fmt(qs.gemini_5h_remaining)
        g_wk = _fmt(qs.gemini_weekly_remaining)
        c_5h = _fmt(qs.third_party_5h_remaining)
        c_wk = _fmt(qs.third_party_weekly_remaining)

        print(f"{'Gemini Models':<24} {'5h':<10} {g_5h:<12} {qs.gemini_5h_reset or '-'}")
        print(f"{'Gemini Models':<24} {'weekly':<10} {g_wk:<12} {qs.gemini_weekly_reset or '-'}")
        tp_5h_reset = qs.third_party_5h_reset or "-"
        tp_wk_reset = qs.third_party_weekly_reset or "-"
        print(f"{'Claude & GPT Models':<24} {'5h':<10} {c_5h:<12} {tp_5h_reset}")
        print(f"{'Claude & GPT Models':<24} {'weekly':<10} {c_wk:<12} {tp_wk_reset}")
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

    for acct_meta in accounts:
        acct_name = acct_meta.name
        is_active = acct_name == active_profile
        qs, project_id, err = _fetch_account_quota(storage, acct_name)

        if json_out:
            record: dict[str, Any] = {
                "account": acct_name,
                "active": is_active,
                "project_id": project_id,
            }
            if qs:
                record["gemini"] = {
                    "5h_remaining": qs.gemini_5h_remaining,
                    "5h_reset": qs.gemini_5h_reset,
                    "weekly_remaining": qs.gemini_weekly_remaining,
                    "weekly_reset": qs.gemini_weekly_reset,
                }
                record["third_party"] = {
                    "5h_remaining": qs.third_party_5h_remaining,
                    "5h_reset": qs.third_party_5h_reset,
                    "weekly_remaining": qs.third_party_weekly_remaining,
                    "weekly_reset": qs.third_party_weekly_reset,
                }
            else:
                record["error"] = err
            json_records.append(record)
        else:
            active_mark = "*" if is_active else ""
            if qs:
                g5 = (
                    f"{qs.gemini_5h_remaining * 100:.1f}%"
                    if qs.gemini_5h_remaining is not None
                    else "-"
                )
                gw = (
                    f"{qs.gemini_weekly_remaining * 100:.1f}%"
                    if qs.gemini_weekly_remaining is not None
                    else "-"
                )
                c5 = (
                    f"{qs.third_party_5h_remaining * 100:.1f}%"
                    if qs.third_party_5h_remaining is not None
                    else "-"
                )
                cw = (
                    f"{qs.third_party_weekly_remaining * 100:.1f}%"
                    if qs.third_party_weekly_remaining is not None
                    else "-"
                )
                reset_raw = qs.gemini_5h_reset or qs.third_party_5h_reset or "-"
                reset_display = (
                    reset_raw.split("T")[-1].replace("Z", "") if "T" in reset_raw else reset_raw
                )
            else:
                g5 = "[AUTH ERROR]" if "401" in str(err) else "[ERROR]"
                gw = "-"
                c5 = "-"
                cw = "-"
                reset_display = "-"
            text_rows.append((active_mark, acct_name, g5, gw, c5, cw, reset_display))

    if json_out:
        print(json.dumps(json_records, indent=2))
        return 0

    header = (
        f"{'ACTIVE':<7} {'PROFILE':<17} {'GEMINI (5H)':<13} {'GEMINI (WK)':<13} "
        f"{'CLAUDE (5H)':<13} {'CLAUDE (WK)':<13} {'NEXT RESET (UTC)'}"
    )
    print(header)
    print("-" * len(header))
    for active_mark, acct_name, g5, gw, c5, cw, reset_display in text_rows:
        print(
            f"{active_mark:<7} {acct_name:<17} {g5:<13} {gw:<13} {c5:<13} {cw:<13} {reset_display}"
        )

    return 0


def _format_expiry_table(expiry_str: str | None) -> str:
    """Format an ISO timestamp to 'YYYY-MM-DD HH:MM:SS' for table display."""
    if not expiry_str:
        return "-"
    try:
        clean = expiry_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return expiry_str


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
                pass
        method = (
            _last_refresh_method
            if _last_refresh_method in ("native", "fallback (agy)")
            else "native"
        )
        return True, f"Token refreshed successfully ({method})."
    else:
        return False, f"Failed to refresh token ({_last_refresh_method})."


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

        # Locate / sync token file:
        # If target is active profile and storage.live_token exists, check/sync
        if is_active and storage.live_token.exists():
            if not vault_token_path.exists():
                _atomic_copy_token(storage.live_token, vault_token_path)
            else:
                live_data = _read_token_data(storage.live_token)
                vault_data = _read_token_data(vault_token_path)
                lr = _extract_refresh_token(live_data)
                vr = _extract_refresh_token(vault_data)
                # Check that storage.live_token and vault_token_path share the same refresh_token
                # before syncing to ensure token identity.
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
                    _last_refresh_method
                    if _last_refresh_method in ("native", "fallback (agy)")
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
                # If target is active profile, sync refreshed token to storage.live_token
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
                        pass
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
        disp_exp = _format_expiry_table(r["expiry"])
        print(f"{r['account']:<18}{r['status']:<13}{disp_exp:<26}{r['method']}")

    return 1 if any_failed else 0


def cmd_watch(failover: FailoverEngine, poll_seconds: float, cooldown: int | None) -> int:
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

    def on_match(match: LogMatch) -> None:
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


def cmd_completion(shell: str) -> int:
    if shell == "bash":
        print(generate_bash_completion(), end="")
        return 0
    elif shell == "zsh":
        print(generate_zsh_completion(), end="")
        return 0
    return 1


def build_parser() -> argparse.ArgumentParser:
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="Output in JSON format"
    )

    parser = argparse.ArgumentParser(
        prog="promux",
        description="Antigravity CLI profile multiplexer & quota failover daemon",
        parents=[common_parser],
    )

    sub = parser.add_subparsers(dest="command", required=True, help="Subcommand to execute")

    # list
    sub.add_parser("list", parents=[common_parser], help="List all accounts in the vault")

    # save
    save_p = sub.add_parser(
        "save", parents=[common_parser], help="Save active token as a named profile"
    )
    save_p.add_argument("name", help="Profile name")
    save_p.add_argument("--email", default=None, help="Account email (auto-detected if omitted)")

    # switch
    switch_p = sub.add_parser("switch", parents=[common_parser], help="Hot-swap to a named profile")
    switch_p.add_argument("name", help="Profile name to switch to")

    # next
    next_p = sub.add_parser(
        "next", parents=[common_parser], help="Rotate to next eligible standby profile"
    )
    next_p.add_argument("--reason", default="manual", help="Reason for rotation (default: manual)")
    next_p.add_argument(
        "--cooldown",
        type=int,
        default=DEFAULT_COOLDOWN_MINUTES,
        help=f"Cooldown duration in minutes (default: {DEFAULT_COOLDOWN_MINUTES})",
    )

    # quota
    quota_p = sub.add_parser("quota", parents=[common_parser], help="Check Cloud Code Assist quota")
    quota_p.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Profile name (default: all profiles in vault)",
    )

    # refresh
    refresh_p = sub.add_parser(
        "refresh",
        parents=[common_parser],
        help="Refresh OAuth access token for vault accounts",
    )
    refresh_p.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Profile name (default: all profiles in vault)",
    )
    refresh_p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force token refresh even if not expired",
    )

    # whoami
    sub.add_parser("whoami", parents=[common_parser], help="Show active profile and token status")

    # remove
    remove_p = sub.add_parser(
        "remove", parents=[common_parser], help="Remove a profile from the vault"
    )
    remove_p.add_argument("name", help="Profile name to remove")

    # watch
    watch_p = sub.add_parser(
        "watch", parents=[common_parser], help="Start reactive log tailer daemon"
    )
    watch_p.add_argument(
        "--poll-seconds",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help=f"Log polling interval in seconds (default: {DEFAULT_POLL_SECONDS})",
    )
    watch_p.add_argument(
        "--cooldown",
        type=int,
        default=None,
        help="Cooldown override in minutes (default: auto-detected from log hints)",
    )

    # completion
    p_comp = sub.add_parser("completion", help="Generate shell auto-completion script")
    p_comp.add_argument("shell", choices=SUPPORTED_SHELLS, help="Target shell (bash or zsh)")

    return parser


def main(argv: list[str] | None = None) -> int:
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
        elif args.command == "refresh":
            return cmd_refresh(storage, args.name, args.force, json_out)
        elif args.command == "whoami":
            return cmd_whoami(storage, json_out)
        elif args.command == "remove":
            return cmd_remove(storage, args.name, json_out)
        elif args.command == "watch":
            return cmd_watch(failover, args.poll_seconds, args.cooldown)
        elif args.command == "completion":
            return cmd_completion(args.shell)
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
