import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from promux_win.core import (
    AccountMeta,
    AccountState,
    OAuthFlowManager,
    OAuthRefresher,
    WindowsStorageEngine,
    SmartSwitchEngine,
    format_relative_countdown,
)


def test_account_meta_state():
    now = datetime.now(timezone.utc)
    acct = AccountMeta(name="work", enabled=True)
    assert acct.get_state(is_active=True) == AccountState.ACTIVE
    assert acct.get_state(is_active=False) == AccountState.STANDBY

    # Cooldown
    acct.cooldown_until = now + timedelta(minutes=30)
    assert acct.is_cooldown is True
    assert acct.get_state(is_active=False) == AccountState.COOLDOWN

    # Disabled
    acct.enabled = False
    assert acct.get_state(is_active=False) == AccountState.DISABLED


def test_format_relative_countdown():
    now = datetime.now(timezone.utc)
    in_two_hours = (now + timedelta(hours=2, minutes=15)).isoformat()
    res = format_relative_countdown(in_two_hours)
    assert "2h" in res or "1h" in res

    in_past = (now - timedelta(minutes=5)).isoformat()
    assert format_relative_countdown(in_past) == "now"
    assert format_relative_countdown(None) == ""


def test_oauth_flow_manager_url():
    url = OAuthFlowManager.build_auth_url("http://127.0.0.1:18420/oauth/callback", "csrf123")
    assert "https://accounts.google.com/o/oauth2/auth" in url
    assert "client_id=" in url
    assert "state=csrf123" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A18420%2Foauth%2Fcallback" in url


def test_oauth_flow_manager_token_dict():
    raw_res = {
        "access_token": "ya29.test_token",
        "expires_in": 3600,
        "refresh_token": "test_refresh",
        "id_token": "header.payload.signature",
    }
    tok_dict = OAuthFlowManager.build_token_dict(raw_res)
    assert tok_dict["token"]["access_token"] == "ya29.test_token"
    assert tok_dict["token"]["refresh_token"] == "test_refresh"
    assert tok_dict["auth_method"] == "oauth"
    assert tok_dict["id_token"] == "header.payload.signature"


def test_windows_storage_engine(tmp_path):
    promux_home = tmp_path / ".promux"
    engine = WindowsStorageEngine(promux_home=promux_home)

    token_data = {
        "token": {
            "access_token": "ya29.fake",
            "refresh_token": "fake_refresh",
            "expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        },
        "auth_method": "oauth",
    }

    acct = engine.save_profile("test_profile", token_data=token_data, email="test@gmail.com")
    assert acct.name == "test_profile"
    assert acct.email == "test@gmail.com"

    accounts = engine.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].name == "test_profile"

    # Cooldown
    engine.apply_cooldown("test_profile", minutes=10)
    accts = engine.list_accounts()
    assert accts[0].is_cooldown is True

    engine.clear_cooldown("test_profile")
    accts = engine.list_accounts()
    assert accts[0].is_cooldown is False

    # Remove
    assert engine.remove_profile("test_profile") is True
    assert len(engine.list_accounts()) == 0


def test_account_alias_and_rename(tmp_path):
    promux_home = tmp_path / ".promux"
    engine = WindowsStorageEngine(promux_home=promux_home)

    token_data = {
        "token": {
            "access_token": "ya29.fake2",
            "refresh_token": "fake_refresh2",
            "expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        },
        "auth_method": "oauth",
    }

    # Save with alias
    acct = engine.save_profile("work_id", token_data=token_data, email="work@company.com", alias="Work Laptop")
    assert acct.alias == "Work Laptop"

    # Update alias
    assert engine.set_account_alias("work_id", "Office Primary") is True
    accts = engine.list_accounts()
    assert accts[0].alias == "Office Primary"

    # Rename account
    ok, msg = engine.rename_account("work_id", "office_id", alias="Office Desktop")
    assert ok is True
    accts = engine.list_accounts()
    assert len(accts) == 1
    assert accts[0].name == "office_id"
    assert accts[0].alias == "Office Desktop"


def test_antigravity_manager():
    from promux_win.core import AntigravityManager
    # Should run without throwing errors
    is_run = AntigravityManager.is_running()
    assert isinstance(is_run, bool)
    exe = AntigravityManager.find_antigravity_executable()
    if exe is not None:
        assert exe.exists()

