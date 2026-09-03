from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import pytest
from promux.failover import FailoverEngine
from promux.models import AccountMeta, AccountState


class FakeStorage:
    def __init__(self):
        self.state = {"active": "acc1", "accounts": {}}
        self.switched = []
        self.fail_switch = False

    def load_state(self):
        return self.state

    def save_state(self, state):
        self.state = state

    @contextmanager
    def transaction(self):
        yield self.state

    def switch_profile(self, name):
        if self.fail_switch:
            return False
        if name in self.state["accounts"]:
            self.state["active"] = name
            self.switched.append(name)
            return True
        return False


def test_failover_rotates_to_lru_standby():
    storage = FakeStorage()
    now = datetime.now(timezone.utc)
    storage.state["accounts"] = {
        "acc1": AccountMeta(name="acc1", enabled=True, last_used_at=now).to_dict(),
        "acc2": AccountMeta(name="acc2", enabled=True, last_used_at=now - timedelta(hours=2)).to_dict(),
        "acc3": AccountMeta(name="acc3", enabled=True, last_used_at=now - timedelta(hours=5)).to_dict(),
    }

    engine = FailoverEngine(storage)
    candidates = engine.get_eligible_standby(exclude="acc1")
    assert [c.name for c in candidates] == ["acc3", "acc2"]

    res = engine.rotate_next(reason="quota_exhausted", cooldown_minutes=30)
    assert res.success is True
    assert res.from_account == "acc1"
    assert res.to_account == "acc3"
    assert storage.state["active"] == "acc3"

    # acc1 should be in cooldown
    acc1_meta = AccountMeta.from_dict(storage.state["accounts"]["acc1"])
    assert acc1_meta.state == AccountState.COOLDOWN


def test_failover_no_candidates():
    storage = FakeStorage()
    storage.state["accounts"] = {
        "acc1": AccountMeta(name="acc1", enabled=True).to_dict()
    }
    engine = FailoverEngine(storage)
    res = engine.rotate_next(reason="quota_exhausted")
    assert res.success is False
    assert "No eligible standby" in res.reason
    assert res.cooldown_until is None
    # C2: active account must NOT be in cooldown when rotation fails
    acc1_meta = AccountMeta.from_dict(storage.state["accounts"]["acc1"])
    assert acc1_meta.state == AccountState.STANDBY
    assert storage.state["accounts"]["acc1"].get("cooldown_until") is None


def test_get_eligible_standby_lru_sorting_with_none_and_timezones():
    storage = FakeStorage()
    now = datetime.now(timezone.utc)
    # acc_none has never been used (last_used_at=None)
    # acc_old used 10 hours ago
    # acc_recent used 1 hour ago
    storage.state["accounts"] = {
        "acc1": AccountMeta(name="acc1", enabled=True, last_used_at=now).to_dict(),
        "acc_recent": AccountMeta(name="acc_recent", enabled=True, last_used_at=now - timedelta(hours=1)).to_dict(),
        "acc_none": AccountMeta(name="acc_none", enabled=True, last_used_at=None).to_dict(),
        "acc_old": AccountMeta(name="acc_old", enabled=True, last_used_at=now - timedelta(hours=10)).to_dict(),
    }

    engine = FailoverEngine(storage)
    candidates = engine.get_eligible_standby(exclude="acc1")
    # None must sort first (least recently used / never used), then oldest timestamp
    assert [c.name for c in candidates] == ["acc_none", "acc_old", "acc_recent"]


def test_get_eligible_standby_excludes_disabled_and_cooldown():
    storage = FakeStorage()
    now = datetime.now(timezone.utc)
    storage.state["accounts"] = {
        "acc1": AccountMeta(name="acc1", enabled=True, last_used_at=now).to_dict(),
        "disabled_acct": AccountMeta(name="disabled_acct", enabled=False).to_dict(),
        "cooldown_acct": AccountMeta(
            name="cooldown_acct",
            enabled=True,
            cooldown_until=now + timedelta(minutes=30),
        ).to_dict(),
        "expired_cooldown_acct": AccountMeta(
            name="expired_cooldown_acct",
            enabled=True,
            cooldown_until=now - timedelta(minutes=10),
        ).to_dict(),
    }

    engine = FailoverEngine(storage)
    candidates = engine.get_eligible_standby(exclude="acc1")
    assert [c.name for c in candidates] == ["expired_cooldown_acct"]


def test_apply_cooldown():
    storage = FakeStorage()
    storage.state["accounts"] = {
        "acc1": AccountMeta(name="acc1", enabled=True).to_dict(),
        "acc2": AccountMeta(name="acc2", enabled=True).to_dict(),
    }
    engine = FailoverEngine(storage)
    engine.apply_cooldown("acc2", minutes=45)

    acc2_data = storage.state["accounts"]["acc2"]
    assert "cooldown_until" in acc2_data
    acc2_meta = AccountMeta.from_dict(acc2_data)
    assert acc2_meta.state == AccountState.COOLDOWN

    # Applying to non-existent account should be a safe no-op
    engine.apply_cooldown("non_existent", minutes=30)


def test_rotate_next_switch_failure():
    storage = FakeStorage()
    storage.fail_switch = True
    storage.state["accounts"] = {
        "acc1": AccountMeta(name="acc1", enabled=True).to_dict(),
        "acc2": AccountMeta(name="acc2", enabled=True).to_dict(),
    }
    engine = FailoverEngine(storage)
    res = engine.rotate_next(reason="quota_exhausted")
    assert res.success is False
    assert res.from_account == "acc1"
    assert res.to_account == "acc2"
    assert "Failed to switch token to acc2" in res.reason
    assert res.cooldown_until is None
    # C2: active account must NOT be in cooldown when switch fails
    acc1_meta = AccountMeta.from_dict(storage.state["accounts"]["acc1"])
    assert acc1_meta.state == AccountState.STANDBY
    assert storage.state["accounts"]["acc1"].get("cooldown_until") is None


def test_rotate_next_no_prior_active_account():
    storage = FakeStorage()
    storage.state["active"] = None
    storage.state["accounts"] = {
        "acc1": AccountMeta(name="acc1", enabled=True).to_dict(),
    }
    engine = FailoverEngine(storage)
    res = engine.rotate_next(reason="initial_selection")
    assert res.success is True
    assert res.from_account is None
    assert res.to_account == "acc1"
    assert storage.state["active"] == "acc1"
