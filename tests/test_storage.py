import json
import os
from datetime import datetime, timezone
from pathlib import Path
import pytest

from promux.storage import StorageEngine
from promux.models import AccountMeta, AccountState


def test_save_and_switch_profile(tmp_path, sample_token_dict):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    live_token = gemini_home / "antigravity-oauth-token"
    live_token.write_text(json.dumps(sample_token_dict))
    live_token.chmod(0o600)

    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)

    # Save work profile
    acct = storage.save_profile("work", email="work@company.com", project_id="aicode-work")
    assert acct.name == "work"
    assert acct.email == "work@company.com"
    assert storage.get_active_profile() == "work"

    # Verify vault copy has 0600 permissions
    vault_token = promux_home / "accounts" / "work" / "antigravity-oauth-token"
    assert vault_token.exists()
    assert oct(vault_token.stat().st_mode & 0o777) == "0o600"

    # Verify state.json exists and has 0600 permissions
    assert storage.state_file.exists()
    assert oct(storage.state_file.stat().st_mode & 0o777) == "0o600"

    # Save personal profile with different token
    personal_token_data = dict(sample_token_dict)
    personal_token_data["token"] = dict(sample_token_dict["token"])
    personal_token_data["token"]["access_token"] = "personal_token"
    live_token.write_text(json.dumps(personal_token_data))
    storage.save_profile("personal", email="personal@home.org")

    # Switch back to work
    assert storage.switch_profile("work") is True
    assert storage.get_active_profile() == "work"
    active_data = json.loads(live_token.read_text())
    assert active_data["token"]["access_token"] == "ya29.mock_access_token"
    assert oct(live_token.stat().st_mode & 0o777) == "0o600"


def test_remove_profile(tmp_path, sample_token_dict):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    live_token = gemini_home / "antigravity-oauth-token"
    live_token.write_text(json.dumps(sample_token_dict))

    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    storage.save_profile("temp")
    assert storage.get_account("temp") is not None
    assert storage.get_active_profile() == "temp"

    assert storage.remove_profile("temp") is True
    assert storage.get_account("temp") is None
    assert not (promux_home / "accounts" / "temp").exists()
    assert storage.get_active_profile() is None


def test_remove_nonexistent_profile(tmp_path):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    assert storage.remove_profile("nonexistent") is False


def test_switch_nonexistent_profile(tmp_path):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    assert storage.switch_profile("nonexistent") is False


def test_switch_profile_missing_vault_token(tmp_path, sample_token_dict):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    live_token = gemini_home / "antigravity-oauth-token"
    live_token.write_text(json.dumps(sample_token_dict))

    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    storage.save_profile("missing_vault")

    # Manually delete the vaulted token
    vault_token = promux_home / "accounts" / "missing_vault" / "antigravity-oauth-token"
    vault_token.unlink()

    assert storage.switch_profile("missing_vault") is False


def test_save_profile_missing_live_token(tmp_path):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    with pytest.raises(FileNotFoundError, match="Active token file not found"):
        storage.save_profile("work")


def test_conversations_and_history_preserved_across_switches(tmp_path, sample_token_dict):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    live_token = gemini_home / "antigravity-oauth-token"
    live_token.write_text(json.dumps(sample_token_dict))

    # Mock existing chat files and directories including brain/
    conv_dir = gemini_home / "conversations"
    conv_dir.mkdir()
    conv_file = conv_dir / "chat-1234.json"
    conv_file.write_text('{"id": "chat-1234", "messages": []}')

    brain_dir = gemini_home / "brain" / "sub-session"
    brain_dir.mkdir(parents=True)
    brain_file = brain_dir / "memory.txt"
    brain_file.write_text("brain session contents")

    db_file = gemini_home / "conversation_summaries.db"
    db_file.write_text("sqlite dummy data")
    history_file = gemini_home / "history.jsonl"
    history_file.write_text('{"display": "hello"}\n')

    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    storage.save_profile("acc1")
    storage.save_profile("acc2")
    storage.switch_profile("acc2")
    storage.switch_profile("acc1")

    # Assert conversation and session files are untouched
    assert conv_file.exists()
    assert conv_file.read_text() == '{"id": "chat-1234", "messages": []}'
    assert brain_file.exists()
    assert brain_file.read_text() == "brain session contents"
    assert db_file.exists()
    assert db_file.read_text() == "sqlite dummy data"
    assert history_file.exists()
    assert history_file.read_text() == '{"display": "hello"}\n'


def test_list_accounts_and_load_corrupted_state(tmp_path, sample_token_dict):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    live_token = gemini_home / "antigravity-oauth-token"
    live_token.write_text(json.dumps(sample_token_dict))

    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    storage.save_profile("alpha", email="alpha@test.com")
    storage.save_profile("beta", email="beta@test.com")

    accounts = storage.list_accounts()
    assert len(accounts) == 2
    names = {a.name for a in accounts}
    assert names == {"alpha", "beta"}

    account = storage.get_account("alpha")
    assert account is not None
    assert account.email == "alpha@test.com"

    # Corrupt state.json and verify graceful fallback
    storage.state_file.write_text("{corrupted-json")
    state = storage.load_state()
    assert state == {"active": None, "accounts": {}}


def test_save_state_under_lock(tmp_path):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)

    custom_state = {"active": "foo", "accounts": {}}
    storage.save_state(custom_state)

    loaded = storage.load_state()
    assert loaded == custom_state
    assert oct(storage.state_file.stat().st_mode & 0o777) == "0o600"
