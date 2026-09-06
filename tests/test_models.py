from datetime import datetime, timedelta, timezone

from promux.constants import (
    ACCOUNTS_DIR,
    CODE_ASSIST_BASE_URL,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_LOCK_TIMEOUT,
    DEFAULT_POLL_SECONDS,
    INDIVIDUAL_QUOTA_RE,
    LOAD_ENDPOINT,
    LOCK_FILE,
    PROMUX_HOME,
    QUOTA_ENDPOINT,
    RESET_HINT_RE,
    RESOURCE_EXHAUSTED_RE,
    STATE_FILE,
    USER_AGENT,
    USERINFO_URL,
    WEEKLY_QUOTA_RE,
)
from promux.models import AccountMeta, AccountState, QuotaBucket, QuotaSummary, RotationResult


def test_account_state_computation():
    now_utc = datetime.now(timezone.utc)
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    # Disabled
    disabled_acct = AccountMeta(name="work", enabled=False)
    assert disabled_acct.state == AccountState.DISABLED

    # Cooldown (naive datetime)
    cool_acct = AccountMeta(
        name="work", enabled=True, cooldown_until=now_naive + timedelta(minutes=30)
    )
    assert cool_acct.state == AccountState.COOLDOWN

    # Standby (cooldown expired)
    standby_acct = AccountMeta(
        name="work", enabled=True, cooldown_until=now_naive - timedelta(minutes=10)
    )
    assert standby_acct.state == AccountState.STANDBY

    # Standby (no cooldown set)
    standby_no_cool = AccountMeta(name="work", enabled=True)
    assert standby_no_cool.state == AccountState.STANDBY

    # Cooldown with timezone-aware datetime
    cool_tz_acct = AccountMeta(
        name="work", enabled=True, cooldown_until=now_utc + timedelta(minutes=30)
    )
    assert cool_tz_acct.state == AccountState.COOLDOWN


def test_account_meta_serialization():
    now = datetime(2026, 9, 3, 21, 0, 0)
    meta = AccountMeta(
        name="primary",
        enabled=True,
        cooldown_until=now + timedelta(hours=1),
        saved_at=now,
        last_used_at=now - timedelta(hours=2),
        email="dev@example.com",
        project_id="proj-123",
        plan_type="PRO",
    )
    data = meta.to_dict()
    assert data["name"] == "primary"
    assert data["enabled"] is True
    assert data["cooldown_until"] == (now + timedelta(hours=1)).isoformat()
    assert data["saved_at"] == now.isoformat()
    assert data["last_used_at"] == (now - timedelta(hours=2)).isoformat()
    assert data["email"] == "dev@example.com"
    assert data["project_id"] == "proj-123"
    assert data["plan_type"] == "PRO"

    restored = AccountMeta.from_dict(data)
    assert restored.name == meta.name
    assert restored.enabled == meta.enabled
    assert restored.cooldown_until == meta.cooldown_until
    assert restored.saved_at == meta.saved_at
    assert restored.last_used_at == meta.last_used_at
    assert restored.email == meta.email
    assert restored.project_id == meta.project_id
    assert restored.plan_type == meta.plan_type


def test_account_meta_from_dict_defaults():
    minimal_data = {"name": "minimal"}
    acct = AccountMeta.from_dict(minimal_data)
    assert acct.name == "minimal"
    assert acct.enabled is True
    assert acct.cooldown_until is None
    assert acct.last_used_at is None
    assert acct.email is None
    assert acct.project_id is None
    assert acct.plan_type == "STANDARD"
    assert isinstance(acct.saved_at, datetime)


def test_quota_summary_dataclass():
    qs = QuotaSummary(
        gemini_5h_remaining=0.85,
        gemini_weekly_remaining=0.57,
        third_party_5h_remaining=0.25,
        third_party_weekly_remaining=0.43,
        gemini_5h_reset="2026-09-03T22:55:18Z",
        third_party_5h_reset="2026-09-04T02:36:43Z",
    )
    assert qs.min_short_window == 0.25
    assert qs.min_weekly == 0.43


def test_quota_summary_defaults():
    qs = QuotaSummary()
    assert qs.gemini_5h_remaining == 1.0
    assert qs.gemini_weekly_remaining == 1.0
    assert qs.third_party_5h_remaining == 1.0
    assert qs.third_party_weekly_remaining == 1.0
    assert qs.min_short_window == 1.0
    assert qs.min_weekly == 1.0
    assert qs.gemini_5h_reset is None
    assert qs.gemini_weekly_reset is None


def test_quota_bucket_dataclass():
    qb = QuotaBucket(
        bucket_id="gemini_5h",
        display_name="Gemini 5-Hour",
        window="5h",
        remaining_fraction=0.85,
        reset_time="2026-09-03T22:55:18Z",
    )
    assert qb.bucket_id == "gemini_5h"
    assert qb.display_name == "Gemini 5-Hour"
    assert qb.window == "5h"
    assert qb.remaining_fraction == 0.85
    assert qb.reset_time == "2026-09-03T22:55:18Z"


def test_rotation_result_dataclass():
    now = datetime(2026, 9, 3, 22, 0, 0)
    rr = RotationResult(
        success=True,
        from_account="account_a",
        to_account="account_b",
        reason="quota_exhausted",
        cooldown_until=now,
    )
    assert rr.success is True
    assert rr.from_account == "account_a"
    assert rr.to_account == "account_b"
    assert rr.reason == "quota_exhausted"
    assert rr.cooldown_until == now


def test_constants_regex_signatures():
    # Individual quota
    assert INDIVIDUAL_QUOTA_RE.search("Error: Individual quota reached for this model")
    assert INDIVIDUAL_QUOTA_RE.search("individual QUOTA reached")

    # Resource exhausted
    assert RESOURCE_EXHAUSTED_RE.search("RESOURCE_EXHAUSTED (code 429)")
    assert RESOURCE_EXHAUSTED_RE.search("RESOURCE_EXHAUSTED")
    assert RESOURCE_EXHAUSTED_RE.search("resource_exhausted ( code 429 )")

    # Weekly quota
    assert WEEKLY_QUOTA_RE.search("weekly quota reached")
    assert WEEKLY_QUOTA_RE.search("Weekly Quota Reached")

    # Reset hint
    match = RESET_HINT_RE.search("Resets in ~2 hours and 15 minutes.")
    assert match is not None
    assert match.group("reset") == "~2 hours and 15 minutes"

    match2 = RESET_HINT_RE.search("Resets in 45m)")
    assert match2 is not None
    assert match2.group("reset") == "45m"


def test_constants_defaults_and_paths():
    assert DEFAULT_POLL_SECONDS == 1.0
    assert DEFAULT_COOLDOWN_MINUTES == 60
    assert DEFAULT_LOCK_TIMEOUT == 10.0
    assert DEFAULT_HTTP_TIMEOUT == 15.0

    assert CODE_ASSIST_BASE_URL == "https://cloudcode-pa.googleapis.com"
    assert LOAD_ENDPOINT == "/v1internal:loadCodeAssist"
    assert QUOTA_ENDPOINT == "/v1internal:retrieveUserQuotaSummary"
    assert USERINFO_URL == "https://www.googleapis.com/oauth2/v3/userinfo"
    assert USER_AGENT == "antigravity"

    assert PROMUX_HOME.name == ".promux"
    assert ACCOUNTS_DIR == PROMUX_HOME / "accounts"
    assert STATE_FILE == PROMUX_HOME / "state.json"
    assert LOCK_FILE == PROMUX_HOME / "manager.lock"
