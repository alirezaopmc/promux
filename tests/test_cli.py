import json
import threading
import time
from typing import Any

from promux.cli import main
from promux.models import QuotaSummary


def _setup_env(tmp_path, monkeypatch, sample_token_dict=None):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True, exist_ok=True)
    if sample_token_dict:
        live_token = gemini_home / "antigravity-oauth-token"
        live_token.write_text(json.dumps(sample_token_dict))
        live_token.chmod(0o600)

    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))
    return promux_home, gemini_home


def test_cli_list_json(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)

    rc = main(["list", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert data == []


def test_cli_list_table_empty(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)

    rc = main(["list"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "No accounts" in out or "ACTIVE" in out


def test_cli_save_and_whoami(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    # Save
    rc = main(["save", "demo", "--email", "demo@test.com"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "demo" in out

    # Whoami
    rc = main(["whoami", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["active"] == "demo"
    assert res["email"] == "demo@test.com"


def test_cli_save_json(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    rc = main(["save", "demo", "--email", "demo@test.com", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert data["name"] == "demo"
    assert data["email"] == "demo@test.com"


def test_cli_save_auto_fetches_email_and_metadata(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    from promux.quota import QuotaClient

    def mock_fetch_email(self):
        return "auto@fetched.com"

    def mock_load_metadata(self):
        return {"project_id": "auto-project-123", "plan_name": "ULTRA"}

    monkeypatch.setattr(QuotaClient, "fetch_email", mock_fetch_email)
    monkeypatch.setattr(QuotaClient, "load_metadata", mock_load_metadata)

    rc = main(["save", "autoprofile"])
    assert rc == 0
    capsys.readouterr()

    rc = main(["whoami", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["active"] == "autoprofile"
    assert res["email"] == "auto@fetched.com"
    assert res["project_id"] == "auto-project-123"


def test_cli_save_missing_live_token(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)  # no live token written

    rc = main(["save", "demo"])
    assert rc == 1
    _, err = capsys.readouterr()
    assert "not found" in err.lower() or "error" in err.lower()


def test_cli_list_table_with_multiple_accounts(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    # Save first
    assert main(["save", "acct1", "--email", "one@test.com"]) == 0
    # Save second
    assert main(["save", "acct2", "--email", "two@test.com"]) == 0

    capsys.readouterr()  # clear

    # Table output
    rc = main(["list"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "acct1" in out
    assert "acct2" in out
    assert "*" in out  # active indicator

    # JSON output
    rc = main(["list", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert len(data) == 2
    names = [a["name"] for a in data]
    assert "acct1" in names
    assert "acct2" in names
    actives = [a["active"] for a in data if a["name"] == "acct1"]
    assert actives == [True]


def test_cli_switch_success(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    assert main(["save", "primary", "--email", "p@test.com"]) == 0

    token2 = dict(sample_token_dict)
    token2["token"]["access_token"] = "token_secondary"
    gemini_home = tmp_path / ".gemini"
    (gemini_home / "antigravity-oauth-token").write_text(json.dumps(token2))

    assert main(["save", "secondary", "--email", "s@test.com"]) == 0

    # Switch to primary
    rc = main(["switch", "primary"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "primary" in out

    # Verify whoami
    rc = main(["whoami", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["active"] == "primary"

    # Switch with --json
    rc = main(["switch", "secondary", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["success"] is True
    assert res["active"] == "secondary"


def test_cli_switch_nonexistent(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    rc = main(["switch", "ghost"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert "not found" in (out + err).lower() or "error" in (out + err).lower()


def test_cli_whoami_text_mode(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    assert main(["save", "demo", "--email", "demo@test.com"]) == 0
    capsys.readouterr()

    rc = main(["whoami"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "demo" in out
    assert "demo@test.com" in out


def test_cli_whoami_no_active(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)

    rc = main(["whoami"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert "no active" in (out + err).lower()


def test_cli_whoami_no_active_json(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)

    rc = main(["whoami", "--json"])
    assert rc == 1
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["active"] is None


def test_cli_next_success(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    assert main(["save", "acc1", "--email", "1@test.com"]) == 0

    token2 = dict(sample_token_dict)
    token2["token"]["access_token"] = "acc2_token"
    (tmp_path / ".gemini" / "antigravity-oauth-token").write_text(json.dumps(token2))
    assert main(["save", "acc2", "--email", "2@test.com"]) == 0
    capsys.readouterr()

    # Rotate next
    rc = main(["next", "--reason", "rate_limit", "--cooldown", "30", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["success"] is True
    assert res["from_account"] == "acc1"
    assert res["to_account"] == "acc2"
    assert res["reason"] == "rate_limit"


def test_cli_next_no_candidates(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    assert main(["save", "only_one"]) == 0
    capsys.readouterr()

    rc = main(["next"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert "no eligible" in (out + err).lower() or "failed" in (out + err).lower()


def test_cli_remove_success(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cache import QuotaCache
    from promux.cli import get_storage
    from promux.models import QuotaSummary

    assert main(["save", "todelete"]) == 0
    capsys.readouterr()

    storage = get_storage()
    cache = QuotaCache(storage.home)
    cache.set("todelete", QuotaSummary(gemini_5h_remaining=0.8))
    assert cache.get("todelete") is not None

    rc = main(["remove", "todelete"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "todelete" in out
    assert cache.get("todelete") is None

    # In JSON mode
    assert main(["save", "todelete2"]) == 0
    capsys.readouterr()
    rc = main(["remove", "todelete2", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["success"] is True
    assert res["removed"] == "todelete2"


def test_cli_remove_nonexistent(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)

    rc = main(["remove", "ghost"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert "not found" in (out + err).lower() or "error" in (out + err).lower()


def test_cli_quota_success(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    assert main(["save", "demo", "--email", "demo@test.com"]) == 0
    capsys.readouterr()

    from promux.quota import QuotaClient

    def mock_load_metadata(self):
        return {"project_id": "test-companion-proj", "plan_name": "Antigravity"}

    def mock_get_quota(self, project_id):
        return QuotaSummary(
            gemini_5h_remaining=0.85,
            gemini_weekly_remaining=0.57,
            third_party_5h_remaining=0.25,
            third_party_weekly_remaining=0.43,
            gemini_5h_reset="2026-09-03T22:55:18Z",
            third_party_5h_reset="2026-09-04T02:36:43Z",
        )

    monkeypatch.setattr(QuotaClient, "load_metadata", mock_load_metadata)
    monkeypatch.setattr(QuotaClient, "get_quota", mock_get_quota)

    # ASCII table
    rc = main(["quota"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "GEMINI" in out or "Gemini" in out
    assert "CLAUDE" in out or "Claude" in out or "third" in out.lower()
    assert "85" in out
    assert "NEXT RESET" not in out

    # JSON mode
    rc = main(["quota", "demo", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["account"] == "demo"
    assert res["gemini"]["5h_remaining"] == 0.85
    assert res["third_party"]["5h_remaining"] == 0.25
    assert "5h_reset_relative" in res["gemini"]


def test_cli_quota_error_handling(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    assert main(["save", "demo"]) == 0
    capsys.readouterr()

    from promux.quota import QuotaClient

    def mock_get_quota_fail(self, project_id):
        raise ConnectionError("Network unreachable")

    monkeypatch.setattr(QuotaClient, "get_quota", mock_get_quota_fail)

    rc = main(["quota", "demo"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert "error" in (out + err).lower()


def test_cli_quota_nonexistent_profile(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)

    rc = main(["quota", "nonexistent"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert "not found" in (out + err).lower() or "error" in (out + err).lower()


def test_cli_quota_empty_vault(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)

    rc = main(["quota"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "No accounts registered in vault" in out

    rc_json = main(["quota", "--json"])
    assert rc_json == 0
    out_json, _ = capsys.readouterr()
    assert json.loads(out_json) == []


def test_cli_watch_invocation(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    from promux.watch import LogWatcher

    called = {}

    def mock_run_forever(self, on_match=None, cooldown_minutes=None, max_iterations=None):
        called["poll_seconds"] = self.poll_seconds
        called["cooldown_minutes"] = cooldown_minutes

    monkeypatch.setattr(LogWatcher, "run_forever", mock_run_forever)

    rc = main(["watch", "--poll-seconds", "0.5", "--cooldown", "15"])
    assert rc == 0
    assert called.get("poll_seconds") == 0.5
    assert called.get("cooldown_minutes") == 15


def test_cli_argparse_errors(capsys):
    # No arguments: now shows help (rc == 0)
    rc = main([])
    assert rc == 0

    # Invalid command
    rc = main(["invalid_command"])
    assert rc == 2

    # Help
    rc = main(["--help"])
    assert rc == 0


def test_cli_json_flag_before_subcommand(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    assert main(["save", "preflag"]) == 0
    capsys.readouterr()

    # --json before list
    rc = main(["--json", "list"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["name"] == "preflag"

    # --json before whoami
    rc = main(["--json", "whoami"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["active"] == "preflag"


def test_cli_watch_on_match_callback(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    from promux.watch import LogMatch, LogWatcher

    def mock_run_forever(self, on_match=None, cooldown_minutes=None, max_iterations=None):
        if on_match:
            on_match(
                LogMatch(
                    pattern="RESOURCE_EXHAUSTED",
                    line="RESOURCE_EXHAUSTED (code 429)",
                    reset_hint="30m",
                    file_path="/tmp/cli.log",
                )
            )

    monkeypatch.setattr(LogWatcher, "run_forever", mock_run_forever)

    rc = main(["watch"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "Starting log watcher" in out
    assert "RESOURCE_EXHAUSTED" in out
    assert "Reset hint detected: 30m" in out


def test_refresh_token_file_success(tmp_path, monkeypatch):
    from promux.cli import _refresh_token_file

    monkeypatch.setenv("PROMUX_OAUTH_CLIENT_ID", "mock_client_id")
    monkeypatch.setenv("PROMUX_OAUTH_CLIENT_SECRET", "mock_client_secret")

    token_path = tmp_path / "test-token"
    token_data = {
        "token": {
            "access_token": "old_token",
            "refresh_token": "valid_refresh",
            "expiry": "2020-01-01T00:00:00Z",
        }
    }
    token_path.write_text(json.dumps(token_data))

    class MockResponse:
        def __init__(self, data):
            self.data = data

        def read(self):
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def mock_urlopen(req, timeout=10):
        return MockResponse(
            json.dumps({"access_token": "new_refreshed_token", "expires_in": 3600}).encode("utf-8")
        )

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    new_token = _refresh_token_file(token_path, token_data)
    assert new_token == "new_refreshed_token"

    updated = json.loads(token_path.read_text())
    assert updated["token"]["access_token"] == "new_refreshed_token"
    assert "expiry" in updated["token"]


def test_refresh_token_file_failures(tmp_path, monkeypatch):
    from promux.cli import _refresh_token_file

    monkeypatch.setenv("PROMUX_OAUTH_CLIENT_ID", "mock_id")
    monkeypatch.setenv("PROMUX_OAUTH_CLIENT_SECRET", "mock_sec")

    token_path = tmp_path / "test-token"

    # Not a dict token
    assert _refresh_token_file(token_path, {"token": "not-a-dict"}) is None

    # Missing refresh_token
    assert _refresh_token_file(token_path, {"token": {"access_token": "acc"}}) is None

    # Missing credentials
    monkeypatch.delenv("PROMUX_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("PROMUX_OAUTH_CLIENT_SECRET", raising=False)
    assert _refresh_token_file(token_path, {"token": {"refresh_token": "ref"}}) is None

    # Load from PROMUX_HOME/oauth.json
    promux_home = tmp_path / ".promux"
    promux_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    (promux_home / "oauth.json").write_text(
        json.dumps({"client_id": "cfg_id", "client_secret": "cfg_secret"})
    )

    # URLError / network failure
    def mock_urlopen_fail(req, timeout=10):
        raise ConnectionError("Server down")

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_fail)
    data = {"token": {"refresh_token": "ref_tok"}}
    assert _refresh_token_file(token_path, data) is None


def test_get_or_refresh_access_token_expired(tmp_path, monkeypatch):
    from promux.cli import _get_or_refresh_access_token

    token_path = tmp_path / "test-token"
    past_expiry = "2020-01-01T00:00:00Z"
    token_data = {
        "token": {"access_token": "expired_acc", "refresh_token": "ref_tok", "expiry": past_expiry}
    }
    token_path.write_text(json.dumps(token_data))

    # Mock refresh returns refreshed token
    monkeypatch.setattr("promux.cli._refresh_token_file", lambda p, td: "refreshed_acc")

    token = _get_or_refresh_access_token(token_path)
    assert token == "refreshed_acc"


def test_get_or_refresh_access_token_valid(tmp_path):
    from promux.cli import _get_or_refresh_access_token

    token_path = tmp_path / "test-token"
    future_expiry = "2099-01-01T00:00:00Z"
    token_data = {"token": {"access_token": "valid_acc", "expiry": future_expiry}}
    token_path.write_text(json.dumps(token_data))

    token = _get_or_refresh_access_token(token_path)
    assert token == "valid_acc"


def test_cli_quota_401_refresh_retry(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)

    assert main(["save", "demo", "--email", "demo@test.com"]) == 0
    capsys.readouterr()

    from promux.quota import QuotaClient

    calls = {"count": 0}

    def mock_load_metadata(self):
        return {"project_id": "test-companion-proj", "plan_name": "Antigravity"}

    def mock_get_quota(self, project_id):
        calls["count"] += 1
        if calls["count"] == 1:
            raise Exception("HTTP Error 401: Unauthorized")
        return QuotaSummary(
            gemini_5h_remaining=0.90,
            gemini_weekly_remaining=0.70,
            third_party_5h_remaining=0.50,
            third_party_weekly_remaining=0.50,
            gemini_5h_reset="2026-09-03T22:55:18Z",
            third_party_5h_reset="2026-09-04T02:36:43Z",
        )

    monkeypatch.setattr(QuotaClient, "load_metadata", mock_load_metadata)
    monkeypatch.setattr(QuotaClient, "get_quota", mock_get_quota)
    monkeypatch.setattr("promux.cli._refresh_token_file", lambda p, td: "new_token_401")

    rc = main(["quota", "demo"])
    assert rc == 0
    assert calls["count"] == 2
    out, _ = capsys.readouterr()
    assert "Gemini" in out


def test_cli_completion_bash(capsys):
    rc = main(["completion", "bash"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "_promux_completion" in out


def test_cli_completion_zsh(capsys):
    rc = main(["completion", "zsh"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "#compdef promux" in out


def test_cli_completion_invalid_shell(capsys):
    rc = main(["completion", "fish"])
    assert rc == 2
    out, err = capsys.readouterr()
    assert "invalid choice" in (out + err).lower()


def test_fetch_account_quota_success(tmp_path, sample_token_dict, monkeypatch):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import _fetch_account_quota, get_storage, main
    from promux.quota import QuotaClient

    main(["save", "testacc", "--email", "test@test.com"])

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "proj-123"})
    monkeypatch.setattr(
        QuotaClient,
        "get_quota",
        lambda self, pid: QuotaSummary(
            gemini_5h_remaining=0.95,
            gemini_weekly_remaining=0.80,
            third_party_5h_remaining=1.0,
            third_party_weekly_remaining=0.40,
            gemini_5h_reset="2026-09-06T18:00:00Z",
        ),
    )

    storage = get_storage()
    qs, pid, err, is_cached = _fetch_account_quota(storage, "testacc")
    assert err is None
    assert pid == "proj-123"
    assert qs is not None
    assert qs.gemini_5h_remaining == 0.95
    assert is_cached is False


def test_fetch_account_quota_caches_project_id_transactionally(tmp_path, sample_token_dict, monkeypatch):
    from contextlib import contextmanager

    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import _fetch_account_quota, get_storage, main
    from promux.quota import QuotaClient

    main(["save", "testacc", "--email", "test@test.com"])

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "proj-tx-123"})
    monkeypatch.setattr(
        QuotaClient,
        "get_quota",
        lambda self, pid: QuotaSummary(
            gemini_5h_remaining=0.95,
            gemini_weekly_remaining=0.80,
            third_party_5h_remaining=1.0,
            third_party_weekly_remaining=0.40,
            gemini_5h_reset="2026-09-06T18:00:00Z",
        ),
    )

    storage = get_storage()
    tx_called = []
    orig_tx = storage.transaction

    @contextmanager
    def spy_transaction():
        tx_called.append(True)
        with orig_tx() as s:
            yield s

    monkeypatch.setattr(storage, "transaction", spy_transaction)

    qs, pid, err, is_cached = _fetch_account_quota(storage, "testacc")
    assert err is None
    assert pid == "proj-tx-123"
    assert len(tx_called) == 1
    assert storage.load_state()["accounts"]["testacc"]["project_id"] == "proj-tx-123"
    assert is_cached is False


def test_fetch_account_quota_nonexistent(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from promux.cli import _fetch_account_quota, get_storage

    storage = get_storage()
    qs, pid, err, is_cached = _fetch_account_quota(storage, "nonexistent")
    assert qs is None
    assert err is not None
    assert "not found" in err.lower()
    assert is_cached is False


def test_fetch_account_quota_401_retry_success(tmp_path, sample_token_dict, monkeypatch):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import _fetch_account_quota, get_storage, main
    from promux.quota import QuotaClient

    main(["save", "testacc", "--email", "test@test.com"])

    calls = {"count": 0}

    def mock_load_metadata(self):
        return {"project_id": "proj-401"}

    def mock_get_quota(self, pid):
        calls["count"] += 1
        if calls["count"] == 1:
            raise Exception("HTTP Error 401: Unauthorized")
        return QuotaSummary(
            gemini_5h_remaining=0.85,
            gemini_weekly_remaining=0.75,
            third_party_5h_remaining=0.90,
            third_party_weekly_remaining=0.60,
        )

    monkeypatch.setattr(QuotaClient, "load_metadata", mock_load_metadata)
    monkeypatch.setattr(QuotaClient, "get_quota", mock_get_quota)
    monkeypatch.setattr("promux.cli._refresh_token_file", lambda p, td: "new_token_401")

    storage = get_storage()
    qs, pid, err, is_cached = _fetch_account_quota(storage, "testacc")
    assert err is None
    assert pid == "proj-401"
    assert qs is not None
    assert qs.gemini_5h_remaining == 0.85
    assert calls["count"] == 2
    assert is_cached is False


def test_fetch_account_quota_401_refresh_failure(tmp_path, sample_token_dict, monkeypatch):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import _fetch_account_quota, get_storage, main
    from promux.quota import QuotaClient

    main(["save", "testacc", "--email", "test@test.com"])

    def mock_get_quota(self, pid):
        raise Exception("HTTP Error 401: Unauthorized")

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "proj-401"})
    monkeypatch.setattr(QuotaClient, "get_quota", mock_get_quota)
    monkeypatch.setattr("promux.cli._refresh_token_file", lambda p, td: None)

    storage = get_storage()
    qs, pid, err, is_cached = _fetch_account_quota(storage, "testacc")
    assert qs is None
    assert pid == "proj-401"
    assert err is not None
    assert "401" in err
    assert is_cached is False


def test_cli_quota_all_profiles_matrix(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.quota import QuotaClient

    main(["save", "acc1", "--email", "1@test.com"])
    token2 = dict(sample_token_dict)
    token2["token"]["access_token"] = "acc2_token"
    (tmp_path / ".gemini" / "antigravity-oauth-token").write_text(json.dumps(token2))
    main(["save", "acc2", "--email", "2@test.com"])
    capsys.readouterr()

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "test-proj"})
    monkeypatch.setattr(
        QuotaClient,
        "get_quota",
        lambda self, pid: QuotaSummary(
            gemini_5h_remaining=0.961,
            gemini_weekly_remaining=0.82,
            third_party_5h_remaining=1.0,
            third_party_weekly_remaining=0.317,
            gemini_5h_reset="2026-09-06T16:58:03Z",
        ),
    )

    rc = main(["quota"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "PROFILE" in out
    assert "GEMINI (5H)" in out
    assert "CLAUDE (5H)" in out
    assert "acc1" in out
    assert "acc2" in out
    assert "96.1%" in out


def test_cli_quota_all_profiles_json(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.quota import QuotaClient

    main(["save", "acc1"])
    capsys.readouterr()

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "test-proj"})
    monkeypatch.setattr(
        QuotaClient,
        "get_quota",
        lambda self, pid: QuotaSummary(
            gemini_5h_remaining=0.5,
            gemini_weekly_remaining=0.5,
            third_party_5h_remaining=0.5,
            third_party_weekly_remaining=0.5,
        ),
    )

    rc = main(["quota", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["account"] == "acc1"
    assert data[0]["active"] is True
    assert "gemini" in data[0]


def test_cli_quota_single_profile_detail(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.quota import QuotaClient

    main(["save", "acc1"])
    capsys.readouterr()

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "test-proj"})
    monkeypatch.setattr(
        QuotaClient,
        "get_quota",
        lambda self, pid: QuotaSummary(
            gemini_5h_remaining=0.85,
            gemini_weekly_remaining=0.60,
            third_party_5h_remaining=0.20,
            third_party_weekly_remaining=0.40,
            gemini_5h_reset="2026-09-06T20:00:00Z",
        ),
    )

    rc = main(["quota", "acc1"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "Quota for account 'acc1'" in out
    assert "MODEL GROUP" in out
    assert "Gemini Models" in out
    assert "REMAINING & RESET" in out


def test_cli_quota_all_profiles_with_isolated_error(
    tmp_path, sample_token_dict, monkeypatch, capsys
):
    import copy

    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import get_storage
    from promux.quota import QuotaClient

    main(["save", "acc1", "--email", "1@test.com"])
    main(["save", "acc2", "--email", "2@test.com"])
    main(["switch", "acc1"])
    capsys.readouterr()

    storage = get_storage()
    token2 = copy.deepcopy(sample_token_dict)
    token2["token"]["access_token"] = "acc2_token"
    (storage.accounts_dir / "acc2" / "antigravity-oauth-token").write_text(json.dumps(token2))

    def mock_get_quota(self, pid):
        if self.token == "acc2_token":
            raise Exception("HTTP Error 401: Unauthorized")
        return QuotaSummary(
            gemini_5h_remaining=0.9,
            gemini_weekly_remaining=0.8,
            third_party_5h_remaining=0.7,
            third_party_weekly_remaining=0.6,
        )

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "test-proj"})
    monkeypatch.setattr(QuotaClient, "get_quota", mock_get_quota)
    monkeypatch.setattr("promux.cli._refresh_token_file", lambda p, td: None)

    rc = main(["quota"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "acc1" in out
    assert "acc2" in out
    assert "[AUTH ERROR]" in out
    assert "90.0%" in out

    rc_json = main(["quota", "--json"])
    assert rc_json == 0
    out_json, _ = capsys.readouterr()
    data = json.loads(out_json)
    assert len(data) == 2
    acc2_rec = next(d for d in data if d["account"] == "acc2")
    assert "error" in acc2_rec
    assert "401" in acc2_rec["error"]


def test_constants_default_credentials(monkeypatch):
    import importlib
    import promux.constants

    monkeypatch.delenv("PROMUX_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("PROMUX_OAUTH_CLIENT_SECRET", raising=False)
    importlib.reload(promux.constants)

    assert promux.constants.OAUTH_CLIENT_ID == "REDACTED_OAUTH_CLIENT_ID"
    assert promux.constants.OAUTH_CLIENT_SECRET == "REDACTED_OAUTH_CLIENT_SECRET"
    assert promux.constants.DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS == 60
    assert promux.constants.DEFAULT_PROACTIVE_REFRESH_INTERVAL_SECONDS == 900
    monkeypatch.undo()
    importlib.reload(promux.constants)


def test_constants_env_override(monkeypatch):
    import importlib
    import promux.constants

    monkeypatch.setenv("PROMUX_OAUTH_CLIENT_ID", "custom-client-id")
    monkeypatch.setenv("PROMUX_OAUTH_CLIENT_SECRET", "custom-client-secret")
    importlib.reload(promux.constants)

    assert promux.constants.OAUTH_CLIENT_ID == "custom-client-id"
    assert promux.constants.OAUTH_CLIENT_SECRET == "custom-client-secret"
    monkeypatch.undo()
    importlib.reload(promux.constants)


def test_is_token_expired_buffer():
    from datetime import datetime, timedelta, timezone
    from promux.cli import _is_token_expired

    now = datetime.now(timezone.utc)
    # Token expiring in 30s is expired under 60s buffer
    tok_near = {"token": {"expiry": (now + timedelta(seconds=30)).isoformat()}}
    assert _is_token_expired(tok_near, buffer_seconds=60) is True

    # Token expiring in 120s is NOT expired under 60s buffer
    tok_far = {"token": {"expiry": (now + timedelta(seconds=120)).isoformat()}}
    assert _is_token_expired(tok_far, buffer_seconds=60) is False

    # Default buffer_seconds is 60s
    assert _is_token_expired(tok_near) is True
    assert _is_token_expired(tok_far) is False

    # Missing or invalid expiry
    assert _is_token_expired({}) is True
    assert _is_token_expired({"token": {}}) is True
    assert _is_token_expired(None) is True
    assert _is_token_expired({"token": {"expiry": "invalid-datetime"}}) is True


def test_native_refresh_updates_refresh_token_if_provided(tmp_path, monkeypatch):
    from promux.cli import _refresh_token_native

    token_path = tmp_path / "antigravity-oauth-token"
    token_data = {
        "token": {
            "access_token": "old_acc",
            "refresh_token": "old_refresh",
            "expiry": "2020-01-01T00:00:00Z",
        },
        "auth_method": "consumer",
    }
    token_path.write_text(json.dumps(token_data))

    class MockResp:
        def read(self):
            return json.dumps(
                {
                    "access_token": "new_acc",
                    "refresh_token": "rotated_refresh",
                    "expires_in": 3600,
                }
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=10: MockResp())

    new_acc = _refresh_token_native(token_path, token_data)
    assert new_acc == "new_acc"

    saved = json.loads(token_path.read_text())
    assert saved["token"]["access_token"] == "new_acc"
    assert saved["token"]["refresh_token"] == "rotated_refresh"
    assert saved["auth_method"] == "consumer"
    assert (token_path.stat().st_mode & 0o777) == 0o600


def test_native_refresh_keeps_existing_refresh_token_if_not_in_response(tmp_path, monkeypatch):
    from promux.cli import _refresh_token_native

    token_path = tmp_path / "antigravity-oauth-token"
    token_data = {
        "token": {
            "access_token": "old_acc",
            "refresh_token": "old_refresh",
            "expiry": "2020-01-01T00:00:00Z",
        },
    }
    token_path.write_text(json.dumps(token_data))

    class MockResp:
        def read(self):
            return json.dumps(
                {
                    "access_token": "new_acc_only",
                    "expires_in": 3600,
                }
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=10: MockResp())

    new_acc = _refresh_token_native(token_path, token_data)
    assert new_acc == "new_acc_only"

    saved = json.loads(token_path.read_text())
    assert saved["token"]["access_token"] == "new_acc_only"
    assert saved["token"]["refresh_token"] == "old_refresh"


def test_native_refresh_failure_handling(tmp_path, monkeypatch):
    from promux.cli import _refresh_token_native

    token_path = tmp_path / "antigravity-oauth-token"

    # Missing refresh_token in token_data
    assert _refresh_token_native(token_path, {"token": {"access_token": "acc"}}) is None

    # Invalid token structure
    assert _refresh_token_native(token_path, {"token": "not-a-dict"}) is None

    # HTTP / Network error during urlopen
    token_data = {
        "token": {
            "access_token": "old_acc",
            "refresh_token": "old_refresh",
            "expiry": "2020-01-01T00:00:00Z",
        }
    }
    token_path.write_text(json.dumps(token_data))

    def mock_urlopen_err(req, timeout=10):
        raise ConnectionError("Network unreachable")

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_err)
    assert _refresh_token_native(token_path, token_data) is None


def test_get_or_refresh_access_token_uses_buffer(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from promux.cli import _get_or_refresh_access_token

    token_path = tmp_path / "test-token"
    # Token expiring in 30 seconds (within default 60s buffer)
    near_expiry = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    token_data = {
        "token": {
            "access_token": "old_acc",
            "refresh_token": "ref_tok",
            "expiry": near_expiry,
        }
    }
    token_path.write_text(json.dumps(token_data))

    monkeypatch.setattr("promux.cli._refresh_token_file", lambda p, td: "buffer_refreshed_acc")

    token = _get_or_refresh_access_token(token_path)
    assert token == "buffer_refreshed_acc"


def test_refresh_token_fallback_agy_success(tmp_path, monkeypatch):
    import subprocess
    from promux.cli import _refresh_token_fallback_agy, get_storage

    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = get_storage()
    live_token = storage.live_token
    live_token.write_text(json.dumps({"token": {"access_token": "original_live"}}))

    target_dir = storage.accounts_dir / "target_acct"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_token = target_dir / "antigravity-oauth-token"
    target_token.write_text(json.dumps({"token": {"access_token": "target_old", "expiry": "2020-01-01T00:00:00Z"}}))

    # Mock shutil.which to say 'agy' exists
    monkeypatch.setattr("shutil.which", lambda cmd: "/mock/bin/agy" if cmd == "agy" else None)

    # Mock subprocess.run to simulate agy updating the staged live token
    def mock_run(cmd, capture_output=True, timeout=10, check=False):
        # Simulate agy refreshing the live token file
        live_token.write_text(json.dumps({"token": {"access_token": "target_renewed_by_agy", "expiry": "2030-01-01T00:00:00Z"}}))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"gemini-3.8-flash", stderr=b"")

    monkeypatch.setattr("subprocess.run", mock_run)

    new_acc = _refresh_token_fallback_agy(target_token, storage)
    assert new_acc == "target_renewed_by_agy"

    # Verify target token in vault was updated
    vault_data = json.loads(target_token.read_text())
    assert vault_data["token"]["access_token"] == "target_renewed_by_agy"

    # Verify original live token was restored!
    live_data = json.loads(live_token.read_text())
    assert live_data["token"]["access_token"] == "original_live"


def test_refresh_token_fallback_agy_no_agy(tmp_path, monkeypatch):
    from promux.cli import _refresh_token_fallback_agy, get_storage

    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = get_storage()
    target_dir = storage.accounts_dir / "target_acct"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_token = target_dir / "antigravity-oauth-token"
    target_token.write_text(json.dumps({"token": {"access_token": "target_old"}}))

    # Mock shutil.which to say 'agy' does NOT exist
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    assert _refresh_token_fallback_agy(target_token, storage) is None


def test_refresh_token_fallback_agy_no_token_file(tmp_path, monkeypatch):
    from promux.cli import _refresh_token_fallback_agy, get_storage

    storage = get_storage()
    non_existent = storage.accounts_dir / "missing" / "token"
    monkeypatch.setattr("shutil.which", lambda cmd: "/mock/bin/agy")

    assert _refresh_token_fallback_agy(non_existent, storage) is None


def test_refresh_token_fallback_agy_when_live_did_not_exist(tmp_path, monkeypatch):
    import subprocess
    from promux.cli import _refresh_token_fallback_agy, get_storage

    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = get_storage()
    live_token = storage.live_token
    # live_token does NOT exist initially
    if live_token.exists():
        live_token.unlink()

    target_dir = storage.accounts_dir / "target_acct"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_token = target_dir / "antigravity-oauth-token"
    target_token.write_text(json.dumps({"token": {"access_token": "target_old", "expiry": "2020-01-01T00:00:00Z"}}))

    monkeypatch.setattr("shutil.which", lambda cmd: "/mock/bin/agy" if cmd == "agy" else None)

    def mock_run(cmd, capture_output=True, timeout=10, check=False):
        assert live_token.exists()
        live_token.write_text(json.dumps({"token": {"access_token": "target_renewed_live_none", "expiry": "2030-01-01T00:00:00Z"}}))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", mock_run)

    new_acc = _refresh_token_fallback_agy(target_token, storage)
    assert new_acc == "target_renewed_live_none"

    vault_data = json.loads(target_token.read_text())
    assert vault_data["token"]["access_token"] == "target_renewed_live_none"

    # Verify live_token was cleaned up because it didn't exist originally
    assert not live_token.exists()


def test_refresh_token_fallback_agy_failure_or_timeout(tmp_path, monkeypatch):
    import subprocess
    from promux.cli import _refresh_token_fallback_agy, get_storage

    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = get_storage()
    live_token = storage.live_token
    live_token.write_text(json.dumps({"token": {"access_token": "original_live"}}))

    target_dir = storage.accounts_dir / "target_acct"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_token = target_dir / "antigravity-oauth-token"
    target_token.write_text(json.dumps({"token": {"access_token": "target_old", "expiry": "2020-01-01T00:00:00Z"}}))

    monkeypatch.setattr("shutil.which", lambda cmd: "/mock/bin/agy" if cmd == "agy" else None)

    def mock_run_timeout(cmd, capture_output=True, timeout=10, check=False):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr("subprocess.run", mock_run_timeout)

    new_acc = _refresh_token_fallback_agy(target_token, storage)
    assert new_acc is None

    # Target token unchanged
    vault_data = json.loads(target_token.read_text())
    assert vault_data["token"]["access_token"] == "target_old"

    # Live token restored
    live_data = json.loads(live_token.read_text())
    assert live_data["token"]["access_token"] == "original_live"


def test_refresh_token_fallback_agy_same_file(tmp_path, monkeypatch):
    import subprocess
    from promux.cli import _refresh_token_fallback_agy, get_storage

    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = get_storage()
    live_token = storage.live_token
    live_token.write_text(json.dumps({"token": {"access_token": "live_old", "expiry": "2020-01-01T00:00:00Z"}}))

    monkeypatch.setattr("shutil.which", lambda cmd: "/mock/bin/agy" if cmd == "agy" else None)

    def mock_run(cmd, capture_output=True, timeout=10, check=False):
        live_token.write_text(json.dumps({"token": {"access_token": "live_renewed", "expiry": "2030-01-01T00:00:00Z"}}))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", mock_run)

    new_acc = _refresh_token_fallback_agy(live_token, storage)
    assert new_acc == "live_renewed"

    live_data = json.loads(live_token.read_text())
    assert live_data["token"]["access_token"] == "live_renewed"


def test_refresh_token_file_two_tier(tmp_path, monkeypatch):
    from promux.cli import _refresh_token_file, get_storage

    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = get_storage()
    token_path = tmp_path / "token"
    token_data = {"token": {"access_token": "old", "refresh_token": "ref"}}

    # Case 1: Native refresh succeeds
    monkeypatch.setattr("promux.cli._refresh_token_native", lambda p, td: "tier1_acc")
    fallback_called = []
    monkeypatch.setattr("promux.cli._refresh_token_fallback_agy", lambda p, s: fallback_called.append(True) or "tier2_acc")

    res = _refresh_token_file(token_path, token_data, storage=storage)
    assert res == "tier1_acc"
    assert len(fallback_called) == 0

    # Case 2: Native fails, no storage provided -> returns None
    monkeypatch.setattr("promux.cli._refresh_token_native", lambda p, td: None)
    res_no_storage = _refresh_token_file(token_path, token_data, storage=None)
    assert res_no_storage is None
    assert len(fallback_called) == 0

    # Case 3: Native fails, storage provided -> falls back to Tier 2
    res_with_storage = _refresh_token_file(token_path, token_data, storage=storage)
    assert res_with_storage == "tier2_acc"
    assert len(fallback_called) == 1


def test_get_or_refresh_access_token_passes_storage(tmp_path, monkeypatch):
    from promux.cli import _get_or_refresh_access_token, get_storage

    storage = get_storage()
    token_path = tmp_path / "token"
    token_data = {
        "token": {
            "access_token": "expired_acc",
            "refresh_token": "ref",
            "expiry": "2020-01-01T00:00:00Z",
        }
    }
    token_path.write_text(json.dumps(token_data))

    monkeypatch.setattr("promux.cli._refresh_token_native", lambda p, td: None)
    monkeypatch.setattr("promux.cli._refresh_token_fallback_agy", lambda p, s: "fallback_token_val")

    # When storage passed, fallback succeeds
    res = _get_or_refresh_access_token(token_path, storage=storage)
    assert res == "fallback_token_val"


def test_cli_refresh_single_and_all(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main

    assert main(["save", "acct1", "--email", "a1@test.com"]) == 0
    assert main(["save", "acct2", "--email", "a2@test.com"]) == 0
    capsys.readouterr()

    # Mock native refresh
    monkeypatch.setattr("promux.cli._refresh_token_native", lambda p, td: "refreshed_acc")

    # Refresh specific with --force
    rc = main(["refresh", "acct1", "--force"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "acct1" in out
    assert "REFRESHED" in out

    # Refresh all with --json
    rc = main(["refresh", "--force", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert len(data) == 2
    assert all(d["status"] == "REFRESHED" for d in data)


def test_cli_refresh_unchanged_when_not_expired(tmp_path, sample_token_dict, monkeypatch, capsys):
    sample_token_dict["token"]["expiry"] = "2030-01-01T00:00:00Z"
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main

    assert main(["save", "acct1", "--email", "a1@test.com"]) == 0
    capsys.readouterr()

    # Without --force, unexpired token remains UNCHANGED
    rc = main(["refresh", "acct1"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "acct1" in out
    assert "UNCHANGED" in out
    assert "valid" in out


def test_cli_refresh_fallback_agy_method(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main

    assert main(["save", "acct1", "--email", "a1@test.com"]) == 0
    capsys.readouterr()

    monkeypatch.setattr("promux.cli._refresh_token_native", lambda p, td: None)
    monkeypatch.setattr("promux.cli._refresh_token_fallback_agy", lambda p, s: "agy_acc")

    rc = main(["refresh", "acct1", "--force", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["status"] == "REFRESHED"
    assert data[0]["method"] == "fallback (agy)"


def test_cli_refresh_nonexistent_account(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main

    rc = main(["refresh", "nonexistent"])
    assert rc == 1
    _, err = capsys.readouterr()
    assert "nonexistent" in err

    rc_json = main(["refresh", "nonexistent", "--json"])
    assert rc_json == 1
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert "error" in data


def test_cli_refresh_syncs_to_active_live_token(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main, get_storage

    assert main(["save", "active_acct", "--email", "act@test.com"]) == 0
    capsys.readouterr()

    storage = get_storage()
    # Write refreshed token when native refresh is called
    def mock_refresh_native(token_path, token_data):
        token_data["token"]["access_token"] = "new_synced_token"
        token_data["token"]["expiry"] = "2030-01-01T00:00:00Z"
        token_path.write_text(json.dumps(token_data))
        return "new_synced_token"

    monkeypatch.setattr("promux.cli._refresh_token_native", mock_refresh_native)

    rc = main(["refresh", "active_acct", "--force"])
    assert rc == 0

    live_data = json.loads(storage.live_token.read_text())
    assert live_data["token"]["access_token"] == "new_synced_token"


def test_cli_refresh_empty_vault(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)
    from promux.cli import main

    rc = main(["refresh"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "No accounts" in out

    rc = main(["refresh", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert data == []


def test_cli_refresh_failure_returns_1(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main

    assert main(["save", "fail_acct", "--email", "fail@test.com"]) == 0
    capsys.readouterr()

    monkeypatch.setattr("promux.cli._refresh_token_native", lambda p, td: None)
    monkeypatch.setattr("promux.cli._refresh_token_fallback_agy", lambda p, s: None)

    rc = main(["refresh", "fail_acct", "--force"])
    assert rc == 1
    out, _ = capsys.readouterr()
    assert "fail_acct" in out
    assert "FAILED" in out
    assert "failed" in out

    rc_json = main(["refresh", "fail_acct", "--force", "--json"])
    assert rc_json == 1
    out_json, _ = capsys.readouterr()
    data = json.loads(out_json)
    assert len(data) == 1
    assert data[0]["status"] == "FAILED"
    assert data[0]["method"] == "failed"


def test_cli_refresh_missing_token_file(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main, get_storage

    assert main(["save", "ghost", "--email", "ghost@test.com"]) == 0
    capsys.readouterr()

    storage = get_storage()
    # Delete token file and live token
    vault_token = storage.accounts_dir / "ghost" / "antigravity-oauth-token"
    if vault_token.exists():
        vault_token.unlink()
    if storage.live_token.exists():
        storage.live_token.unlink()

    rc = main(["refresh", "ghost", "--force"])
    assert rc == 1
    out, _ = capsys.readouterr()
    assert "ghost" in out
    assert "FAILED" in out


def test_atomic_copy_token_permissions_and_atomicity(tmp_path):
    from promux.cli import _atomic_copy_token

    src = tmp_path / "src_token"
    src.write_text("token_content")
    dst = tmp_path / "sub" / "dst_token"

    _atomic_copy_token(src, dst)
    assert dst.exists()
    assert dst.read_text() == "token_content"
    assert oct(dst.stat().st_mode & 0o777) == oct(0o600)


def test_refresh_account_token_direct(tmp_path, sample_token_dict, monkeypatch):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import _refresh_account_token, get_storage, main

    storage = get_storage()
    # 1. Non-existent account
    ok, msg = _refresh_account_token(storage, "nonexistent")
    assert ok is False
    assert "not found" in msg.lower()

    # Save an account
    assert main(["save", "acct1", "--email", "a1@test.com"]) == 0

    # 2. Token still valid without force
    sample_token_dict["token"]["expiry"] = "2030-01-01T00:00:00Z"
    vault_token = storage.accounts_dir / "acct1" / "antigravity-oauth-token"
    vault_token.write_text(json.dumps(sample_token_dict))
    ok, msg = _refresh_account_token(storage, "acct1", force=False)
    assert ok is True
    assert "still valid" in msg.lower()

    # 3. Force refresh success
    monkeypatch.setattr("promux.cli._refresh_token_native", lambda p, td: "new_token")
    ok, msg = _refresh_account_token(storage, "acct1", force=True)
    assert ok is True
    assert "refreshed successfully" in msg.lower()

    # 4. Refresh failure
    monkeypatch.setattr("promux.cli._refresh_token_native", lambda p, td: None)
    monkeypatch.setattr("promux.cli._refresh_token_fallback_agy", lambda p, s: None)
    ok, msg = _refresh_account_token(storage, "acct1", force=True)
    assert ok is False
    assert "failed" in msg.lower()

    # 5. Missing token data
    vault_token.unlink()
    if storage.live_token.exists():
        storage.live_token.unlink()
    ok, msg = _refresh_account_token(storage, "acct1", force=True)
    assert ok is False
    assert "no token data found" in msg.lower()



def test_cli_refresh_identity_mismatch_prevents_sync(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main, get_storage

    assert main(["save", "acct1", "--email", "a1@test.com"]) == 0
    capsys.readouterr()

    storage = get_storage()
    # Alter live_token so it has a different refresh_token and a newer expiry
    live_dict = {
        "token": {
            "access_token": "different_acc",
            "refresh_token": "DIFFERENT_RT",
            "expiry": "2030-01-01T00:00:00Z",
        }
    }
    storage.live_token.write_text(json.dumps(live_dict))

    # Mock native refresh
    monkeypatch.setattr("promux.cli._refresh_token_native", lambda p, td: "refreshed_acc")

    rc = main(["refresh", "acct1"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "acct1" in out
    assert "REFRESHED" in out

    # Verify vault token still has original refresh_token, NOT DIFFERENT_RT
    vault_token = storage.accounts_dir / "acct1" / "antigravity-oauth-token"
    vault_data = json.loads(vault_token.read_text())
    assert vault_data["token"]["refresh_token"] == "mock_refresh_token"


def test_cli_switch_auto_refreshes_expired(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main, get_storage

    assert main(["save", "acct1"]) == 0

    token2 = dict(sample_token_dict)
    token2["token"] = dict(sample_token_dict["token"])
    token2["token"]["access_token"] = "expired_acct2"
    token2["token"]["expiry"] = "2020-01-01T00:00:00Z"
    gemini_home = tmp_path / ".gemini"
    (gemini_home / "antigravity-oauth-token").write_text(json.dumps(token2))

    assert main(["save", "acct2"]) == 0
    assert main(["switch", "acct1"]) == 0
    capsys.readouterr()

    monkeypatch.setattr("promux.cli._get_or_refresh_access_token", lambda p, storage=None: "refreshed_acct2")

    rc = main(["switch", "acct2"])
    assert rc == 0
    storage = get_storage()
    live_data = json.loads(storage.live_token.read_text())
    assert live_data["token"]["access_token"] == "refreshed_acct2"


def test_quota_auto_refreshes_vault_and_syncs_live(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main, get_storage
    from promux.quota import QuotaClient

    # Save active profile
    assert main(["save", "live_acct"]) == 0
    capsys.readouterr()
    storage = get_storage()

    # Expire the live token
    live_data = json.loads(storage.live_token.read_text())
    live_data["token"]["expiry"] = "2020-01-01T00:00:00Z"
    storage.live_token.write_text(json.dumps(live_data))

    # Mock refresh to renew
    def mock_refresh_native(p, td):
        td["token"]["access_token"] = "refreshed_live_quota"
        td["token"]["expiry"] = "2030-01-01T00:00:00Z"
        p.write_text(json.dumps(td))
        return "refreshed_live_quota"

    monkeypatch.setattr("promux.cli._refresh_token_native", mock_refresh_native)

    # Mock QuotaClient get_quota and load_metadata
    qs = QuotaSummary()
    qs.gemini_5h_remaining = 0.95
    monkeypatch.setattr(QuotaClient, "load_metadata", lambda s: {"project_id": "proj-live"})
    monkeypatch.setattr(QuotaClient, "get_quota", lambda s, p: qs)

    rc = main(["quota", "live_acct", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["gemini"]["5h_remaining"] == 0.95

    # Check that live token was updated
    updated_live = json.loads(storage.live_token.read_text())
    assert updated_live["token"]["access_token"] == "refreshed_live_quota"

    # Check that vault token was also synced atomically
    vault_file = storage.accounts_dir / "live_acct" / "antigravity-oauth-token"
    updated_vault = json.loads(vault_file.read_text())
    assert updated_vault["token"]["access_token"] == "refreshed_live_quota"


def test_quota_refreshed_in_vault_syncs_to_live(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main, get_storage
    from promux.quota import QuotaClient

    assert main(["save", "vault_acct"]) == 0
    storage = get_storage()

    # Expire vault token and remove live token so vault path is used
    vault_file = storage.accounts_dir / "vault_acct" / "antigravity-oauth-token"
    vault_data = json.loads(vault_file.read_text())
    vault_data["token"]["expiry"] = "2020-01-01T00:00:00Z"
    vault_file.write_text(json.dumps(vault_data))
    if storage.live_token.exists():
        storage.live_token.unlink()

    def mock_refresh_native(p, td):
        td["token"]["access_token"] = "refreshed_from_vault"
        td["token"]["expiry"] = "2030-01-01T00:00:00Z"
        p.write_text(json.dumps(td))
        return "refreshed_from_vault"

    monkeypatch.setattr("promux.cli._refresh_token_native", mock_refresh_native)
    monkeypatch.setattr(QuotaClient, "load_metadata", lambda s: {"project_id": "proj-vault"})
    qs = QuotaSummary()
    qs.gemini_5h_remaining = 0.90
    monkeypatch.setattr(QuotaClient, "get_quota", lambda s, p: qs)

    rc = main(["quota", "vault_acct", "--json"])
    assert rc == 0

    # Check that live token was recreated and synced
    assert storage.live_token.exists()
    updated_live = json.loads(storage.live_token.read_text())
    assert updated_live["token"]["access_token"] == "refreshed_from_vault"


def test_quota_passes_storage_for_fallback_agy(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main, get_storage
    from promux.quota import QuotaClient

    assert main(["save", "agy_acct"]) == 0
    storage = get_storage()

    # Expire live token
    live_data = json.loads(storage.live_token.read_text())
    live_data["token"]["expiry"] = "2020-01-01T00:00:00Z"
    storage.live_token.write_text(json.dumps(live_data))

    # Tier 1 fails
    monkeypatch.setattr("promux.cli._refresh_token_native", lambda p, td: None)

    # Tier 2 succeeds and checks storage argument was passed
    fallback_called = {"called": False}

    def mock_fallback_agy(p, st):
        fallback_called["called"] = True
        assert st is not None
        live_d = json.loads(p.read_text())
        live_d["token"]["access_token"] = "agy_fallback_token"
        live_d["token"]["expiry"] = "2030-01-01T00:00:00Z"
        p.write_text(json.dumps(live_d))
        return "agy_fallback_token"

    monkeypatch.setattr("promux.cli._refresh_token_fallback_agy", mock_fallback_agy)
    monkeypatch.setattr(QuotaClient, "load_metadata", lambda s: {"project_id": "proj-agy"})
    qs = QuotaSummary()
    qs.gemini_5h_remaining = 0.88
    monkeypatch.setattr(QuotaClient, "get_quota", lambda s, p: qs)

    rc = main(["quota", "agy_acct", "--json"])
    assert rc == 0
    assert fallback_called["called"] is True
    updated_live = json.loads(storage.live_token.read_text())
    assert updated_live["token"]["access_token"] == "agy_fallback_token"


def test_quota_401_retry_passes_storage_and_syncs(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main, get_storage
    from promux.quota import QuotaClient

    assert main(["save", "retry_acct"]) == 0
    storage = get_storage()

    calls = {"count": 0}

    def mock_get_quota(self, pid):
        calls["count"] += 1
        if calls["count"] == 1:
            raise Exception("HTTP Error 401: Unauthorized")
        return QuotaSummary(gemini_5h_remaining=0.82)

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda s: {"project_id": "proj-retry"})
    monkeypatch.setattr(QuotaClient, "get_quota", mock_get_quota)

    # Tier 1 fails on 401 retry
    monkeypatch.setattr("promux.cli._refresh_token_native", lambda p, td: None)

    # Tier 2 succeeds on 401 retry
    fallback_called = {"called": False}

    def mock_fallback_agy(p, st):
        fallback_called["called"] = True
        assert st is not None
        live_d = json.loads(p.read_text())
        live_d["token"]["access_token"] = "retry_agy_token"
        live_d["token"]["expiry"] = "2030-01-01T00:00:00Z"
        p.write_text(json.dumps(live_d))
        return "retry_agy_token"

    monkeypatch.setattr("promux.cli._refresh_token_fallback_agy", mock_fallback_agy)

    rc = main(["quota", "retry_acct", "--json"])
    assert rc == 0
    assert calls["count"] == 2
    assert fallback_called["called"] is True

    # Check both live and vault updated
    updated_live = json.loads(storage.live_token.read_text())
    assert updated_live["token"]["access_token"] == "retry_agy_token"
    vault_file = storage.accounts_dir / "retry_acct" / "antigravity-oauth-token"
    updated_vault = json.loads(vault_file.read_text())
    assert updated_vault["token"]["access_token"] == "retry_agy_token"


def test_quota_revoked_token_error_handling(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main, get_storage
    from promux.quota import QuotaClient

    assert main(["save", "revoked_acct"]) == 0
    capsys.readouterr()
    storage = get_storage()

    # Expire live token
    live_data = json.loads(storage.live_token.read_text())
    live_data["token"]["expiry"] = "2020-01-01T00:00:00Z"
    storage.live_token.write_text(json.dumps(live_data))

    # Mock native HTTP call to return Google invalid_grant error
    import io
    import urllib.error

    def mock_urlopen_revoked(req, timeout=10):
        err_bytes = b'{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}'
        raise urllib.error.HTTPError(
            url="https://oauth2.googleapis.com/token",
            code=400,
            msg="Bad Request",
            hdrs={},  # type: ignore[arg-type]
            fp=io.BytesIO(err_bytes),
        )

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_revoked)

    def mock_get_quota(self, pid):
        raise Exception("HTTP Error 401: Unauthorized")

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda s: {"project_id": "proj-revoked"})
    monkeypatch.setattr(QuotaClient, "get_quota", mock_get_quota)

    rc = main(["quota", "revoked_acct", "--json"])
    assert rc == 1
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert "revoked" in res["error"].lower()
    assert "re-authenticate via 'agy'" in res["error"].lower()


def test_quota_multi_profile_does_not_leak_revoked_state(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main, get_storage
    from promux.quota import QuotaClient
    import io
    import urllib.error

    # Create account 1 (acct1)
    assert main(["save", "acct1"]) == 0
    capsys.readouterr()

    # Create account 2 (acct2) with valid unexpired token
    token2 = dict(sample_token_dict)
    token2["token"] = dict(sample_token_dict["token"])
    token2["token"]["access_token"] = "valid_acct2_token"
    token2["token"]["expiry"] = "2030-01-01T00:00:00Z"
    gemini_home = tmp_path / ".gemini"
    (gemini_home / "antigravity-oauth-token").write_text(json.dumps(token2))
    assert main(["save", "acct2"]) == 0
    capsys.readouterr()

    storage = get_storage()

    # Expire active profile (acct1) live token so refresh is attempted
    live_data = json.loads(storage.live_token.read_text())
    live_data["token"]["access_token"] = "revoked_acct1_token"
    live_data["token"]["expiry"] = "2020-01-01T00:00:00Z"
    storage.live_token.write_text(json.dumps(live_data))

    # Ensure standby profile (acct2) vault token is valid and unexpired
    vault_acct2 = storage.accounts_dir / "acct2" / "antigravity-oauth-token"
    vault_acct2.write_text(json.dumps(token2))

    # Mock urlopen to fail on refresh with invalid_grant
    def mock_urlopen_revoked(req, timeout=10):
        err_bytes = b'{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}'
        raise urllib.error.HTTPError(
            url="https://oauth2.googleapis.com/token",
            code=400,
            msg="Bad Request",
            hdrs={},  # type: ignore[arg-type]
            fp=io.BytesIO(err_bytes),
        )

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_revoked)

    def mock_load_metadata(self):
        return {"project_id": "proj-multi"}

    def mock_get_quota(self, pid):
        if self.token == "valid_acct2_token":
            return QuotaSummary(gemini_5h_remaining=0.99)
        raise Exception("HTTP Error 401: Unauthorized")

    monkeypatch.setattr(QuotaClient, "load_metadata", mock_load_metadata)
    monkeypatch.setattr(QuotaClient, "get_quota", mock_get_quota)

    # Run multi-profile quota query
    rc = main(["quota", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert len(res) == 2

    # acct1 had revoked token and should have error
    r1 = next(r for r in res if r["account"] == "acct1")
    assert "error" in r1
    assert "revoked" in r1["error"].lower()

    # acct2 had valid token and should succeed despite acct1 having set revocation flag
    r2 = next(r for r in res if r["account"] == "acct2")
    assert "error" not in r2
    assert r2["gemini"]["5h_remaining"] == 0.99


def test_cmd_quota_redesigned_display(storage_with_profiles, monkeypatch, capsys):
    from promux.cli import cmd_quota
    from promux.models import QuotaSummary

    mock_qs = QuotaSummary(
        gemini_5h_remaining=0.98,
        gemini_weekly_remaining=0.85,
        third_party_5h_remaining=1.0,
        third_party_weekly_remaining=0.925,
        gemini_5h_reset="2099-01-01T14:15:00Z",
        gemini_weekly_reset="2099-01-05T16:00:00Z",
        third_party_5h_reset="2099-01-01T15:00:00Z",
        third_party_weekly_reset="2099-01-06T18:00:00Z",
    )

    def mock_fetch(storage, name):
        return mock_qs, "test-proj", None

    monkeypatch.setattr("promux.cli._fetch_account_quota", mock_fetch)

    # Overview mode
    ret = cmd_quota(storage_with_profiles, name=None, json_out=False)
    assert ret == 0
    captured = capsys.readouterr().out

    # Verify column headers: NEXT RESET is gone, cells contain reset countdowns
    assert "NEXT RESET" not in captured
    assert "GEMINI (5H)" in captured
    assert "GEMINI (WK)" in captured
    assert "CLAUDE (5H)" in captured
    assert "CLAUDE (WK)" in captured
    assert "98.0%" in captured
    assert "85.0%" in captured
    assert "(" in captured and ")" in captured

    # Single profile detail mode
    capsys.readouterr()  # clear buffer
    ret_detail = cmd_quota(storage_with_profiles, name="work", json_out=False)
    assert ret_detail == 0
    detail_captured = capsys.readouterr().out
    assert "REMAINING & RESET" in detail_captured
    assert "98.0%" in detail_captured
    assert "UTC" in detail_captured

    # Overview JSON mode
    capsys.readouterr()
    ret_json = cmd_quota(storage_with_profiles, name=None, json_out=True)
    assert ret_json == 0
    json_captured = capsys.readouterr().out
    overview_data = json.loads(json_captured)
    assert len(overview_data) >= 1
    rec = overview_data[0]
    assert "5h_reset_relative" in rec["gemini"]
    assert "weekly_reset_relative" in rec["gemini"]
    assert "5h_reset_relative" in rec["third_party"]
    assert "weekly_reset_relative" in rec["third_party"]

    # Detail JSON mode
    capsys.readouterr()
    ret_detail_json = cmd_quota(storage_with_profiles, name="work", json_out=True)
    assert ret_detail_json == 0
    detail_json_captured = capsys.readouterr().out
    detail_data = json.loads(detail_json_captured)
    assert "5h_reset_relative" in detail_data["gemini"]
    assert "weekly_reset_relative" in detail_data["gemini"]
    assert "5h_reset_relative" in detail_data["third_party"]
    assert "weekly_reset_relative" in detail_data["third_party"]


def test_cmd_quota_concurrent_execution_and_order(storage_with_profiles, monkeypatch, capsys):
    from promux.cli import cmd_quota
    from promux.models import QuotaSummary

    storage_with_profiles.save_profile("staging", email="staging@company.com", project_id="proj-staging")

    mock_qs = QuotaSummary(
        gemini_5h_remaining=0.98,
        gemini_weekly_remaining=0.85,
        third_party_5h_remaining=1.0,
        third_party_weekly_remaining=0.925,
    )

    calling_threads = set()

    def mock_fetch(storage, name):
        calling_threads.add(threading.current_thread().ident)
        # Delay one account so finishes happen out of original order
        if name == "personal":
            time.sleep(0.05)
        return mock_qs, f"proj-{name}", None

    monkeypatch.setattr("promux.cli._fetch_account_quota", mock_fetch)

    ret = cmd_quota(storage_with_profiles, name=None, json_out=True)
    assert ret == 0
    captured = capsys.readouterr().out
    data = json.loads(captured)

    # Concurrency verification: fetching executed across multiple worker threads
    assert len(calling_threads) > 1

    # Order preservation verification: output order matches storage.list_accounts() exactly
    expected_order = [acct.name for acct in storage_with_profiles.list_accounts()]
    actual_order = [item["account"] for item in data]
    assert actual_order == expected_order


def test_tools_command(capsys):
    ret = main(["tools"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "TOOL" in captured
    assert "NAME" in captured
    assert "ACTIVE PROFILE" in captured
    assert "CAPABILITIES" in captured
    assert "agy" in captured
    assert "Antigravity CLI" in captured
    assert "claude" in captured
    assert "Claude Code" in captured
    assert "codex" in captured
    assert "cursor" in captured
    assert "vault, quota, watch, refresh" in captured
    assert "vault (scaffolded)" in captured


def test_tools_command_json(capsys):
    ret = main(["tools", "--json"])
    assert ret == 0
    captured = capsys.readouterr().out
    tools = json.loads(captured)
    assert isinstance(tools, list)
    assert len(tools) == 4
    tool_map = {t["tool"]: t for t in tools}

    assert tool_map["agy"]["name"] == "Antigravity CLI"
    assert tool_map["agy"]["default"] is True
    assert tool_map["agy"]["capabilities"] == ["vault", "quota", "watch", "refresh"]

    assert tool_map["claude"]["name"] == "Claude Code"
    assert tool_map["claude"]["default"] is False
    assert tool_map["claude"]["capabilities"] == ["vault"]


def test_tools_list_subcommand(capsys):
    ret = main(["tools", "list"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "agy" in captured


def test_tool_first_dispatch(storage_with_profiles, monkeypatch, capsys):
    monkeypatch.setenv("PROMUX_HOME", str(storage_with_profiles.home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(storage_with_profiles.live_token.parent))

    ret = main(["agy", "list"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "work" in captured
    assert "personal" in captured


def test_top_level_fallback(storage_with_profiles, monkeypatch, capsys):
    monkeypatch.setenv("PROMUX_HOME", str(storage_with_profiles.home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(storage_with_profiles.live_token.parent))

    ret = main(["list"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "work" in captured
    assert "personal" in captured


def test_tool_capability_validation_text(capsys):
    ret = main(["claude", "quota"])
    assert ret == 1
    err = capsys.readouterr().err
    assert "Error: Tool 'claude' does not support 'quota'. Supported capabilities: vault" in err

    ret = main(["codex", "watch"])
    assert ret == 1
    err = capsys.readouterr().err
    assert "Error: Tool 'codex' does not support 'watch'. Supported capabilities: vault" in err

    ret = main(["cursor", "refresh"])
    assert ret == 1
    err = capsys.readouterr().err
    assert "Error: Tool 'cursor' does not support 'refresh'. Supported capabilities: vault" in err


def test_tool_capability_validation_json(capsys):
    ret = main(["claude", "quota", "--json"])
    assert ret == 1
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["error"] == "Tool 'claude' does not support 'quota'. Supported capabilities: vault"


def test_tool_first_dispatch_stub_vault(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PROMUX_HOME", str(tmp_path))

    ret = main(["claude", "list"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "No accounts" in captured or "ACTIVE" in captured


def test_cmd_switch_missing_name_without_smart(capsys):
    from promux.cli import main

    code = main(["switch"])
    assert code == 2
    captured = capsys.readouterr()
    assert "Error: must specify account name or pass --smart" in captured.err


def test_cmd_switch_smart_gemini_success(monkeypatch, tmp_path, capsys):
    from promux import cli
    from promux.cli import main
    from promux.models import AccountMeta, QuotaSummary
    from promux.storage import StorageEngine

    promux_home = tmp_path / "promux"
    gemini_home = tmp_path / "gemini"
    promux_home.mkdir()
    gemini_home.mkdir()
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = StorageEngine(promux_home=promux_home / "tools" / "agy", gemini_home=gemini_home)
    (storage.accounts_dir / "acc1").mkdir(parents=True)
    (storage.accounts_dir / "acc1" / "antigravity-oauth-token").write_text('{"access_token":"t1"}')
    (storage.accounts_dir / "acc2").mkdir(parents=True)
    (storage.accounts_dir / "acc2" / "antigravity-oauth-token").write_text('{"access_token":"t2"}')
    storage.save_state({
        "active": "acc1",
        "accounts": {
            "acc1": AccountMeta(name="acc1", enabled=True).to_dict(),
            "acc2": AccountMeta(name="acc2", enabled=True).to_dict(),
        },
    })
    storage.live_token.write_text('{"access_token":"t1"}')

    def mock_fetch(st, name):
        if name == "acc1":
            return QuotaSummary(gemini_5h_remaining=0.0, gemini_weekly_remaining=0.5), "p1", None
        return QuotaSummary(gemini_5h_remaining=0.9, gemini_weekly_remaining=0.85), "p2", None

    monkeypatch.setattr(cli, "_fetch_account_quota", mock_fetch)

    code = main(["switch", "--smart"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Switched active profile to 'acc2'" in captured.out
    assert "gemini 5h: 90.0%" in captured.out
    assert "weekly: 85.0%" in captured.out


def test_cmd_switch_smart_json_output(monkeypatch, tmp_path, capsys):
    from promux import cli
    from promux.cli import main
    from promux.models import AccountMeta, QuotaSummary
    from promux.storage import StorageEngine

    promux_home = tmp_path / "promux"
    gemini_home = tmp_path / "gemini"
    promux_home.mkdir()
    gemini_home.mkdir()
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = StorageEngine(promux_home=promux_home / "tools" / "agy", gemini_home=gemini_home)
    (storage.accounts_dir / "acc1").mkdir(parents=True)
    (storage.accounts_dir / "acc1" / "antigravity-oauth-token").write_text('{"access_token":"t1"}')
    (storage.accounts_dir / "acc2").mkdir(parents=True)
    (storage.accounts_dir / "acc2" / "antigravity-oauth-token").write_text('{"access_token":"t2"}')
    storage.save_state({
        "active": "acc1",
        "accounts": {
            "acc1": AccountMeta(name="acc1", enabled=True).to_dict(),
            "acc2": AccountMeta(name="acc2", enabled=True).to_dict(),
        },
    })
    storage.live_token.write_text('{"access_token":"t1"}')

    def mock_fetch(st, name):
        if name == "acc1":
            return QuotaSummary(gemini_5h_remaining=0.0, gemini_weekly_remaining=0.5), "p1", None
        return QuotaSummary(gemini_5h_remaining=0.9, gemini_weekly_remaining=0.85), "p2", None

    monkeypatch.setattr(cli, "_fetch_account_quota", mock_fetch)

    code = main(["switch", "--smart", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["success"] is True
    assert data["from_account"] == "acc1"
    assert data["to_account"] == "acc2"
    assert data["model"] == "gemini"
    assert data["quota"] == {"5h_remaining": 0.9, "weekly_remaining": 0.85}
    assert "cooldown_until" in data


def test_cmd_switch_smart_unsupported_quota_tool(capsys):
    from promux.cli import main

    code = main(["claude", "switch", "--smart"])
    assert code == 1
    captured = capsys.readouterr()
    assert (
        "Tool 'claude' does not support smart quota switching ('quota' capability required)."
        in captured.err
    )


def test_cmd_switch_smart_no_candidates(monkeypatch, tmp_path, capsys):
    from promux import cli
    from promux.cli import main
    from promux.models import AccountMeta, QuotaSummary
    from promux.storage import StorageEngine

    promux_home = tmp_path / "promux"
    gemini_home = tmp_path / "gemini"
    promux_home.mkdir()
    gemini_home.mkdir()
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = StorageEngine(promux_home=promux_home / "tools" / "agy", gemini_home=gemini_home)
    (storage.accounts_dir / "acc1").mkdir(parents=True)
    (storage.accounts_dir / "acc1" / "antigravity-oauth-token").write_text('{"access_token":"t1"}')
    (storage.accounts_dir / "acc2").mkdir(parents=True)
    (storage.accounts_dir / "acc2" / "antigravity-oauth-token").write_text('{"access_token":"t2"}')
    storage.save_state({
        "active": "acc1",
        "accounts": {
            "acc1": AccountMeta(name="acc1", enabled=True).to_dict(),
            "acc2": AccountMeta(name="acc2", enabled=True).to_dict(),
        },
    })
    storage.live_token.write_text('{"access_token":"t1"}')

    def mock_fetch(st, name):
        return QuotaSummary(gemini_5h_remaining=0.0, gemini_weekly_remaining=0.0), "p", None

    monkeypatch.setattr(cli, "_fetch_account_quota", mock_fetch)

    code = main(["switch", "--smart"])
    assert code == 1
    captured = capsys.readouterr()
    assert (
        "Error: No standby accounts found with available quota for 'gemini' models"
        in captured.err
    )


def test_cmd_switch_smart_model_claude(monkeypatch, tmp_path, capsys):
    from promux import cli
    from promux.cli import main
    from promux.models import AccountMeta, QuotaSummary
    from promux.storage import StorageEngine

    promux_home = tmp_path / "promux"
    gemini_home = tmp_path / "gemini"
    promux_home.mkdir()
    gemini_home.mkdir()
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = StorageEngine(promux_home=promux_home / "tools" / "agy", gemini_home=gemini_home)
    (storage.accounts_dir / "acc1").mkdir(parents=True)
    (storage.accounts_dir / "acc1" / "antigravity-oauth-token").write_text('{"access_token":"t1"}')
    (storage.accounts_dir / "acc2").mkdir(parents=True)
    (storage.accounts_dir / "acc2" / "antigravity-oauth-token").write_text('{"access_token":"t2"}')
    storage.save_state({
        "active": "acc1",
        "accounts": {
            "acc1": AccountMeta(name="acc1", enabled=True).to_dict(),
            "acc2": AccountMeta(name="acc2", enabled=True).to_dict(),
        },
    })
    storage.live_token.write_text('{"access_token":"t1"}')

    def mock_fetch(st, name):
        if name == "acc1":
            return (
                QuotaSummary(third_party_5h_remaining=0.0, third_party_weekly_remaining=0.5),
                "p1",
                None,
            )
        return (
            QuotaSummary(third_party_5h_remaining=0.75, third_party_weekly_remaining=0.8),
            "p2",
            None,
        )

    monkeypatch.setattr(cli, "_fetch_account_quota", mock_fetch)

    code = main(["switch", "--smart", "--model", "claude"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Switched active profile to 'acc2'" in captured.out
    assert "claude 5h: 75.0%" in captured.out


def test_cmd_switch_smart_failure_json(monkeypatch, tmp_path, capsys):
    from promux.cli import main
    from promux.models import AccountMeta
    from promux.storage import StorageEngine

    promux_home = tmp_path / "promux"
    gemini_home = tmp_path / "gemini"
    promux_home.mkdir()
    gemini_home.mkdir()
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = StorageEngine(promux_home=promux_home / "tools" / "agy", gemini_home=gemini_home)
    (storage.accounts_dir / "acc1").mkdir(parents=True)
    (storage.accounts_dir / "acc1" / "antigravity-oauth-token").write_text('{"access_token":"t1"}')
    storage.save_state({
        "active": "acc1",
        "accounts": {
            "acc1": AccountMeta(name="acc1", enabled=True).to_dict(),
        },
    })
    storage.live_token.write_text('{"access_token":"t1"}')

    code = main(["switch", "--smart", "--json"])
    assert code == 1
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["success"] is False
    assert "error" in data


def test_cli_token_refresh_thread_local():
    from promux.cli import (
        _get_last_refresh_method,
        _set_last_refresh_method,
        _get_last_refresh_revoked,
        _set_last_refresh_revoked,
    )

    thread_results = {}

    def worker_default():
        thread_results["default_method"] = _get_last_refresh_method()
        thread_results["default_revoked"] = _get_last_refresh_revoked()

    t1 = threading.Thread(target=worker_default)
    t1.start()
    t1.join()

    assert thread_results["default_method"] == "native"
    assert thread_results["default_revoked"] is False

    try:
        # Set state in calling thread
        _set_last_refresh_method("fallback (agy)")
        _set_last_refresh_revoked(True)
        assert _get_last_refresh_method() == "fallback (agy)"
        assert _get_last_refresh_revoked() is True

        def worker_mutate():
            # Worker thread should have independent default values
            thread_results["worker_initial_method"] = _get_last_refresh_method()
            thread_results["worker_initial_revoked"] = _get_last_refresh_revoked()
            _set_last_refresh_method("failed")
            _set_last_refresh_revoked(False)
            thread_results["worker_updated_method"] = _get_last_refresh_method()
            thread_results["worker_updated_revoked"] = _get_last_refresh_revoked()

        t2 = threading.Thread(target=worker_mutate)
        t2.start()
        t2.join()

        assert thread_results["worker_initial_method"] == "native"
        assert thread_results["worker_initial_revoked"] is False
        assert thread_results["worker_updated_method"] == "failed"
        assert thread_results["worker_updated_revoked"] is False

        # Calling thread should be untouched by worker mutations
        assert _get_last_refresh_method() == "fallback (agy)"
        assert _get_last_refresh_revoked() is True
    finally:
        _set_last_refresh_method("native")
        _set_last_refresh_revoked(False)


def test_quota_cache_fetch_account_quota_integration(tmp_path, sample_token_dict, monkeypatch):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import _fetch_account_quota, get_storage, main
    from promux.quota import QuotaClient
    from promux.cache import QuotaCache

    main(["save", "acc1", "--email", "1@test.com"])
    storage = get_storage()
    cache = QuotaCache(storage.home)

    calls = {"count": 0}
    mock_qs = QuotaSummary(
        gemini_5h_remaining=0.88,
        gemini_weekly_remaining=0.99,
        third_party_5h_remaining=0.77,
        third_party_weekly_remaining=0.66,
    )

    def mock_get_quota(self, project_id):
        calls["count"] += 1
        return mock_qs

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "proj-1"})
    monkeypatch.setattr(QuotaClient, "get_quota", mock_get_quota)

    # 1. First run: cold cache -> live fetch
    qs1, pid1, err1, cached1 = _fetch_account_quota(storage, "acc1", cache=cache)
    assert err1 is None
    assert pid1 == "proj-1"
    assert qs1 is not None
    assert qs1.gemini_5h_remaining == 0.88
    assert cached1 is False
    assert calls["count"] == 1

    # Verify persistent cache populated
    assert cache.get("acc1") is not None

    # 2. Second run: warm cache -> hit without network query
    qs2, pid2, err2, cached2 = _fetch_account_quota(storage, "acc1", cache=cache)
    assert err2 is None
    assert pid2 == "proj-1"
    assert qs2 is not None
    assert qs2.gemini_5h_remaining == 0.88
    assert cached2 is True
    assert calls["count"] == 1  # No additional network query

    # 3. Third run: no_cache=True -> forces live query
    qs3, pid3, err3, cached3 = _fetch_account_quota(storage, "acc1", no_cache=True, cache=cache)
    assert err3 is None
    assert pid3 == "proj-1"
    assert qs3 is not None
    assert cached3 is False
    assert calls["count"] == 2  # Queried again!


def test_quota_cache_cmd_quota_overview_and_detail_json(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import cmd_quota, get_storage, main
    from promux.quota import QuotaClient

    main(["save", "acc1", "--email", "1@test.com"])
    token2 = dict(sample_token_dict)
    token2["token"]["access_token"] = "acc2_token"
    (tmp_path / ".gemini" / "antigravity-oauth-token").write_text(json.dumps(token2))
    main(["save", "acc2", "--email", "2@test.com"])
    capsys.readouterr()

    storage = get_storage()
    calls = {"count": 0}
    mock_qs = QuotaSummary(
        gemini_5h_remaining=0.91,
        gemini_weekly_remaining=0.82,
        third_party_5h_remaining=0.73,
        third_party_weekly_remaining=0.64,
    )

    def mock_get_quota(self, project_id):
        calls["count"] += 1
        return mock_qs

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "proj-common"})
    monkeypatch.setattr(QuotaClient, "get_quota", mock_get_quota)

    # 1. Multi-profile overview cold run -> cached=False
    rc = cmd_quota(storage, name=None, json_out=True)
    assert rc == 0
    records = json.loads(capsys.readouterr().out)
    assert len(records) == 2
    assert records[0]["cached"] is False
    assert records[1]["cached"] is False
    assert calls["count"] == 2

    # 2. Multi-profile overview warm run -> cached=True, no new network calls
    rc = cmd_quota(storage, name=None, json_out=True)
    assert rc == 0
    records2 = json.loads(capsys.readouterr().out)
    assert len(records2) == 2
    assert records2[0]["cached"] is True
    assert records2[1]["cached"] is True
    assert calls["count"] == 2

    # 3. Multi-profile overview with no_cache=True -> cached=False, calls refreshed
    rc = cmd_quota(storage, name=None, json_out=True, no_cache=True)
    assert rc == 0
    records3 = json.loads(capsys.readouterr().out)
    assert len(records3) == 2
    assert records3[0]["cached"] is False
    assert records3[1]["cached"] is False
    assert calls["count"] == 4

    # 4. Single-profile detail mode warm run -> cached=True
    rc = cmd_quota(storage, name="acc1", json_out=True)
    assert rc == 0
    detail1 = json.loads(capsys.readouterr().out)
    assert detail1["cached"] is True
    assert calls["count"] == 4

    # 5. Single-profile detail mode with no_cache=True -> cached=False
    rc = cmd_quota(storage, name="acc1", json_out=True, no_cache=True)
    assert rc == 0
    detail2 = json.loads(capsys.readouterr().out)
    assert detail2["cached"] is False
    assert calls["count"] == 5


def test_cli_quota_no_cache_flag_dispatch(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main
    from promux.quota import QuotaClient

    main(["save", "acc1", "--email", "1@test.com"])
    capsys.readouterr()

    calls = {"count": 0}
    mock_qs = QuotaSummary(
        gemini_5h_remaining=0.80,
        gemini_weekly_remaining=0.90,
    )

    def mock_get_quota(self, project_id):
        calls["count"] += 1
        return mock_qs

    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "proj-dispatch"})
    monkeypatch.setattr(QuotaClient, "get_quota", mock_get_quota)

    # Cold run via CLI
    assert main(["quota", "--json"]) == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1[0]["cached"] is False
    assert calls["count"] == 1

    # Warm run via CLI: cached=True
    assert main(["quota", "--json"]) == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2[0]["cached"] is True
    assert calls["count"] == 1

    # --no-cache flag via CLI overview: cached=False
    assert main(["quota", "--no-cache", "--json"]) == 0
    out3 = json.loads(capsys.readouterr().out)
    assert out3[0]["cached"] is False
    assert calls["count"] == 2

    # --no-cache flag via CLI single profile: cached=False
    assert main(["quota", "acc1", "--no-cache", "--json"]) == 0
    out4 = json.loads(capsys.readouterr().out)
    assert out4["cached"] is False
    assert calls["count"] == 3


def test_quota_cache_invalidation_on_401(tmp_path, sample_token_dict, monkeypatch):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import _fetch_account_quota, get_storage, main
    from promux.quota import QuotaClient
    from promux.cache import QuotaCache

    main(["save", "acc1", "--email", "1@test.com"])
    storage = get_storage()
    cache = QuotaCache(storage.home)

    mock_qs = QuotaSummary(gemini_5h_remaining=0.88)
    monkeypatch.setattr(QuotaClient, "load_metadata", lambda self: {"project_id": "proj-401-inv"})
    monkeypatch.setattr(QuotaClient, "get_quota", lambda self, pid: mock_qs)

    # Initial success: cache gets populated
    qs, pid, err, cached = _fetch_account_quota(storage, "acc1", cache=cache)
    assert err is None
    assert cache.get("acc1") is not None

    # Next attempt triggers 401 with failed refresh
    monkeypatch.setattr(
        QuotaClient,
        "get_quota",
        lambda self, pid: (_ for _ in ()).throw(Exception("HTTP Error 401: Unauthorized")),
    )
    monkeypatch.setattr("promux.cli._refresh_token_file", lambda p, td, storage=None: None)

    qs_err, pid_err, err_msg, cached_err = _fetch_account_quota(storage, "acc1", no_cache=True, cache=cache)
    assert err_msg is not None
    assert "401" in err_msg
    # Cache must now be invalidated!
    assert cache.get("acc1") is None


def test_cmd_switch_smart_no_cache_flag(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main, get_storage
    from promux.models import QuotaSummary

    main(["save", "acc1", "--email", "1@test.com"])
    main(["save", "acc2", "--email", "2@test.com"])
    main(["switch", "acc1"])

    calls: list[dict[str, Any]] = []

    def mock_fetch(st, acct, no_cache=False, cache=None):
        calls.append({"acct": acct, "no_cache": no_cache, "cache": cache})
        if acct == "acc2":
            return QuotaSummary(gemini_5h_remaining=0.9, gemini_weekly_remaining=0.9), "p2", None, False
        return QuotaSummary(gemini_5h_remaining=0.1, gemini_weekly_remaining=0.1), "p1", None, False

    monkeypatch.setattr("promux.cli._fetch_account_quota", mock_fetch)

    # 1. Without --no-cache
    ret = main(["switch", "--smart"])
    assert ret == 0
    assert any(c["acct"] == "acc2" and c["no_cache"] is False for c in calls)
    capsys.readouterr()

    # Reset calls and switch back to acc1
    calls.clear()
    main(["switch", "acc1"])
    capsys.readouterr()

    # 2. With --no-cache
    ret2 = main(["switch", "--smart", "--no-cache"])
    assert ret2 == 0
    assert any(c["acct"] == "acc2" and c["no_cache"] is True for c in calls)


