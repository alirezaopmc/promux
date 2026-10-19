"""Prime weekly quota reset countdowns via minimal headless turns."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from ..cache import QuotaCache
from ..colors import green, red, yellow
from ..formatters import format_relative_countdown
from ..models import QuotaSummary
from ..prime import (
    PrimeResult,
    is_eligible_for_prime,
    prime_account,
    query_quota,
)
from ..storage import StorageEngine

logger = logging.getLogger(__name__)


def _pluralize(count: int, singular: str, plural: str) -> str:
    return f"1 {singular}" if count == 1 else f"{count} {plural}"


def cmd_prime(
    storage: StorageEngine,
    account: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    json_out: bool = False,
    no_color: bool = False,
) -> int:
    """Execute promux prime command."""
    cache = QuotaCache(storage.home)
    state = storage.load_state()
    accounts = state.get("accounts", {})

    target_accounts: list[str] = []
    if account is not None and account != "all":
        if account not in accounts:
            msg = f"profile '{account}' not found in vault"
            if json_out:
                print(json.dumps({"error": msg}, indent=2))
            else:
                print(f"Error: {msg}", file=sys.stderr)
            return 1
        target_accounts = [account]
    else:
        if not accounts:
            if json_out:
                print(json.dumps([], indent=2))
            else:
                print("No profiles registered in vault.")
            return 0
        target_accounts = sorted(accounts.keys())

    if not json_out:
        if len(target_accounts) > 1:
            print(f"Checking weekly quota status across {len(target_accounts)} profiles...")
        else:
            print(f"Checking weekly quota status for profile '{target_accounts[0]}'...")

    results: list[PrimeResult] = []
    primed_count = 0
    skipped_count = 0
    error_count = 0
    now = datetime.now(timezone.utc)

    for name in target_accounts:
        qs: QuotaSummary | None = None
        if cache is not None:
            qs = cache.get(name)
        if qs is None and not force:
            try:
                qs = query_quota(storage, name)
                if cache is not None and qs is not None:
                    cache.set(name, qs)
            except Exception as q_err:
                error_count += 1
                results.append(PrimeResult(account=name, primed=False, error=str(q_err)))
                if not json_out:
                    mark = "✗" if no_color else red("✗")
                    print(f"{mark} {name}: failed to query quota: {q_err}")
                continue

        eligible, reason = is_eligible_for_prime(qs, now=now, force=force)

        if dry_run:
            if eligible:
                primed_count += 1
                results.append(PrimeResult(account=name, primed=False, reason=reason))
                if not json_out:
                    mark = "⚡" if no_color else yellow("⚡")
                    print(f"{mark} {name}: {reason} (would prime)")
            else:
                skipped_count += 1
                results.append(PrimeResult(account=name, primed=False, reason=reason))
                if not json_out:
                    mark = "✓" if no_color else green("✓")
                    print(f"{mark} {name}: {reason} - skipped")
            continue

        # Live mode
        if not eligible:
            skipped_count += 1
            results.append(PrimeResult(account=name, primed=False, reason=reason))
            if not json_out:
                mark = "✓" if no_color else green("✓")
                print(f"{mark} {name}: {reason} - skipped")
            continue

        if not json_out:
            mark = "⚡" if no_color else yellow("⚡")
            print(f"{mark} {name}: {reason} -> Priming via agy...")

        res = prime_account(storage, name, force=force, cache=cache)
        if res.error and not res.primed:
            error_count += 1
            results.append(res)
            if not json_out:
                mark = "✗" if no_color else red("✗")
                print(f"{mark} [{name}] Priming failed: {res.error}")
        elif res.primed:
            primed_count += 1
            results.append(res)
            if not json_out:
                if res.new_reset:
                    rel = format_relative_countdown(res.new_reset, now=datetime.now(timezone.utc))
                    print(f"   [{name}] Token primed! New weekly reset: {res.new_reset} ({rel})")
                elif res.error:
                    warn = f"(warning: quota refresh delayed: {res.error})"
                    print(f"   [{name}] Token primed! {warn}")
                else:
                    print(f"   [{name}] Token primed!")
        else:
            skipped_count += 1
            results.append(res)
            if not json_out:
                mark = "✓" if no_color else green("✓")
                print(f"{mark} {name}: {res.reason} - skipped")

    if json_out:
        print(json.dumps([r.to_dict() for r in results], indent=2))
        return 0

    if dry_run:
        print(
            f"\nSummary: {_pluralize(primed_count, 'account', 'accounts')} would be primed, "
            f"{_pluralize(skipped_count, 'account skipped', 'accounts skipped')} (dry-run)."
        )
    elif error_count > 0:
        print(
            f"\nSummary: {_pluralize(primed_count, 'account', 'accounts')} primed, "
            f"{_pluralize(skipped_count, 'account skipped', 'accounts skipped')}, "
            f"{_pluralize(error_count, 'error', 'errors')}."
        )
    else:
        print(
            f"\nSummary: {_pluralize(primed_count, 'account', 'accounts')} primed, "
            f"{_pluralize(skipped_count, 'account skipped', 'accounts skipped')}."
        )

    return 0
