import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_QUOTA_CACHE_TTL_SECONDS,
    PROMUX_HOME,
)
from .models import QuotaSummary


class QuotaCache:
    """Thread-safe persistent cache for account quota summaries."""

    def __init__(self, promux_home: Path | None = None):
        self.home = Path(promux_home or PROMUX_HOME)
        self.config_file = self.home / "config.json"
        self.cache_dir = self.home / "cache"
        self.cache_file = self.cache_dir / "quota.json"
        self._lock = threading.Lock()

    def get_ttl_seconds(self) -> int:
        """Resolve quota cache TTL from environment, config.json, or default."""
        env_val = os.environ.get("PROMUX_QUOTA_CACHE_TTL")
        if env_val:
            try:
                val = int(env_val)
                if val >= 0:
                    return val
            except ValueError:
                pass

        if self.config_file.exists():
            try:
                with open(self.config_file, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        ttl = data.get("quota_cache_ttl_seconds")
                        if isinstance(ttl, (int, float)) and ttl >= 0:
                            return int(ttl)
            except Exception:
                pass

        return DEFAULT_QUOTA_CACHE_TTL_SECONDS

    def _read_cache(self) -> dict[str, Any]:
        if not self.cache_file.exists():
            return {"accounts": {}}
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) and "accounts" in data else {"accounts": {}}
        except Exception:
            return {"accounts": {}}

    def _write_cache(self, data: dict[str, Any]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp_file = self.cache_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp_file, 0o600)
        os.replace(tmp_file, self.cache_file)

    def get(self, account_name: str) -> QuotaSummary | None:
        """Return cached QuotaSummary if present and not expired, else None."""
        ttl = self.get_ttl_seconds()
        if ttl == 0:
            return None

        with self._lock:
            data = self._read_cache()

        account_entry = data.get("accounts", {}).get(account_name)
        if not account_entry or not isinstance(account_entry, dict):
            return None

        cached_at_str = account_entry.get("cached_at")
        quota_data = account_entry.get("quota")
        if not cached_at_str or not isinstance(quota_data, dict):
            return None

        try:
            cached_at = datetime.fromisoformat(cached_at_str)
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if (now - cached_at).total_seconds() > ttl:
                return None
        except Exception:
            return None

        try:
            return QuotaSummary(
                gemini_5h_remaining=float(quota_data.get("gemini_5h_remaining", 1.0)),
                gemini_weekly_remaining=float(quota_data.get("gemini_weekly_remaining", 1.0)),
                third_party_5h_remaining=float(quota_data.get("third_party_5h_remaining", 1.0)),
                third_party_weekly_remaining=float(quota_data.get("third_party_weekly_remaining", 1.0)),
                gemini_5h_reset=quota_data.get("gemini_5h_reset"),
                gemini_weekly_reset=quota_data.get("gemini_weekly_reset"),
                third_party_5h_reset=quota_data.get("third_party_5h_reset"),
                third_party_weekly_reset=quota_data.get("third_party_weekly_reset"),
            )
        except Exception:
            return None

    def set(self, account_name: str, quota: QuotaSummary) -> None:
        """Save account QuotaSummary with current UTC timestamp."""
        now = datetime.now(timezone.utc).isoformat()
        quota_dict = {
            "gemini_5h_remaining": quota.gemini_5h_remaining,
            "gemini_weekly_remaining": quota.gemini_weekly_remaining,
            "third_party_5h_remaining": quota.third_party_5h_remaining,
            "third_party_weekly_remaining": quota.third_party_weekly_remaining,
            "gemini_5h_reset": quota.gemini_5h_reset,
            "gemini_weekly_reset": quota.gemini_weekly_reset,
            "third_party_5h_reset": quota.third_party_5h_reset,
            "third_party_weekly_reset": quota.third_party_weekly_reset,
        }

        with self._lock:
            data = self._read_cache()
            data.setdefault("accounts", {})[account_name] = {
                "cached_at": now,
                "quota": quota_dict,
            }
            self._write_cache(data)

    def invalidate(self, account_name: str | None = None) -> None:
        """Invalidate cache for specific account or all accounts."""
        with self._lock:
            if account_name is None:
                self._write_cache({"accounts": {}})
            else:
                data = self._read_cache()
                if account_name in data.get("accounts", {}):
                    del data["accounts"][account_name]
                    self._write_cache(data)
