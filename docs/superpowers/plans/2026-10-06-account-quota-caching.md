# Account Quota Caching & Bypass Flag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement account quota caching with configurable TTL (`~/.promux/config.json`) and a `--no-cache` CLI bypass flag for `promux quota` and `promux switch --smart`.

**Architecture:** Create a dedicated `QuotaCache` subsystem in `src/promux/cache.py` managing configuration loading and thread-safe atomic cache persistence at `~/.promux/cache/quota.json`. Integrate `QuotaCache` into `_fetch_account_quota()` in `src/promux/cli.py`, add `--no-cache` to CLI argument parsers, update shell completions and documentation, and bump the version to 0.4.0.

**Tech Stack:** Python 3.10+ standard library (`pathlib`, `json`, `datetime`, `threading`, `os`, `argparse`), pytest.

## Global Constraints

- Zero new external runtime dependencies (strictly Python standard library).
- Default TTL: 300 seconds (5 minutes).
- Precedence: `PROMUX_QUOTA_CACHE_TTL` env var > `~/.promux/config.json` > 300 seconds default.
- Cache file path: `~/.promux/cache/quota.json` with permissions `0600`.
- Corrupt or missing config/cache files must fail gracefully to defaults without crashing the CLI.
- All existing 210 tests must continue to pass without regression.

---

### Task 1: Constants & `QuotaCache` Subsystem

**Files:**
- Modify: `src/promux/constants.py`
- Create: `src/promux/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: `PROMUX_HOME`, `QuotaSummary`
- Produces: `QuotaCache` class with methods `get_ttl_seconds()`, `get(account_name)`, `set(account_name, quota)`, `invalidate(account_name=None)`

- [ ] **Step 1: Write the failing unit tests in `tests/test_cache.py`**

```python
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
    cache_file.write_text(json.dumps({
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
                }
            }
        }
    }))

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py -v`  
Expected: FAIL with ModuleNotFoundError: No module named 'promux.cache'

- [ ] **Step 3: Define constants in `src/promux/constants.py`**

Add constants to `src/promux/constants.py`:
```python
DEFAULT_QUOTA_CACHE_TTL_SECONDS = 300
CONFIG_FILE = PROMUX_HOME / "config.json"
CACHE_DIR = PROMUX_HOME / "cache"
QUOTA_CACHE_FILE = CACHE_DIR / "quota.json"
```

- [ ] **Step 4: Implement `src/promux/cache.py`**

```python
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
```

- [ ] **Step 5: Run tests and verify they pass**

Run: `pytest tests/test_cache.py -v`  
Expected: PASS (8 passed)

- [ ] **Step 6: Commit**

```bash
git add src/promux/constants.py src/promux/cache.py tests/test_cache.py
git commit -m "feat(cache): implement QuotaCache subsystem with configurable TTL"
```

---

### Task 2: Integrate `QuotaCache` into `_fetch_account_quota` and `promux quota`

**Files:**
- Modify: `src/promux/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `QuotaCache`
- Produces: `_fetch_account_quota` with `no_cache` flag and cache checking; `cmd_quota` with `--no-cache` and `"cached"` field in JSON

- [ ] **Step 1: Write integration tests in `tests/test_cli.py`**

Add tests to `tests/test_cli.py`:
```python
def test_quota_cache_integration(tmp_path, monkeypatch, capsys):
    from promux.cli import cmd_quota
    from promux.storage import StorageEngine
    from promux.models import AccountMeta, QuotaSummary

    promux_home = tmp_path / ".promux"
    storage = StorageEngine(promux_home=promux_home, gemini_home=tmp_path / ".gemini")
    storage.save_state({
        "active": "acc1",
        "accounts": {"acc1": AccountMeta(name="acc1", project_id="proj-1").to_dict()}
    })

    call_count = 0
    mock_qs = QuotaSummary(gemini_5h_remaining=0.88, gemini_weekly_remaining=0.99)

    def mock_fetch(st, target, no_cache=False, cache=None):
        nonlocal call_count
        call_count += 1
        return mock_qs, "proj-1", None, False

    # First run without pre-populated cache -> live fetch
    # Second run with pre-populated cache -> should be cached
    # We test actual _fetch_account_quota with QuotaCache
```

- [ ] **Step 2: Update `_fetch_account_quota` signature and logic in `src/promux/cli.py`**

In `src/promux/cli.py`:
- Update `_fetch_account_quota`:
```python
def _fetch_account_quota(
    storage: StorageEngine,
    target_name: str,
    no_cache: bool = False,
    cache: QuotaCache | None = None,
) -> tuple[QuotaSummary | None, str | None, str | None, bool]:
```
- At the top of `_fetch_account_quota`:
```python
    if cache is None:
        cache = QuotaCache(storage.home)

    acct = storage.load_profile(target_name)
    if not acct:
        return None, None, f"Account '{target_name}' not found.", False

    if not no_cache:
        cached_qs = cache.get(target_name)
        if cached_qs is not None:
            return cached_qs, acct.project_id, None, True
```
- On live success:
```python
    cache.set(target_name, qs)
    return qs, project_id, None, False
```
- On 401 / revocation:
```python
    cache.invalidate(target_name)
```

- [ ] **Step 3: Update `cmd_quota` and CLI argument parser in `src/promux/cli.py`**

In `cmd_quota(storage: StorageEngine, name: str | None, json_out: bool, no_cache: bool = False)`:
- Pass `no_cache=no_cache, cache=cache` into `_fetch_account_quota`.
- In `fetch_for_account`: return `(acct_name, is_active, qs, project_id, err, is_cached)`.
- In JSON records: add `"cached": is_cached`.
- In `build_parser()`: under `quota` parser, add `--no-cache`:
```python
quota_parser.add_argument(
    "--no-cache",
    action="store_true",
    default=False,
    help="Bypass quota cache and query Cloud Code Assist API directly",
)
```

- [ ] **Step 4: Run CLI tests**

Run: `pytest tests/test_cli.py -k "quota" -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/promux/cli.py tests/test_cli.py
git commit -m "feat(cli): integrate quota caching and --no-cache into promux quota"
```

---

### Task 3: Integrate `--no-cache` into `promux switch --smart`

**Files:**
- Modify: `src/promux/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `QuotaCache`, `_fetch_account_quota(..., no_cache=...)`
- Produces: `cmd_switch` accepting `--no-cache` and evaluating candidate quotas with `no_cache` flag

- [ ] **Step 1: Write test for `switch --smart --no-cache` in `tests/test_cli.py`**

```python
def test_cmd_switch_smart_no_cache_flag(monkeypatch, tmp_path):
    # Verify that passing --no-cache passes no_cache=True down to quota fetcher
```

- [ ] **Step 2: Update `switch` subcommand parser in `src/promux/cli.py`**

In `build_parser()` under `switch_parser`:
```python
switch_parser.add_argument(
    "--no-cache",
    action="store_true",
    default=False,
    help="Bypass quota cache when evaluating candidates for smart switching",
)
```

- [ ] **Step 3: Forward `no_cache` flag in `cmd_switch`**

In `cmd_switch()`:
```python
    res = engine.rotate_smart(
        quota_fetcher=lambda acct: _fetch_account_quota(storage, acct, no_cache=getattr(args, "no_cache", False))[:3],
        model=args.model,
    )
```

- [ ] **Step 4: Run failover and switch tests**

Run: `pytest tests/test_cli.py -k "switch" -v && pytest tests/test_failover.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/promux/cli.py tests/test_cli.py
git commit -m "feat(switch): support --no-cache flag in smart quota switching"
```

---

### Task 4: Documentation, Shell Completion, and Version Bump to 0.4.0

**Files:**
- Modify: `src/promux/help.py`
- Modify: `src/promux/completion.py`
- Modify: `pyproject.toml`
- Modify: `src/promux/__init__.py`
- Modify: `CHANGELOG.md`
- Modify: `SPEC.md`
- Test: `tests/test_help.py`, `tests/test_completion.py`, `tests/test_version.py`

**Interfaces:**
- Consumes: CLI specifications from Tasks 1-3
- Produces: Updated help text, bash/zsh completions with `--no-cache`, version `0.4.0`

- [ ] **Step 1: Update `src/promux/help.py`**

Add `--no-cache` flag to command descriptions under `quota` and `switch`.

- [ ] **Step 2: Update `src/promux/completion.py`**

Add `'--no-cache[Bypass quota cache and query API directly]'` to zsh completions and `--no-cache` to bash completions for `quota` and `switch`.

- [ ] **Step 3: Bump package version to `0.4.0`**

Update `version = "0.4.0"` in:
- `pyproject.toml`
- `src/promux/__init__.py`
- `CHANGELOG.md`
- `SPEC.md`

- [ ] **Step 4: Run full verification suite**

Run: `pytest -v && uv run mypy src tests`  
Expected: PASS (all tests pass, 0 type issues)

- [ ] **Step 5: Commit**

```bash
git add src/promux/help.py src/promux/completion.py pyproject.toml src/promux/__init__.py CHANGELOG.md SPEC.md
git commit -m "chore(release): bump version to 0.4.0 with quota caching and --no-cache"
```
