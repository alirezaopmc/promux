"""Unit tests for promux.auth module."""

from __future__ import annotations

import io
import json
import os
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from promux import auth

# --- Task 1.1: Token reading and extraction ---


def test_read_token_data_missing_file(tmp_path: Path):
    assert auth.read_token_data(tmp_path / "missing.json") is None


def test_read_token_data_valid(tmp_path: Path):
    p = tmp_path / "token.json"
    p.write_text(json.dumps({"token": {"access_token": "abc"}}))
    data = auth.read_token_data(p)
    assert data == {"token": {"access_token": "abc"}}


def test_read_token_data_invalid_json(tmp_path: Path):
    p = tmp_path / "token.json"
    p.write_text("not-json{{{")
    assert auth.read_token_data(p) is None


def test_read_token_data_non_dict(tmp_path: Path):
    p = tmp_path / "token.json"
    p.write_text(json.dumps([1, 2, 3]))
    assert auth.read_token_data(p) is None


def test_extract_access_token_nested():
    data = {"token": {"access_token": "tok_nested"}}
    assert auth.extract_access_token(data) == "tok_nested"


def test_extract_access_token_flat():
    data = {"access_token": "tok_flat"}
    assert auth.extract_access_token(data) == "tok_flat"


def test_extract_access_token_none():
    assert auth.extract_access_token(None) is None
    assert auth.extract_access_token({}) is None
    assert auth.extract_access_token({"token": "not-a-dict"}) is None


def test_extract_expiry_nested():
    data = {"token": {"expiry": "2030-01-01T00:00:00Z"}}
    assert auth.extract_expiry(data) == "2030-01-01T00:00:00Z"


def test_extract_expiry_flat():
    data = {"expiry": "2030-01-01T00:00:00Z"}
    assert auth.extract_expiry(data) == "2030-01-01T00:00:00Z"


def test_extract_expiry_none():
    assert auth.extract_expiry(None) is None
    assert auth.extract_expiry({}) is None
    assert auth.extract_expiry({"token": "not-a-dict"}) is None


def test_extract_refresh_token_nested():
    data = {"token": {"refresh_token": "ref_tok"}}
    assert auth.extract_refresh_token(data) == "ref_tok"


def test_extract_refresh_token_flat():
    data = {"refresh_token": "ref_tok_flat"}
    assert auth.extract_refresh_token(data) == "ref_tok_flat"


def test_extract_refresh_token_none():
    assert auth.extract_refresh_token(None) is None
    assert auth.extract_refresh_token({}) is None
    assert auth.extract_refresh_token({"token": "not-a-dict"}) is None


# --- Task 1.2: Token copy, sync, credentials, expiry ---


def test_atomic_copy_token(tmp_path: Path):
    src = tmp_path / "src_token.json"
    src.write_text('{"token": {"access_token": "abc"}}')
    dst = tmp_path / "dst" / "token.json"
    auth.atomic_copy_token(src, dst)
    assert dst.exists()
    assert json.loads(dst.read_text())["token"]["access_token"] == "abc"
    if os.name != "nt":
        assert oct(dst.stat().st_mode)[-3:] == "600"


def test_sync_active_tokens_no_src(tmp_path: Path):
    auth.sync_active_tokens(tmp_path / "missing.json", tmp_path / "dst.json")
    assert not (tmp_path / "dst.json").exists()


def test_sync_active_tokens_dst_missing(tmp_path: Path):
    token = {"token": {"refresh_token": "shared", "access_token": "val"}}
    src = tmp_path / "src.json"
    src.write_text(json.dumps(token))
    dst = tmp_path / "dst.json"
    auth.sync_active_tokens(src, dst)
    assert dst.exists()
    assert auth.extract_access_token(auth.read_token_data(dst)) == "val"


def test_sync_active_tokens_same_refresh_token(tmp_path: Path):
    token = {"token": {"refresh_token": "shared", "access_token": "old"}}
    src = tmp_path / "src.json"
    src.write_text(json.dumps(token))
    dst = tmp_path / "dst.json"
    dst.write_text(json.dumps({**token, "token": {**token["token"], "access_token": "old"}}))
    auth.sync_active_tokens(src, dst)
    assert auth.extract_access_token(auth.read_token_data(dst)) == "old"


def test_sync_active_tokens_different_refresh_skips(tmp_path: Path):
    src_token = {"token": {"refresh_token": "rA", "access_token": "new"}}
    dst_token = {"token": {"refresh_token": "rB", "access_token": "old"}}
    src = tmp_path / "src.json"
    src.write_text(json.dumps(src_token))
    dst = tmp_path / "dst.json"
    dst.write_text(json.dumps(dst_token))
    auth.sync_active_tokens(src, dst)
    assert auth.extract_access_token(auth.read_token_data(dst)) == "old"


def test_sync_active_tokens_handles_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "src.json"
    src.write_text('{"token": "bad"}')
    dst = tmp_path / "dst.json"

    def faulty_copy(s, d):
        raise OSError("Permission denied")

    monkeypatch.setattr(auth, "atomic_copy_token", faulty_copy)
    # Should not raise exception
    auth.sync_active_tokens(src, dst)


def test_get_oauth_credentials_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PROMUX_OAUTH_CLIENT_ID", "env_cid")
    monkeypatch.setenv("PROMUX_OAUTH_CLIENT_SECRET", "env_sec")
    cid, sec = auth.get_oauth_credentials()
    assert cid == "env_cid"
    assert sec == "env_sec"


def test_get_oauth_credentials_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PROMUX_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("PROMUX_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(auth, "OAUTH_CLIENT_ID", "")
    monkeypatch.setattr(auth, "OAUTH_CLIENT_SECRET", "")
    monkeypatch.setattr("promux.constants.PROMUX_HOME", tmp_path)
    oauth_cfg = tmp_path / "oauth.json"
    oauth_cfg.write_text(json.dumps({"client_id": "file_cid", "client_secret": "file_sec"}))

    cid, sec = auth.get_oauth_credentials()
    assert cid == "file_cid"
    assert sec == "file_sec"


def test_get_oauth_credentials_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PROMUX_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("PROMUX_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(auth, "OAUTH_CLIENT_ID", "")
    monkeypatch.setattr(auth, "OAUTH_CLIENT_SECRET", "")
    monkeypatch.setattr("promux.constants.PROMUX_HOME", tmp_path)

    cid, sec = auth.get_oauth_credentials()
    assert cid == ""
    assert sec == ""


def test_parse_token_expiry_dt_valid():
    result = auth.parse_token_expiry_dt("2030-01-01T00:00:00Z")
    assert result is not None
    assert result.tzinfo is not None
    assert result.year == 2030


def test_parse_token_expiry_dt_without_tz():
    result = auth.parse_token_expiry_dt("2030-01-01T00:00:00")
    assert result is not None
    assert result.tzinfo == timezone.utc


def test_parse_token_expiry_dt_invalid():
    assert auth.parse_token_expiry_dt("not-a-date") is None
    assert auth.parse_token_expiry_dt(None) is None
    assert auth.parse_token_expiry_dt("") is None


def test_is_token_expired_no_data():
    assert auth.is_token_expired(None) is True
    assert auth.is_token_expired({}) is True


def test_is_token_expired_future():
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    data = {"token": {"expiry": future}}
    assert auth.is_token_expired(data) is False


def test_is_token_expired_past():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    data = {"token": {"expiry": past}}
    assert auth.is_token_expired(data) is True


def test_is_token_expired_within_buffer():
    # 30 seconds into the future is within default 60s buffer
    soon = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    data = {"token": {"expiry": soon}}
    assert auth.is_token_expired(data, buffer_seconds=60) is True


# --- Task 1.3: Token refresh orchestration ---


def test_refresh_state_helpers():
    auth._set_last_refresh_method("test_method")
    assert auth._get_last_refresh_method() == "test_method"
    auth._set_last_refresh_revoked(True)
    assert auth._get_last_refresh_revoked() is True
    auth._set_last_refresh_revoked(False)
    assert auth._get_last_refresh_revoked() is False


def test_get_or_refresh_access_token_missing_file(tmp_path: Path):
    result = auth.get_or_refresh_access_token(tmp_path / "no_token.json")
    assert result is None


def test_get_or_refresh_access_token_valid_unexpired(tmp_path: Path):
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    token_data = {"token": {"access_token": "valid_tok", "expiry": future}}
    p = tmp_path / "token.json"
    p.write_text(json.dumps(token_data))
    result = auth.get_or_refresh_access_token(p)
    assert result == "valid_tok"


def test_get_or_refresh_access_token_expired_no_refresh(tmp_path: Path):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    token_data = {"token": {"access_token": "old_tok", "expiry": past}}
    p = tmp_path / "token.json"
    p.write_text(json.dumps(token_data))
    result = auth.get_or_refresh_access_token(p)
    assert result == "old_tok"


def test_refresh_token_native_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    token_data = {
        "token": {
            "access_token": "old_tok",
            "refresh_token": "ref_123",
            "expiry": past,
        }
    }
    p = tmp_path / "token.json"
    p.write_text(json.dumps(token_data))

    monkeypatch.setattr(auth, "get_oauth_credentials", lambda: ("test_id", "test_sec"))

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(
        {
            "access_token": "new_tok_abc",
            "expires_in": 1800,
            "refresh_token": "new_ref_456",
        }
    ).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        new_acc = auth.get_or_refresh_access_token(p)

    assert new_acc == "new_tok_abc"
    saved = json.loads(p.read_text())
    assert saved["token"]["access_token"] == "new_tok_abc"
    assert saved["token"]["refresh_token"] == "new_ref_456"
    assert auth._get_last_refresh_method() == "native"
    assert auth._get_last_refresh_revoked() is False


def test_refresh_token_native_invalid_grant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    token_data = {
        "token": {
            "access_token": "old_tok",
            "refresh_token": "ref_123",
            "expiry": past,
        }
    }
    p = tmp_path / "token.json"
    p.write_text(json.dumps(token_data))

    monkeypatch.setattr(auth, "get_oauth_credentials", lambda: ("test_id", "test_sec"))

    err_body = io.BytesIO(b'{"error": "invalid_grant"}')
    http_error = urllib.error.HTTPError(
        url="https://oauth2.googleapis.com/token",
        code=400,
        msg="Bad Request",
        hdrs={},  # type: ignore
        fp=err_body,
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        result = auth.get_or_refresh_access_token(p)

    # Returns old token on failure
    assert result == "old_tok"
    assert auth._get_last_refresh_method() == "revoked"
    assert auth._get_last_refresh_revoked() is True


def test_refresh_token_fallback_agy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    token_data = {
        "token": {
            "access_token": "old_tok",
            "refresh_token": "ref_123",
            "expiry": past,
        }
    }
    token_file = tmp_path / "account_token.json"
    token_file.write_text(json.dumps(token_data))

    live_token = tmp_path / "live_token.json"
    live_token.write_text(json.dumps(token_data))

    storage = MagicMock()
    storage.lock_file = tmp_path / "lock.file"
    storage.live_token = live_token
    storage._tx_state = None

    # Native fails with network error (not invalid_grant)
    monkeypatch.setattr(auth, "_refresh_token_native", lambda tp, td: None)
    monkeypatch.setattr("shutil.which", lambda cmd: "/bin/agy" if cmd == "agy" else None)

    def fake_subprocess_run(cmd, **kwargs):
        # agy runs and updates live_token
        new_live_data = {
            "token": {
                "access_token": "agy_refreshed_tok",
                "refresh_token": "ref_123",
                "expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            }
        }
        live_token.write_text(json.dumps(new_live_data))
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        result = auth.get_or_refresh_access_token(token_file, storage=storage)

    assert result == "agy_refreshed_tok"
    assert auth._get_last_refresh_method() == "fallback (agy)"
    # token_file should be updated with new token
    assert auth.extract_access_token(auth.read_token_data(token_file)) == "agy_refreshed_tok"
