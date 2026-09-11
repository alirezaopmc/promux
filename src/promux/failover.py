import math
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from .constants import DEFAULT_COOLDOWN_MINUTES
from .formatters import parse_iso_utc
from .models import (
    AccountMeta,
    AccountState,
    QuotaSummary,
    RotationResult,
    SmartRotationResult,
)
from .storage import StorageEngine


class FailoverEngine:
    def __init__(self, storage: StorageEngine):
        self.storage = storage

    def get_eligible_standby(self, exclude: str | None = None) -> list[AccountMeta]:
        state = self.storage.load_state()
        eligible: list[AccountMeta] = []
        for name, data in state.get("accounts", {}).items():
            if name == exclude:
                continue
            acct = AccountMeta.from_dict(data)
            if acct.state == AccountState.STANDBY:
                eligible.append(acct)

        # Sort LRU: least recently used first (None values first)
        def _sort_key(a: AccountMeta) -> datetime:
            dt = a.last_used_at
            if dt is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        eligible.sort(key=_sort_key)
        return eligible

    def apply_cooldown(self, name: str, minutes: int = DEFAULT_COOLDOWN_MINUTES) -> None:
        tx_func = getattr(self.storage, "transaction", None)
        if callable(tx_func):
            cm = self.storage.transaction()
        else:

            @contextmanager
            def _fallback_tx() -> Generator[dict[str, Any], None, None]:
                s = self.storage.load_state()
                yield s
                self.storage.save_state(s)

            cm = _fallback_tx()

        with cm as state:
            if name in state.get("accounts", {}):
                until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
                state["accounts"][name]["cooldown_until"] = until.isoformat()

    def rotate_next(
        self,
        reason: str = "manual",
        cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
    ) -> RotationResult:
        tx_func = getattr(self.storage, "transaction", None)
        if callable(tx_func):
            cm = self.storage.transaction()
        else:

            @contextmanager
            def _fallback_tx() -> Generator[dict[str, Any], None, None]:
                s = self.storage.load_state()
                yield s
                self.storage.save_state(s)

            cm = _fallback_tx()

        with cm as state:
            active_name = state.get("active")

            candidates = self.get_eligible_standby(exclude=active_name)
            if not candidates:
                return RotationResult(
                    success=False,
                    from_account=active_name,
                    to_account=None,
                    reason=f"No eligible standby accounts ({reason})",
                    cooldown_until=None,
                )

            next_target = candidates[0].name
            if not self.storage.switch_profile(next_target):
                return RotationResult(
                    success=False,
                    from_account=active_name,
                    to_account=next_target,
                    reason=f"Failed to switch token to {next_target}",
                    cooldown_until=None,
                )

            cooldown_until = None
            if active_name and active_name in state.get("accounts", {}):
                cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)
                state["accounts"][active_name]["cooldown_until"] = cooldown_until.isoformat()

            return RotationResult(
                success=True,
                from_account=active_name,
                to_account=next_target,
                reason=reason,
                cooldown_until=cooldown_until,
            )

    def _parse_iso_reset_minutes(
        self,
        reset_iso: str | None,
        default_minutes: int = DEFAULT_COOLDOWN_MINUTES,
    ) -> int:
        dt = parse_iso_utc(reset_iso)
        if dt is None:
            return default_minutes
        now = datetime.now(timezone.utc)
        diff_seconds = (dt - now).total_seconds()
        if diff_seconds <= 0:
            return default_minutes
        minutes = int(math.ceil(diff_seconds / 60.0))
        return minutes if minutes > 0 else default_minutes

    def rotate_smart(
        self,
        quota_fetcher: Callable[[str], tuple[QuotaSummary | None, str | None, str | None]],
        model: str = "gemini",
        default_cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
    ) -> SmartRotationResult:
        tx_func = getattr(self.storage, "transaction", None)
        if callable(tx_func):
            cm = self.storage.transaction()
        else:

            @contextmanager
            def _fallback_tx() -> Generator[dict[str, Any], None, None]:
                s = self.storage.load_state()
                yield s
                self.storage.save_state(s)

            cm = _fallback_tx()

        with cm as state:
            active_name = state.get("active")

            eligible = self.get_eligible_standby(exclude=active_name)
            if not eligible:
                return SmartRotationResult(
                    success=False,
                    from_account=active_name,
                    to_account=None,
                    model=model,
                    reason="No eligible standby accounts",
                    cooldown_until=None,
                )

            scored_candidates: list[tuple[AccountMeta, float, float, str | None]] = []
            for candidate in eligible:
                try:
                    qs, _, _ = quota_fetcher(candidate.name)
                except Exception:
                    qs = None

                if qs is None:
                    continue

                if model.lower() == "gemini":
                    five_hour = qs.gemini_5h_remaining
                    weekly = qs.gemini_weekly_remaining
                    reset_time = qs.gemini_5h_reset
                else:
                    five_hour = qs.third_party_5h_remaining
                    weekly = qs.third_party_weekly_remaining
                    reset_time = qs.third_party_5h_reset

                if five_hour is None or weekly is None:
                    continue
                if five_hour <= 0.0 or weekly <= 0.0:
                    continue

                scored_candidates.append((candidate, five_hour, weekly, reset_time))

            if not scored_candidates:
                return SmartRotationResult(
                    success=False,
                    from_account=active_name,
                    to_account=None,
                    model=model,
                    reason=f"No standby accounts found with available quota for '{model}' models",
                    cooldown_until=None,
                )

            def _candidate_sort_key(
                item: tuple[AccountMeta, float, float, str | None],
            ) -> tuple[float, float, int, datetime]:
                c, f_hr, wk, _ = item
                dt = c.last_used_at
                if dt is None:
                    lru_dt = datetime.min.replace(tzinfo=timezone.utc)
                    has_used = 0
                else:
                    lru_dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
                    has_used = 1
                return (-f_hr, -wk, has_used, lru_dt)

            scored_candidates.sort(key=_candidate_sort_key)
            best, best_five_hour, best_weekly, _ = scored_candidates[0]

            if not self.storage.switch_profile(best.name):
                return SmartRotationResult(
                    success=False,
                    from_account=active_name,
                    to_account=best.name,
                    model=model,
                    reason=f"Failed to switch token to {best.name}",
                    cooldown_until=None,
                )

            cooldown_until: datetime | None = None
            if active_name and active_name in state.get("accounts", {}):
                try:
                    active_qs, _, _ = quota_fetcher(active_name)
                except Exception:
                    active_qs = None

                if active_qs is not None:
                    if model.lower() == "gemini":
                        active_5h = active_qs.gemini_5h_remaining
                        active_wk = active_qs.gemini_weekly_remaining
                        active_reset = (
                            active_qs.gemini_5h_reset
                            if active_5h <= 0.0
                            else active_qs.gemini_weekly_reset
                        )
                    else:
                        active_5h = active_qs.third_party_5h_remaining
                        active_wk = active_qs.third_party_weekly_remaining
                        active_reset = (
                            active_qs.third_party_5h_reset
                            if active_5h <= 0.0
                            else active_qs.third_party_weekly_reset
                        )

                    if (active_5h is not None and active_5h <= 0.0) or (
                        active_wk is not None and active_wk <= 0.0
                    ):
                        cool_mins = self._parse_iso_reset_minutes(
                            active_reset, default_cooldown_minutes
                        )
                        cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=cool_mins)
                        state["accounts"][active_name]["cooldown_until"] = (
                            cooldown_until.isoformat()
                        )

            now_iso = datetime.now(timezone.utc).isoformat()
            best.last_used_at = datetime.fromisoformat(now_iso)
            if best.name in state.get("accounts", {}):
                state["accounts"][best.name]["last_used_at"] = now_iso

            return SmartRotationResult(
                success=True,
                from_account=active_name,
                to_account=best.name,
                reason="smart",
                model=model,
                five_hour_remaining=best_five_hour,
                weekly_remaining=best_weekly,
                cooldown_until=cooldown_until,
            )
