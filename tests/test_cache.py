import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from promux.cache import QuotaCache
from promux.models import QuotaSummary


def test_cache_default_ttl(tmp_path: Path):
    cache = QuotaCache(promux_home=tmp_path)
    assert cache.get_ttl_seconds() == 300


def test_cache_config_file_ttl(tmp_path: Path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"quota_cache_ttl_seconds": 600}))
    cache = QuotaCache(promux_home=tmp_path)
    assert cache.get_ttl_seconds() == 600


def test_cache_env_var_ttl_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"quota_cache_ttl_seconds": 600}))
    monkeypatch.setenv("PROMUX_QUOTA_CACHE_TTL", "120")
    cache = QuotaCache(promux_home=tmp_path)
    assert cache.get_ttl_seconds() == 120


def test_cache_malformed_config_fallback(tmp_path: Path):
    config_file = tmp_path / "config.json"
    config_file.write_text("invalid json content")
    cache = QuotaCache(promux_home=tmp_path)
    assert cache.get_ttl_seconds() == 300


def test_cache_set_and_get_hit(tmp_path: Path):
    cache = QuotaCache(promux_home=tmp_path)
    qs = QuotaSummary(gemini_5h_remaining=0.75, gemini_weekly_remaining=0.90)
    cache.set("acct1", qs)

    cached_qs = cache.get("acct1")
    assert cached_qs is not None
    assert cached_qs.gemini_5h_remaining == 0.75
    assert cached_qs.gemini_weekly_remaining == 0.90


def test_cache_expired_miss(tmp_path: Path):
    cache = QuotaCache(promux_home=tmp_path)
    cache_file = tmp_path / "cache" / "quota.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=350)).isoformat()
    cache_file.write_text(
        json.dumps({
            "accounts": {
                "acct1": {
                    "cached_at": stale_time,
                    "quota": {
                        "gemini_5h_remaining": 0.5,
                        "gemini_weekly_remaining": 0.5,
                        "third_party_5h_remaining": 1.0,
                        "third_party_weekly_remaining": 1.0,
                        "gemini_5h_reset": None,
                        "gemini_weekly_reset": None,
                        "third_party_5h_reset": None,
                        "third_party_weekly_reset": None,
                    },
                }
            }
        })
    )

    assert cache.get("acct1") is None


def test_cache_invalidate(tmp_path: Path):
    cache = QuotaCache(promux_home=tmp_path)
    qs = QuotaSummary(gemini_5h_remaining=0.8)
    cache.set("acct1", qs)
    cache.set("acct2", qs)

    cache.invalidate("acct1")
    assert cache.get("acct1") is None
    assert cache.get("acct2") is not None

    cache.invalidate()
    assert cache.get("acct2") is None


def test_cache_thread_safety(tmp_path: Path):
    cache = QuotaCache(promux_home=tmp_path)
    threads = []
    for i in range(10):
        qs = QuotaSummary(gemini_5h_remaining=float(i) / 10.0)
        t = threading.Thread(target=cache.set, args=(f"acct_{i}", qs))
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(10):
        entry = cache.get(f"acct_{i}")
        assert entry is not None


def test_cache_zero_ttl_bypasses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PROMUX_QUOTA_CACHE_TTL", "0")
    cache = QuotaCache(promux_home=tmp_path)
    assert cache.get_ttl_seconds() == 0

    qs = QuotaSummary(gemini_5h_remaining=0.9)
    cache.set("acct1", qs)
    assert cache.get("acct1") is None


def test_cache_negative_ttl_env_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PROMUX_QUOTA_CACHE_TTL", "-1")
    cache = QuotaCache(promux_home=tmp_path)
    assert cache.get_ttl_seconds() == 300


def test_cache_file_permissions(tmp_path: Path):
    cache = QuotaCache(promux_home=tmp_path)
    qs = QuotaSummary(gemini_5h_remaining=0.5)
    cache.set("acct1", qs)
    cache_file = tmp_path / "cache" / "quota.json"
    assert cache_file.exists()
    assert cache_file.stat().st_mode & 0o777 == 0o600


def test_cache_corrupt_cache_file_handled(tmp_path: Path):
    cache = QuotaCache(promux_home=tmp_path)
    cache_file = tmp_path / "cache" / "quota.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("corrupted json")

    assert cache.get("acct1") is None
    # set should overwrite corrupted file cleanly
    qs = QuotaSummary(gemini_5h_remaining=0.5)
    cache.set("acct1", qs)
    assert cache.get("acct1") is not None

