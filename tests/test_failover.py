from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

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
        "acc2": AccountMeta(
            name="acc2", enabled=True, last_used_at=now - timedelta(hours=2)
        ).to_dict(),
        "acc3": AccountMeta(
            name="acc3", enabled=True, last_used_at=now - timedelta(hours=5)
        ).to_dict(),
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
    storage.state["accounts"] = {"acc1": AccountMeta(name="acc1", enabled=True).to_dict()}
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
        "acc_recent": AccountMeta(
            name="acc_recent", enabled=True, last_used_at=now - timedelta(hours=1)
        ).to_dict(),
        "acc_none": AccountMeta(name="acc_none", enabled=True, last_used_at=None).to_dict(),
        "acc_old": AccountMeta(
            name="acc_old", enabled=True, last_used_at=now - timedelta(hours=10)
        ).to_dict(),
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


def test_rotate_smart_selects_highest_5h_quota():
    from promux.models import QuotaSummary

    storage = FakeStorage()
    now = datetime.now(timezone.utc)
    storage.state["active"] = "acc_active"
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True, last_used_at=now).to_dict(),
        "acc_low": AccountMeta(
            name="acc_low", enabled=True, last_used_at=now - timedelta(hours=3)
        ).to_dict(),
        "acc_high": AccountMeta(
            name="acc_high", enabled=True, last_used_at=now - timedelta(hours=2)
        ).to_dict(),
    }

    quotas = {
        "acc_active": QuotaSummary(gemini_5h_remaining=0.0, gemini_weekly_remaining=0.5),
        "acc_low": QuotaSummary(gemini_5h_remaining=0.4, gemini_weekly_remaining=0.9),
        "acc_high": QuotaSummary(gemini_5h_remaining=0.9, gemini_weekly_remaining=0.9),
    }

    def fetcher(acct_name):
        return quotas.get(acct_name), "proj-1", None

    engine = FailoverEngine(storage)
    res = engine.rotate_smart(quota_fetcher=fetcher, model="gemini")

    assert res.success is True
    assert res.from_account == "acc_active"
    assert res.to_account == "acc_high"
    assert res.five_hour_remaining == 0.9
    assert res.weekly_remaining == 0.9
    assert storage.state["active"] == "acc_high"

    # Active account had 0.0 quota, so must be in cooldown
    active_meta = AccountMeta.from_dict(storage.state["accounts"]["acc_active"])
    assert active_meta.state == AccountState.COOLDOWN


def test_rotate_smart_active_account_retains_standby_if_quota_available():
    from promux.models import QuotaSummary

    storage = FakeStorage()
    now = datetime.now(timezone.utc)
    storage.state["active"] = "acc_active"
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True, last_used_at=now).to_dict(),
        "acc_other": AccountMeta(
            name="acc_other", enabled=True, last_used_at=now - timedelta(hours=1)
        ).to_dict(),
    }

    quotas = {
        "acc_active": QuotaSummary(gemini_5h_remaining=0.5, gemini_weekly_remaining=0.5),
        "acc_other": QuotaSummary(gemini_5h_remaining=1.0, gemini_weekly_remaining=1.0),
    }

    def fetcher(acct_name):
        return quotas.get(acct_name), "proj-1", None

    engine = FailoverEngine(storage)
    res = engine.rotate_smart(quota_fetcher=fetcher, model="gemini")

    assert res.success is True
    assert res.to_account == "acc_other"
    # acc_active had 0.5 quota > 0, so should NOT be in cooldown
    active_meta = AccountMeta.from_dict(storage.state["accounts"]["acc_active"])
    assert active_meta.state == AccountState.STANDBY


def test_rotate_smart_third_party_models():
    from promux.models import QuotaSummary

    storage = FakeStorage()
    storage.state["active"] = "acc_active"
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True).to_dict(),
        "acc1": AccountMeta(name="acc1", enabled=True).to_dict(),
        "acc2": AccountMeta(name="acc2", enabled=True).to_dict(),
    }

    # acc1 has high Gemini but 0 Claude; acc2 has high Claude
    quotas = {
        "acc_active": QuotaSummary(third_party_5h_remaining=0.0, third_party_weekly_remaining=0.1),
        "acc1": QuotaSummary(gemini_5h_remaining=1.0, third_party_5h_remaining=0.0),
        "acc2": QuotaSummary(
            gemini_5h_remaining=0.1,
            third_party_5h_remaining=0.8,
            third_party_weekly_remaining=0.9,
        ),
    }

    def fetcher(acct_name):
        return quotas.get(acct_name), "proj-1", None

    engine = FailoverEngine(storage)
    res = engine.rotate_smart(quota_fetcher=fetcher, model="claude")

    assert res.success is True
    assert res.to_account == "acc2"
    assert res.five_hour_remaining == 0.8


def test_rotate_smart_tiebreak_weekly_and_lru():
    from promux.models import QuotaSummary

    storage = FakeStorage()
    now = datetime.now(timezone.utc)
    storage.state["active"] = "acc_active"
    # Scenario A: 5h equal, weekly differs -> higher weekly wins
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True, last_used_at=now).to_dict(),
        "acc1": AccountMeta(
            name="acc1", enabled=True, last_used_at=now - timedelta(hours=1)
        ).to_dict(),
        "acc2": AccountMeta(
            name="acc2", enabled=True, last_used_at=now - timedelta(hours=2)
        ).to_dict(),
    }
    quotas = {
        "acc_active": QuotaSummary(gemini_5h_remaining=0.0, gemini_weekly_remaining=0.0),
        "acc1": QuotaSummary(gemini_5h_remaining=0.8, gemini_weekly_remaining=0.9),
        "acc2": QuotaSummary(gemini_5h_remaining=0.8, gemini_weekly_remaining=0.7),
    }

    def fetcher(acct_name):
        return quotas.get(acct_name), "proj-1", None

    engine = FailoverEngine(storage)
    res = engine.rotate_smart(quota_fetcher=fetcher, model="gemini")
    assert res.success is True
    assert res.to_account == "acc1"

    # Scenario B: 5h and weekly both equal -> LRU tiebreaker (None first, then oldest last_used_at)
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True, last_used_at=now).to_dict(),
        "acc_recent": AccountMeta(
            name="acc_recent", enabled=True, last_used_at=now - timedelta(hours=1)
        ).to_dict(),
        "acc_older": AccountMeta(
            name="acc_older", enabled=True, last_used_at=now - timedelta(hours=5)
        ).to_dict(),
        "acc_never": AccountMeta(name="acc_never", enabled=True, last_used_at=None).to_dict(),
    }
    quotas_b = {
        "acc_active": QuotaSummary(gemini_5h_remaining=0.0, gemini_weekly_remaining=0.0),
        "acc_recent": QuotaSummary(gemini_5h_remaining=0.8, gemini_weekly_remaining=0.8),
        "acc_older": QuotaSummary(gemini_5h_remaining=0.8, gemini_weekly_remaining=0.8),
        "acc_never": QuotaSummary(gemini_5h_remaining=0.8, gemini_weekly_remaining=0.8),
    }
    res_b = engine.rotate_smart(
        quota_fetcher=lambda a: (quotas_b.get(a), "proj-1", None), model="gemini"
    )
    assert res_b.success is True
    assert res_b.to_account == "acc_never"

    # Remove acc_never -> acc_older should win over acc_recent
    del storage.state["accounts"]["acc_never"]
    res_c = engine.rotate_smart(
        quota_fetcher=lambda a: (quotas_b.get(a), "proj-1", None), model="gemini"
    )
    assert res_c.success is True
    assert res_c.to_account == "acc_older"


def test_rotate_smart_filters_zero_quota():
    from promux.models import QuotaSummary

    storage = FakeStorage()
    now = datetime.now(timezone.utc)
    storage.state["active"] = "acc_active"
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True, last_used_at=now).to_dict(),
        "acc_zero_5h": AccountMeta(name="acc_zero_5h", enabled=True).to_dict(),
        "acc_zero_wk": AccountMeta(name="acc_zero_wk", enabled=True).to_dict(),
        "acc_valid": AccountMeta(name="acc_valid", enabled=True).to_dict(),
    }
    quotas = {
        "acc_active": QuotaSummary(gemini_5h_remaining=0.0, gemini_weekly_remaining=0.0),
        "acc_zero_5h": QuotaSummary(gemini_5h_remaining=0.0, gemini_weekly_remaining=0.9),
        "acc_zero_wk": QuotaSummary(gemini_5h_remaining=0.9, gemini_weekly_remaining=0.0),
        "acc_valid": QuotaSummary(gemini_5h_remaining=0.1, gemini_weekly_remaining=0.1),
    }

    engine = FailoverEngine(storage)
    res = engine.rotate_smart(
        quota_fetcher=lambda a: (quotas.get(a), "proj-1", None), model="gemini"
    )
    assert res.success is True
    assert res.to_account == "acc_valid"


def test_rotate_smart_model_mapping():
    from promux.models import QuotaSummary

    storage = FakeStorage()
    storage.state["active"] = "acc_active"
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True).to_dict(),
        "acc1": AccountMeta(name="acc1", enabled=True).to_dict(),
        "acc2": AccountMeta(name="acc2", enabled=True).to_dict(),
    }

    # acc1 has high Gemini but 0 Claude; acc2 has high Claude
    quotas = {
        "acc_active": QuotaSummary(gemini_5h_remaining=0.0, third_party_5h_remaining=0.0),
        "acc1": QuotaSummary(gemini_5h_remaining=1.0, third_party_5h_remaining=0.0),
        "acc2": QuotaSummary(
            gemini_5h_remaining=0.1,
            third_party_5h_remaining=0.8,
            third_party_weekly_remaining=0.9,
        ),
    }

    def fetcher(acct_name):
        return quotas.get(acct_name), "proj-1", None

    engine = FailoverEngine(storage)

    # For Claude, acc2 wins
    res_claude = engine.rotate_smart(quota_fetcher=fetcher, model="claude")
    assert res_claude.success is True
    assert res_claude.to_account == "acc2"
    assert res_claude.five_hour_remaining == 0.8

    # Reset active
    storage.state["active"] = "acc_active"

    # For GPT, acc2 also wins (uses third_party quota)
    res_gpt = engine.rotate_smart(quota_fetcher=fetcher, model="gpt")
    assert res_gpt.success is True
    assert res_gpt.to_account == "acc2"
    assert res_gpt.five_hour_remaining == 0.8

    # Reset active
    storage.state["active"] = "acc_active"

    # For Gemini, acc1 wins (uses gemini quota)
    res_gemini = engine.rotate_smart(quota_fetcher=fetcher, model="gemini")
    assert res_gemini.success is True
    assert res_gemini.to_account == "acc1"
    assert res_gemini.five_hour_remaining == 1.0


def test_rotate_smart_active_cooldown_only_when_exhausted():
    from promux.models import QuotaSummary

    storage = FakeStorage()
    now = datetime.now(timezone.utc)
    storage.state["active"] = "acc_active"
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True, last_used_at=now).to_dict(),
        "acc_target": AccountMeta(name="acc_target", enabled=True).to_dict(),
    }

    # Case 1: active has 0.0 quota and explicit reset time -> gets cooldown until reset
    reset_future = (now + timedelta(minutes=40)).isoformat()
    quotas_exhausted = {
        "acc_active": QuotaSummary(
            gemini_5h_remaining=0.0,
            gemini_weekly_remaining=0.5,
            gemini_5h_reset=reset_future,
        ),
        "acc_target": QuotaSummary(gemini_5h_remaining=0.9, gemini_weekly_remaining=0.9),
    }

    engine = FailoverEngine(storage)
    res1 = engine.rotate_smart(
        quota_fetcher=lambda a: (quotas_exhausted.get(a), "proj-1", None), model="gemini"
    )
    assert res1.success is True
    assert res1.cooldown_until is not None
    active_meta1 = AccountMeta.from_dict(storage.state["accounts"]["acc_active"])
    assert active_meta1.state == AccountState.COOLDOWN
    delta_mins = (active_meta1.cooldown_until - now).total_seconds() / 60
    assert 38 <= delta_mins <= 42

    # Case 2: active has remaining quota -> stays in STANDBY, cooldown_until is None
    storage.state["active"] = "acc_active"
    storage.state["accounts"]["acc_active"]["cooldown_until"] = None
    quotas_available = {
        "acc_active": QuotaSummary(gemini_5h_remaining=0.5, gemini_weekly_remaining=0.5),
        "acc_target": QuotaSummary(gemini_5h_remaining=0.9, gemini_weekly_remaining=0.9),
    }
    res2 = engine.rotate_smart(
        quota_fetcher=lambda a: (quotas_available.get(a), "proj-1", None), model="gemini"
    )
    assert res2.success is True
    assert res2.cooldown_until is None
    active_meta2 = AccountMeta.from_dict(storage.state["accounts"]["acc_active"])
    assert active_meta2.state == AccountState.STANDBY


def test_rotate_smart_no_eligible_candidates():
    from promux.models import QuotaSummary

    storage = FakeStorage()
    storage.state["active"] = "acc_active"
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True).to_dict(),
    }

    engine = FailoverEngine(storage)
    # Case A: no standby accounts at all
    res1 = engine.rotate_smart(quota_fetcher=lambda a: (QuotaSummary(), "p", None), model="gemini")
    assert res1.success is False
    assert res1.from_account == "acc_active"
    assert res1.to_account is None
    assert res1.reason == "No eligible standby accounts"
    assert res1.cooldown_until is None

    # Case B: standby account exists but all have 0 quota
    storage.state["accounts"]["acc_standby"] = AccountMeta(
        name="acc_standby", enabled=True
    ).to_dict()
    zero_quota = QuotaSummary(gemini_5h_remaining=0.0, gemini_weekly_remaining=0.0)
    res2 = engine.rotate_smart(quota_fetcher=lambda a: (zero_quota, "p", None), model="gemini")
    assert res2.success is False
    assert res2.from_account == "acc_active"
    assert res2.to_account is None
    assert res2.reason == "No standby accounts found with available quota for 'gemini' models"
    assert res2.cooldown_until is None
    active_meta = AccountMeta.from_dict(storage.state["accounts"]["acc_active"])
    assert active_meta.state == AccountState.STANDBY


def test_rotate_smart_switch_failure():
    from promux.models import QuotaSummary

    storage = FakeStorage()
    storage.fail_switch = True
    storage.state["active"] = "acc_active"
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True).to_dict(),
        "acc_standby": AccountMeta(name="acc_standby", enabled=True).to_dict(),
    }
    quota = QuotaSummary(gemini_5h_remaining=0.9, gemini_weekly_remaining=0.9)
    engine = FailoverEngine(storage)
    res = engine.rotate_smart(quota_fetcher=lambda a: (quota, "p", None), model="gemini")

    assert res.success is False
    assert res.from_account == "acc_active"
    assert res.to_account == "acc_standby"
    assert "Failed to switch token to acc_standby" in res.reason
    assert res.cooldown_until is None
    active_meta = AccountMeta.from_dict(storage.state["accounts"]["acc_active"])
    assert active_meta.state == AccountState.STANDBY


def test_parse_iso_reset_minutes():
    engine = FailoverEngine(FakeStorage())
    now = datetime.now(timezone.utc)

    # Future 45 minutes
    future_iso = (now + timedelta(minutes=45)).isoformat()
    assert engine._parse_iso_reset_minutes(future_iso) in (45, 46)

    # Future with Z notation
    future_z = (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert engine._parse_iso_reset_minutes(future_z) in (30, 31)

    # In the past -> returns default
    past_iso = (now - timedelta(minutes=10)).isoformat()
    assert engine._parse_iso_reset_minutes(past_iso) == 60

    # None -> returns default
    assert engine._parse_iso_reset_minutes(None) == 60

    # Invalid string -> returns default
    assert engine._parse_iso_reset_minutes("not-a-date") == 60

    # Custom default
    assert engine._parse_iso_reset_minutes(None, default_minutes=120) == 120
