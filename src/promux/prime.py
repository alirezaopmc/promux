"""Weekly quota priming engine and eligibility logic."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .auth import (
    _refresh_token_native,
    atomic_copy_token,
    extract_access_token,
    get_or_refresh_access_token,
    is_token_expired,
    read_token_data,
    sync_active_tokens,
)
from .cache import QuotaCache
from .constants import DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS
from .formatters import format_relative_countdown, parse_iso_utc
from .lock import file_lock
from .models import QuotaSummary
from .quota import QuotaClient
from .storage import StorageEngine

logger = logging.getLogger(__name__)


@dataclass
class PrimeResult:
    """Outcome of priming weekly quota for an account."""

    account: str
    primed: bool
    reason: str = ""
    new_reset: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "account": self.account,
            "primed": self.primed,
            "reason": self.reason,
        }
        if self.new_reset is not None:
            d["new_reset"] = self.new_reset
        if self.error is not None:
            d["error"] = self.error
        return d


def is_eligible_for_prime(
    qs: QuotaSummary | None,
    now: datetime | None = None,
    force: bool = False,
) -> tuple[bool, str]:
    """Check whether an account is eligible for weekly quota priming."""
    if isinstance(now, bool):
        force = now
        now = None

    if force:
        return True, "forced"
    if qs is None:
        return False, "no quota data"

    curr = now or datetime.now(timezone.utc)
    if curr.tzinfo is None:
        curr = curr.replace(tzinfo=timezone.utc)

    def check_reset(rst_str: str | None) -> tuple[bool, bool, str]:
        if rst_str:
            dt = parse_iso_utc(rst_str)
            if dt is not None:
                if dt > curr:
                    dur = format_relative_countdown(rst_str, now=curr)
                    return True, False, dur
                return False, True, ""
        return False, False, ""

    gem_active, gem_expired, gem_dur = check_reset(qs.gemini_weekly_reset)
    tp_active, tp_expired, tp_dur = check_reset(qs.third_party_weekly_reset)

    if gem_active:
        return False, f"active countdown ({gem_dur} left)"
    if tp_active:
        return False, f"active countdown ({tp_dur} left)"

    if gem_expired or tp_expired:
        return True, "weekly reset reached"

    if qs.gemini_weekly_remaining >= 1.0:
        return True, "weekly reset floating at 100%"

    return False, "no weekly reset information"


def _run_agy_prime(timeout: float = 20.0) -> None:
    agy_path = shutil.which("agy")
    if not agy_path:
        raise RuntimeError("agy CLI not found in PATH")
    proc = subprocess.run(
        [
            agy_path,
            "-p",
            "ping",
            "--disable-slash-commands",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        err_msg = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"agy command failed: exit code {proc.returncode} (output: {err_msg})"
        )


run_agy_prime: Callable[..., Any] = _run_agy_prime


def query_account_quota(storage: StorageEngine, account_name: str) -> QuotaSummary:
    """Query live quota summary for an account."""
    acct = storage.get_account(account_name)
    if not acct:
        raise ValueError(f"account not found: {account_name}")

    vault_token = storage.accounts_dir / account_name / "antigravity-oauth-token"
    if not vault_token.exists():
        raise FileNotFoundError(f"token not found: {vault_token}")

    token_data = read_token_data(vault_token)
    if not token_data:
        raise ValueError(f"failed to read token for {account_name}")

    acc_token = extract_access_token(token_data)
    if not acc_token or is_token_expired(
        token_data, buffer_seconds=DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS
    ):
        acc_token = get_or_refresh_access_token(vault_token, storage=storage)
        if not acc_token:
            raise RuntimeError(f"failed to refresh token for {account_name}")

    client = QuotaClient(acc_token)
    project_id = acct.project_id or ""
    if not project_id:
        try:
            meta = client.load_metadata()
            if meta and meta.get("project_id"):
                project_id = meta["project_id"]
        except Exception:
            logger.debug("Failed to load metadata for %s", account_name, exc_info=True)

    return client.get_quota(project_id)


def query_quota(storage: StorageEngine, account_name: str) -> QuotaSummary:
    return query_account_quota(storage, account_name)


def prime_account(
    storage: StorageEngine,
    account_name: str,
    force: bool = False,
    cache: QuotaCache | None = None,
) -> PrimeResult:
    """Safely execute a minimal headless prompt turn to fix the 7-day weekly reset countdown."""
    state = storage.load_state()
    if account_name not in state.get("accounts", {}):
        raise ValueError(f"account not found: {account_name}")

    vault_token = storage.accounts_dir / account_name / "antigravity-oauth-token"
    if not vault_token.exists():
        raise FileNotFoundError(f"token not found: {vault_token}")

    qs: QuotaSummary | None = None
    if cache is not None:
        qs = cache.get(account_name)

    if qs is None and not force:
        try:
            qs = query_quota(storage, account_name)
            if cache is not None and qs is not None:
                cache.set(account_name, qs)
        except Exception as err:
            logger.debug("Failed to query quota for priming %s", account_name, exc_info=True)
            return PrimeResult(account=account_name, primed=False, error=str(err))

    eligible, reason = is_eligible_for_prime(qs, now=datetime.now(timezone.utc), force=force)
    if not eligible:
        return PrimeResult(account=account_name, primed=False, reason=reason)

    result = PrimeResult(account=account_name, primed=False, reason=reason)

    # Proactively check and refresh token before acquiring lock file
    token_data = read_token_data(vault_token)
    if token_data and is_token_expired(
        token_data, buffer_seconds=DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS
    ):
        try:
            get_or_refresh_access_token(vault_token, storage=storage)
        except Exception:
            logger.debug("Pre-prime token refresh failed for %s", account_name, exc_info=True)

    try:
        with file_lock(storage.lock_file, timeout=30.0):
            cur_state = storage.load_state()
            is_active = cur_state.get("active") == account_name

            if is_active:
                sync_active_tokens(storage.live_token, vault_token)

            live_existed = False
            backup_path = storage.live_token.with_name(
                f"{storage.live_token.name}.prime_bak_{os.getpid()}_{time.time_ns()}"
            )

            try:
                if not is_active:
                    storage.live_token.parent.mkdir(parents=True, exist_ok=True)
                    if storage.live_token.exists():
                        live_existed = True
                        atomic_copy_token(storage.live_token, backup_path)
                    atomic_copy_token(vault_token, storage.live_token)

                # Double check live token expiry inside lock
                live_td = read_token_data(storage.live_token)
                if live_td and is_token_expired(
                    live_td, buffer_seconds=DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS
                ):
                    try:
                        _refresh_token_native(storage.live_token, live_td)
                    except Exception:
                        logger.debug("Native refresh inside lock failed", exc_info=True)

                run_agy_prime()

                # Sync updated live token back to vault
                atomic_copy_token(storage.live_token, vault_token)

            finally:
                if not is_active:
                    if live_existed:
                        if backup_path.exists():
                            backup_path.replace(storage.live_token)
                            if os.name != "nt":
                                storage.live_token.chmod(0o600)
                    else:
                        if storage.live_token.exists():
                            storage.live_token.unlink()
                        if backup_path.exists():
                            backup_path.unlink()

    except Exception as e:
        logger.debug("Priming failed for %s", account_name, exc_info=True)
        result.error = str(e)
        return result

    result.primed = True

    # Live quota verification and cache update
    try:
        new_qs = query_quota(storage, account_name)
        if new_qs is not None:
            result.new_reset = new_qs.gemini_weekly_reset
            if cache is not None:
                cache.set(account_name, new_qs)
    except Exception as e:
        logger.debug("Post-prime quota verification failed for %s", account_name, exc_info=True)
        result.error = f"primed successfully, but failed to refresh quota: {e}"

    return result


def prime_all_eligible(
    storage: StorageEngine,
    cache: QuotaCache | None = None,
    force: bool = False,
) -> list[PrimeResult]:
    """Iterate over all accounts in the vault and prime those eligible."""
    state = storage.load_state()
    accounts = state.get("accounts", {})
    if not accounts:
        return []

    names = sorted(accounts.keys())
    results: list[PrimeResult] = []
    for name in names:
        try:
            res = prime_account(storage, name, force=force, cache=cache)
            results.append(res)
        except Exception as e:
            logger.debug("Failed to prime %s in prime_all_eligible", name, exc_info=True)
            results.append(PrimeResult(account=name, primed=False, error=str(e)))

    return results
