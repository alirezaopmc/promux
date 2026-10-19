from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from promux.cache import QuotaCache
from promux.cli import build_parser, main
from promux.models import QuotaSummary
from promux.prime import (
    is_eligible_for_prime,
    prime_account,
    prime_all_eligible,
)
from promux.storage import StorageEngine


def write_test_token(path: Path, token_val: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    token_data = {
        "token": {
            "access_token": token_val,
            "refresh_token": f"refresh-{token_val}",
            "expiry": "2030-01-01T00:00:00Z",
        }
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(token_data, f)


@pytest.fixture
def test_env(tmp_path: Path):
    promux_home = tmp_path / "promux"
    gemini_home = tmp_path / "gemini"
    promux_home.mkdir(parents=True, exist_ok=True)
    gemini_home.mkdir(parents=True, exist_ok=True)
    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    cache = QuotaCache(storage.home)
    return storage, cache


def test_is_eligible_for_prime():
    now = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)

    # 1. Past weekly reset -> eligible
    qs1 = QuotaSummary(gemini_weekly_remaining=1.0, gemini_weekly_reset="2026-10-19T10:00:00Z")
    eligible, reason = is_eligible_for_prime(qs1, now, force=False)
    assert eligible is True
    assert reason == "weekly reset reached"

    # 2. Future weekly reset -> ineligible
    qs2 = QuotaSummary(gemini_weekly_remaining=0.95, gemini_weekly_reset="2026-10-25T12:00:00Z")
    eligible, reason = is_eligible_for_prime(qs2, now, force=False)
    assert eligible is False
    assert "active countdown" in reason

    # 3. Future weekly reset with force=True -> eligible
    eligible, reason = is_eligible_for_prime(qs2, now, force=True)
    assert eligible is True
    assert reason == "forced"

    # 4. Floating 100% quota with nil reset -> eligible
    qs3 = QuotaSummary(gemini_weekly_remaining=1.0, gemini_weekly_reset=None)
    eligible, reason = is_eligible_for_prime(qs3, now, force=False)
    assert eligible is True
    assert reason == "weekly reset floating at 100%"

    # 5. Less than 100% quota with nil reset -> ineligible
    qs4 = QuotaSummary(gemini_weekly_remaining=0.8, gemini_weekly_reset=None)
    eligible, reason = is_eligible_for_prime(qs4, now, force=False)
    assert eligible is False
    assert reason == "no weekly reset information"

    # 6. No quota summary
    eligible, reason = is_eligible_for_prime(None, now, force=False)
    assert eligible is False
    assert reason == "no quota data"


def test_prime_account_not_eligible(test_env, monkeypatch):
    storage, cache = test_env
    write_test_token(storage.live_token, "live-tok")
    storage.save_profile("alpha")

    # Future reset
    future = "2030-01-01T00:00:00Z"
    qs = QuotaSummary(gemini_weekly_remaining=0.9, gemini_weekly_reset=future)
    cache.set("alpha", qs)

    agy_called = False

    def mock_run_agy(*args, **kwargs):
        nonlocal agy_called
        agy_called = True

    import promux.prime as prime_mod

    monkeypatch.setattr(prime_mod, "run_agy_prime", mock_run_agy)

    res = prime_account(storage, "alpha", force=False, cache=cache)
    assert res.primed is False
    assert "active countdown" in res.reason
    assert not agy_called


def test_prime_account_active_profile(test_env, monkeypatch):
    storage, cache = test_env
    write_test_token(storage.live_token, "active-tok")
    storage.save_profile("alpha")

    # Expired reset
    past = "2026-10-19T10:00:00Z"
    qs = QuotaSummary(gemini_weekly_remaining=1.0, gemini_weekly_reset=past)
    cache.set("alpha", qs)

    agy_called = False

    def mock_run_agy(*args, **kwargs):
        nonlocal agy_called
        agy_called = True

    new_reset = "2026-10-26T10:00:00Z"

    def mock_query_quota(st, name):
        return QuotaSummary(gemini_weekly_remaining=0.999, gemini_weekly_reset=new_reset)

    import promux.prime as prime_mod

    monkeypatch.setattr(prime_mod, "run_agy_prime", mock_run_agy)
    monkeypatch.setattr(prime_mod, "query_account_quota", mock_query_quota)

    res = prime_account(storage, "alpha", force=False, cache=cache)
    assert res.primed is True
    assert agy_called is True
    assert res.new_reset == new_reset

    cached = cache.get("alpha")
    assert cached is not None
    assert cached.gemini_weekly_reset == new_reset


def test_prime_account_inactive_backup_and_restore(test_env, monkeypatch):
    storage, cache = test_env
    # Save active account
    write_test_token(storage.live_token, "live-active-token")
    storage.save_profile("active_acct")

    # Save target account
    write_test_token(storage.live_token, "target-vault-token")
    storage.save_profile("target_acct")

    # Switch back to active_acct
    storage.switch_profile("active_acct")
    write_test_token(storage.live_token, "live-active-token")

    # Target has floating 100% quota
    cache.set("target_acct", QuotaSummary(gemini_weekly_remaining=1.0, gemini_weekly_reset=None))

    live_token_during_agy = None
    agy_called = False

    def mock_run_agy(*args, **kwargs):
        nonlocal agy_called, live_token_during_agy
        agy_called = True
        with open(storage.live_token, encoding="utf-8") as f:
            data = json.load(f)
            live_token_during_agy = data["token"]["access_token"]

    new_reset = "2026-10-26T10:00:00Z"

    def mock_query_quota(st, name):
        return QuotaSummary(gemini_weekly_remaining=0.999, gemini_weekly_reset=new_reset)

    import promux.prime as prime_mod

    monkeypatch.setattr(prime_mod, "run_agy_prime", mock_run_agy)
    monkeypatch.setattr(prime_mod, "query_account_quota", mock_query_quota)

    res = prime_account(storage, "target_acct", force=False, cache=cache)
    assert res.primed is True
    assert agy_called is True
    assert live_token_during_agy == "target-vault-token"

    # Verify live token was restored
    with open(storage.live_token, encoding="utf-8") as f:
        data = json.load(f)
        assert data["token"]["access_token"] == "live-active-token"

    # Verify no backup files left in gemini_home
    leftover = [p.name for p in storage.gemini_home.iterdir() if "prime_bak" in p.name]
    assert len(leftover) == 0


def test_prime_account_failure_restores_backup(test_env, monkeypatch):
    storage, cache = test_env
    write_test_token(storage.live_token, "live-active-token")
    storage.save_profile("active_acct")

    write_test_token(storage.live_token, "target-vault-token")
    storage.save_profile("target_acct")

    storage.switch_profile("active_acct")
    write_test_token(storage.live_token, "live-active-token")

    def mock_run_agy(*args, **kwargs):
        raise RuntimeError("agy ping failed")

    import promux.prime as prime_mod

    monkeypatch.setattr(prime_mod, "run_agy_prime", mock_run_agy)

    res = prime_account(storage, "target_acct", force=True, cache=cache)
    assert res.primed is False
    assert res.error is not None
    assert "agy ping failed" in res.error

    # Live token must still be restored
    with open(storage.live_token, encoding="utf-8") as f:
        data = json.load(f)
        assert data["token"]["access_token"] == "live-active-token"

    leftover = [p.name for p in storage.gemini_home.iterdir() if "prime_bak" in p.name]
    assert len(leftover) == 0


def test_prime_all_eligible(test_env, monkeypatch):
    storage, cache = test_env
    write_test_token(storage.live_token, "tok1")
    storage.save_profile("acct1")
    write_test_token(storage.live_token, "tok2")
    storage.save_profile("acct2")

    # acct1 eligible, acct2 ineligible
    cache.set("acct1", QuotaSummary(gemini_weekly_remaining=1.0, gemini_weekly_reset=None))
    cache.set(
        "acct2",
        QuotaSummary(gemini_weekly_remaining=0.8, gemini_weekly_reset="2030-01-01T00:00:00Z"),
    )

    primed_accounts = []

    def mock_run_agy(*args, **kwargs):
        pass

    def mock_query_quota(st, name):
        primed_accounts.append(name)
        return QuotaSummary(gemini_weekly_remaining=0.999, gemini_weekly_reset="2026-10-26T10:00:00Z")

    import promux.prime as prime_mod

    monkeypatch.setattr(prime_mod, "run_agy_prime", mock_run_agy)
    monkeypatch.setattr(prime_mod, "query_account_quota", mock_query_quota)

    results = prime_all_eligible(storage, cache=cache, force=False)
    assert len(results) == 2
    r1 = next(r for r in results if r.account == "acct1")
    r2 = next(r for r in results if r.account == "acct2")
    assert r1.primed is True
    assert r2.primed is False


def test_cli_parser_prime_and_quota_flags():
    parser = build_parser()

    # Verify 'prime' subcommand
    prime_args = parser.parse_args(["prime", "my_account", "--dry-run", "--force", "--json"])
    assert prime_args.command == "prime"
    assert prime_args.account == "my_account"
    assert prime_args.dry_run is True
    assert prime_args.force is True
    assert prime_args.json is True

    # Verify 'quota --prime' flag
    quota_args = parser.parse_args(["quota", "--prime"])
    assert quota_args.command == "quota"
    assert quota_args.prime is True


def test_cli_cmd_prime(test_env, monkeypatch, capsys):
    storage, cache = test_env
    write_test_token(storage.live_token, "tok1")
    storage.save_profile("acct1")

    cache.set("acct1", QuotaSummary(gemini_weekly_remaining=1.0, gemini_weekly_reset=None))

    import promux.prime as prime_mod

    monkeypatch.setattr(prime_mod, "run_agy_prime", lambda: None)
    monkeypatch.setattr(
        prime_mod,
        "query_account_quota",
        lambda st, name: QuotaSummary(
            gemini_weekly_remaining=0.999, gemini_weekly_reset="2026-10-26T10:00:00Z"
        ),
    )

    from promux.adapters.agy import AgyAdapter

    monkeypatch.setattr(AgyAdapter, "get_storage", lambda self: storage)

    # Run dry-run
    code = main(["prime", "--dry-run"])
    assert code == 0
    captured = capsys.readouterr()
    assert "would prime" in captured.out or "would be primed" in captured.out

    # Run live
    code = main(["prime", "acct1"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Token primed" in captured.out or "Priming" in captured.out

    # Run JSON
    code = main(["prime", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["account"] == "acct1"


def test_cli_cmd_quota_prime(test_env, monkeypatch, capsys):
    storage, cache = test_env
    write_test_token(storage.live_token, "tok1")
    storage.save_profile("acct1")

    cache.set("acct1", QuotaSummary(gemini_weekly_remaining=1.0, gemini_weekly_reset=None))

    import promux.prime as prime_mod

    monkeypatch.setattr(prime_mod, "run_agy_prime", lambda: None)
    monkeypatch.setattr(
        prime_mod,
        "query_account_quota",
        lambda st, name: QuotaSummary(
            gemini_weekly_remaining=0.999, gemini_weekly_reset="2026-10-26T10:00:00Z"
        ),
    )

    from promux.adapters.agy import AgyAdapter

    monkeypatch.setattr(AgyAdapter, "get_storage", lambda self: storage)

    code = main(["quota", "--prime"])
    assert code == 0
    captured = capsys.readouterr()
    assert "acct1" in captured.out
