import json
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from promux.cli import main
from promux.models import QuotaSummary, AccountMeta, RotationResult


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
    assert "Gemini" in out
    assert "Claude" in out or "third" in out.lower()
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

    rc = main(["quota"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert "error" in (out + err).lower()


def test_cli_quota_no_active_profile(tmp_path, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch)

    rc = main(["quota"])
    assert rc == 1
    out, err = capsys.readouterr()
    assert "error" in (out + err).lower() or "no active" in (out + err).lower()


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

    from promux.watch import LogWatcher, LogMatch

    def mock_run_forever(self, on_match=None, cooldown_minutes=None, max_iterations=None):
        if on_match:
            on_match(LogMatch(
                pattern="RESOURCE_EXHAUSTED",
                line="RESOURCE_EXHAUSTED (code 429)",
                reset_hint="30m",
                file_path="/tmp/cli.log",
            ))

    monkeypatch.setattr(LogWatcher, "run_forever", mock_run_forever)

    rc = main(["watch"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "Starting log watcher" in out
    assert "RESOURCE_EXHAUSTED" in out
    assert "Reset hint detected: 30m" in out

