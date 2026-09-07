import json

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

    assert main(["save", "todelete"]) == 0
    capsys.readouterr()

    rc = main(["remove", "todelete"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "todelete" in out

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

    # JSON mode
    rc = main(["quota", "demo", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["account"] == "demo"
    assert res["gemini"]["5h_remaining"] == 0.85
    assert res["third_party"]["5h_remaining"] == 0.25


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
    # No arguments
    rc = main([])
    assert rc == 2

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
    qs, pid, err = _fetch_account_quota(storage, "testacc")
    assert err is None
    assert pid == "proj-123"
    assert qs is not None
    assert qs.gemini_5h_remaining == 0.95


def test_fetch_account_quota_nonexistent(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from promux.cli import _fetch_account_quota, get_storage

    storage = get_storage()
    qs, pid, err = _fetch_account_quota(storage, "nonexistent")
    assert qs is None
    assert err is not None
    assert "not found" in err.lower()


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
    qs, pid, err = _fetch_account_quota(storage, "testacc")
    assert err is None
    assert pid == "proj-401"
    assert qs is not None
    assert qs.gemini_5h_remaining == 0.85
    assert calls["count"] == 2


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
    qs, pid, err = _fetch_account_quota(storage, "testacc")
    assert qs is None
    assert pid == "proj-401"
    assert err is not None
    assert "401" in err


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

    assert "apps.googleusercontent.com" in promux.constants.OAUTH_CLIENT_ID
    assert promux.constants.OAUTH_CLIENT_SECRET.startswith("GOCSPX-")
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

    # Mock native refresh to simulate Google invalid_grant
    import urllib.error

    def mock_refresh_revoked(p, td):
        # Trigger revoked state
        raise urllib.error.HTTPError(
            url="https://oauth2.googleapis.com/token",
            code=400,
            msg="Bad Request",
            hdrs={},  # type: ignore[arg-type]
            fp=None,  # type: ignore[arg-type]
        )

    monkeypatch.setattr("promux.cli._last_refresh_revoked", True)

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
