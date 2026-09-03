from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Any
from .constants import DEFAULT_COOLDOWN_MINUTES
from .models import AccountMeta, AccountState, RotationResult
from .storage import StorageEngine


class FailoverEngine:
    def __init__(self, storage: StorageEngine):
        self.storage = storage

    def get_eligible_standby(self, exclude: Optional[str] = None) -> List[AccountMeta]:
        state = self.storage.load_state()
        eligible: List[AccountMeta] = []
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

    def apply_cooldown(self, name: str, minutes: int = DEFAULT_COOLDOWN_MINUTES):
        tx_func = getattr(self.storage, "transaction", None)
        if callable(tx_func):
            cm = self.storage.transaction()
        else:
            @contextmanager
            def _fallback_tx():
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
            def _fallback_tx():
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
