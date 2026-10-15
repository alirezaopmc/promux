"""Token I/O, extraction, and OAuth refresh primitives for promux."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .constants import (
    DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_TOKEN_URL,
)
from .lock import file_lock

if TYPE_CHECKING:
    from .storage import StorageEngine

logger = logging.getLogger(__name__)


def read_token_data(token_path: Path) -> dict[str, Any] | None:
    """Safely read and parse OAuth token JSON."""
    if not token_path.exists():
        return None
    try:
        with open(token_path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def extract_access_token(token_data: dict[str, Any] | None) -> str | None:
    """Extract access_token string from raw token dictionary."""
    if not token_data:
        return None
    tok = token_data.get("token")
    if isinstance(tok, dict):
        return tok.get("access_token")
    if "access_token" in token_data:
        return token_data.get("access_token")
    return None


def extract_expiry(token_data: dict[str, Any] | None) -> str | None:
    """Extract expiry timestamp from raw token dictionary."""
    if not token_data:
        return None
    tok = token_data.get("token")
    if isinstance(tok, dict):
        return tok.get("expiry")
    if "expiry" in token_data:
        return token_data.get("expiry")
    return None


def extract_refresh_token(token_data: dict[str, Any] | None) -> str | None:
    """Extract refresh_token string from raw token dictionary."""
    if not token_data:
        return None
    tok = token_data.get("token")
    if isinstance(tok, dict):
        return tok.get("refresh_token")
    if "refresh_token" in token_data:
        return token_data.get("refresh_token")
    return None


def atomic_copy_token(src: Path, dst: Path) -> None:
    """Atomically copy a token file ensuring 0600 permissions."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_dst = dst.with_suffix(f".tmp_{os.getpid()}")
    shutil.copy2(src, tmp_dst)
    if os.name != "nt":
        tmp_dst.chmod(0o600)
    os.replace(tmp_dst, dst)


def sync_active_tokens(src: Path, dst: Path) -> None:
    """Atomically copy refreshed token from src to dst.

    Only copies if tokens share identity or dst is missing.
    """
    try:
        if not src.exists():
            return
        if dst.exists():
            src_td = read_token_data(src)
            dst_td = read_token_data(dst)
            sr = extract_refresh_token(src_td)
            dr = extract_refresh_token(dst_td)
            if sr and dr and sr != dr:
                return
        atomic_copy_token(src, dst)
    except Exception:
        logger.debug("sync_active_tokens failed src=%s dst=%s", src, dst, exc_info=True)


def get_oauth_credentials() -> tuple[str, str]:
    """Retrieve OAuth client ID and secret from environment or ~/.promux/oauth.json."""
    from .constants import PROMUX_HOME

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
                logger.debug("Failed to read oauth.json", exc_info=True)
    return client_id, client_secret


def parse_token_expiry_dt(expiry_str: str | None) -> datetime | None:
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


def is_token_expired(
    token_data: dict[str, Any] | None,
    buffer_seconds: int = DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS,
) -> bool:
    """Check if token is expired or within buffer_seconds of expiration."""
    if not token_data or not isinstance(token_data, dict):
        return True
    expiry_str = extract_expiry(token_data)
    exp_dt = parse_token_expiry_dt(expiry_str)
    if exp_dt is None:
        return True
    now = datetime.now(timezone.utc)
    return now + timedelta(seconds=buffer_seconds) >= exp_dt


# Thread-local state for refresh tracking
_auth_local = threading.local()


def _get_last_refresh_method() -> str:
    return getattr(_auth_local, "last_refresh_method", "native")


def _set_last_refresh_method(val: str) -> None:
    _auth_local.last_refresh_method = val


def _get_last_refresh_revoked() -> bool:
    return getattr(_auth_local, "last_refresh_revoked", False)


def _set_last_refresh_revoked(val: bool) -> None:
    _auth_local.last_refresh_revoked = val


def _refresh_token_native(token_path: Path, token_data: dict[str, Any]) -> str | None:
    """Tier 1: Execute direct HTTP OAuth refresh using Google OAuth token endpoint."""
    if not isinstance(token_data, dict):
        return None
    tok = token_data.get("token")
    if not isinstance(tok, dict):
        return None
    refresh_token = tok.get("refresh_token")
    if not refresh_token:
        return None
    client_id, client_secret = get_oauth_credentials()
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
            if (
                isinstance(res, dict)
                and "refresh_token" in res
                and isinstance(res["refresh_token"], str)
            ):
                tok["refresh_token"] = res["refresh_token"]
            tmp_path = token_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(token_data, f, indent=2)
            if os.name != "nt":
                tmp_path.chmod(0o600)
            os.replace(tmp_path, token_path)
            return new_acc
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            if isinstance(body, dict) and body.get("error") == "invalid_grant":
                _set_last_refresh_revoked(True)
        except Exception:
            if "invalid_grant" in str(e).lower():
                _set_last_refresh_revoked(True)
    except Exception as e:
        if "invalid_grant" in str(e).lower():
            _set_last_refresh_revoked(True)
        logger.debug("Native token refresh failed for %s: %s", token_path, e, exc_info=True)
    return None


def _refresh_token_fallback_agy(token_path: Path, storage: StorageEngine) -> str | None:
    """Tier 2: Fallback renewal via headless agy CLI under storage file lock."""
    import subprocess

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
            old_data = read_token_data(token_path)
            old_acc = extract_access_token(old_data)
            old_exp = extract_expiry(old_data)
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
                new_data = read_token_data(storage.live_token)
                new_acc = extract_access_token(new_data)
                new_exp = extract_expiry(new_data)
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
                if os.name != "nt":
                    backup_path.chmod(0o600)
            refreshed_acc: str | None = None
            try:
                tmp_live = storage.live_token.with_name(f".tmp_live_{os.getpid()}")
                shutil.copy2(token_path, tmp_live)
                if os.name != "nt":
                    tmp_live.chmod(0o600)
                os.replace(tmp_live, storage.live_token)
                try:
                    subprocess.run(["agy", "models"], capture_output=True, timeout=10)
                except (subprocess.SubprocessError, OSError):
                    pass
                new_data = read_token_data(storage.live_token)
                new_acc = extract_access_token(new_data)
                new_exp = extract_expiry(new_data)
                if new_acc and (old_acc is None or new_acc != old_acc or new_exp != old_exp):
                    tmp_path = token_path.with_suffix(f".tmp_{os.getpid()}")
                    shutil.copy2(storage.live_token, tmp_path)
                    if os.name != "nt":
                        tmp_path.chmod(0o600)
                    os.replace(tmp_path, token_path)
                    refreshed_acc = new_acc
            finally:
                if live_existed:
                    if backup_path.exists():
                        os.replace(backup_path, storage.live_token)
                        if os.name != "nt":
                            storage.live_token.chmod(0o600)
                else:
                    if storage.live_token.exists():
                        storage.live_token.unlink()
                    if backup_path.exists():
                        backup_path.unlink()
            return refreshed_acc
    except Exception:
        logger.debug("agy fallback refresh failed for %s", token_path, exc_info=True)
        return None


def _refresh_token_file(
    token_path: Path,
    token_data: dict[str, Any],
    storage: StorageEngine | None = None,
) -> str | None:
    """Attempt to refresh an expired token using its refresh_token or agy fallback."""
    import sys
    cli_mod = sys.modules.get("promux.cli")
    if cli_mod:
        cli_refresh = getattr(cli_mod, "_refresh_token_file", None)
        if cli_refresh is not None and getattr(cli_refresh, "__code__", None) != _refresh_token_file.__code__:
            try:
                return cli_refresh(token_path, token_data, storage=storage)
            except TypeError:
                return cli_refresh(token_path, token_data)

    _set_last_refresh_method("failed")
    _set_last_refresh_revoked(False)

    native_fn = getattr(cli_mod, "_refresh_token_native", _refresh_token_native) if cli_mod else _refresh_token_native
    fallback_fn = (
        getattr(cli_mod, "_refresh_token_fallback_agy", _refresh_token_fallback_agy)
        if cli_mod
        else _refresh_token_fallback_agy
    )

    try:
        refreshed = native_fn(token_path, token_data)
    except Exception as e:
        if "invalid_grant" in str(e).lower():
            _set_last_refresh_revoked(True)
        refreshed = None
    if refreshed:
        _set_last_refresh_method("native")
        _set_last_refresh_revoked(False)
        return refreshed
    if _get_last_refresh_revoked():
        _set_last_refresh_method("revoked")
        return None
    if storage is not None:
        refreshed = fallback_fn(token_path, storage)
        if refreshed:
            _set_last_refresh_method("fallback (agy)")
            return refreshed
    return None


def get_or_refresh_access_token(
    token_path: Path,
    storage: StorageEngine | None = None,
) -> str | None:
    """Get access token, refreshing it if expired and refresh_token is present."""
    import sys
    cli_mod = sys.modules.get("promux.cli")
    if cli_mod:
        cli_fn = getattr(cli_mod, "_get_or_refresh_access_token", None)
        if cli_fn is not None and getattr(cli_fn, "__module__", "") != "promux.auth":
            return cli_fn(token_path, storage=storage)

    token_data = read_token_data(token_path)
    if not token_data:
        return None
    access_token = extract_access_token(token_data)
    if is_token_expired(token_data, buffer_seconds=DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS):
        refresh_file_fn = (
            getattr(cli_mod, "_refresh_token_file", _refresh_token_file)
            if cli_mod
            else _refresh_token_file
        )
        refreshed = refresh_file_fn(token_path, token_data, storage=storage)
        if refreshed:
            return refreshed
    return access_token

