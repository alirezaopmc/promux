# Account Quota Caching & Bypass Flag Design Specification

**Date:** 2026-10-06  
**Status:** Approved  
**Target Release:** 0.4.0  

---

## 1. Overview & Motivation

Fetching live quota summaries from Google Cloud Code Assist (`/v1internal:retrieveUserQuotaSummary`) requires network round-trips for every account. In multi-account setups, running `promux quota` or evaluating candidates in `promux switch --smart` incurs latency and places unnecessary request load on Google's internal APIs.

Because API quota buckets (such as 5-hour and weekly windows) change relatively slowly during ordinary coding sessions, caching quota snapshots on a configurable time-to-live (TTL) window (default 5 minutes / 300 seconds) drastically improves CLI response times (sub-millisecond cache hits) while preserving up-to-date awareness.

This specification defines:
1. A dedicated `QuotaCache` subsystem located in `src/promux/cache.py`.
2. A user configuration file at `~/.promux/config.json` with `quota_cache_ttl_seconds`.
3. An on-disk cache store at `~/.promux/cache/quota.json`.
4. The `--no-cache` flag for `promux quota` and `promux switch --smart` to force live fetches.
5. Thread-safe, atomic disk operations with strict permissions (`0600`).

---

## 2. Configuration Specification

### 2.1 Configuration File Path & Schema
* **Location:** `~/.promux/config.json` (resolvable via `PROMUX_HOME / "config.json"`).
* **Format:** JSON.
* **Schema:**
  ```json
  {
    "quota_cache_ttl_seconds": 300
  }
  ```

### 2.2 Resolution Precedence
1. Environment variable `PROMUX_QUOTA_CACHE_TTL` (integer in seconds).
2. Key `"quota_cache_ttl_seconds"` in `~/.promux/config.json`.
3. Default constant `DEFAULT_QUOTA_CACHE_TTL_SECONDS = 300` (5 minutes).

### 2.3 Fault Tolerance
* If `config.json` does not exist, Promux silently uses the default TTL without error.
* If `config.json` contains malformed JSON or invalid types (e.g., negative numbers or strings), Promux logs a diagnostic warning in debug mode and safely falls back to the default 300 seconds without failing commands.

---

## 3. Cache Storage Specification

### 3.1 Storage Location & Permissions
* **File Path:** `~/.promux/cache/quota.json` (inside `PROMUX_HOME / "cache"` directory).
* **Permissions:** Restricted to user-only read/write (`0600`), created atomically via temporary file rename (`os.replace`).
* **Decoupling:** Decoupled from `~/.promux/state.json` and credential tokens so cache updates do not contend with account lock operations.

### 3.2 Schema
```json
{
  "accounts": {
    "personal": {
      "cached_at": "2026-10-06T11:45:00Z",
      "quota": {
        "gemini_5h_remaining": 0.85,
        "gemini_weekly_remaining": 0.95,
        "third_party_5h_remaining": 1.0,
        "third_party_weekly_remaining": 1.0,
        "gemini_5h_reset": "2026-10-06T14:00:00Z",
        "gemini_weekly_reset": "2026-10-13T00:00:00Z",
        "third_party_5h_reset": null,
        "third_party_weekly_reset": null
      }
    }
  }
}
```

* `cached_at`: ISO 8601 UTC timestamp of the live fetch.
* `quota`: Serialized representation of `QuotaSummary`.

---

## 4. Architecture & Module Structure

### 4.1 New Module: `src/promux/cache.py`
Provides the `QuotaCache` class:

```python
class QuotaCache:
    def __init__(self, promux_home: Path | None = None):
        self.home = Path(promux_home or PROMUX_HOME)
        self.config_file = self.home / "config.json"
        self.cache_dir = self.home / "cache"
        self.cache_file = self.cache_dir / "quota.json"
        self._lock = threading.Lock()

    def get_ttl_seconds(self) -> int: ...

    def get(self, account_name: str) -> QuotaSummary | None:
        """Read and validate cache entry against current UTC time and TTL."""

    def set(self, account_name: str, quota: QuotaSummary) -> None:
        """Atomically persist updated QuotaSummary for account."""

    def invalidate(self, account_name: str | None = None) -> None:
        """Remove cache for a specific account or purge entire cache file."""
```

### 4.2 Integration into `_fetch_account_quota()`
In `src/promux/cli.py`:

```python
def _fetch_account_quota(
    storage: StorageEngine,
    target_name: str,
    no_cache: bool = False,
    cache: QuotaCache | None = None,
) -> tuple[QuotaSummary | None, str | None, str | None, bool]:
```

1. If `not no_cache`:
   - Inspect `cache.get(target_name)`.
   - If a valid `QuotaSummary` is found:
     - Load `project_id` from `storage.load_profile(target_name).project_id`.
     - Return `(cached_qs, project_id, None, True)` immediately (bypassing OAuth token refresh and network requests).
2. If `no_cache` or cache miss:
   - Perform full live authentication and `QuotaClient.get_quota(project_id)` request.
   - On HTTP 200 success:
     - Save to cache: `cache.set(target_name, qs)`.
     - Return `(qs, project_id, None, False)`.
   - On HTTP 401 / Revocation:
     - Invalidate any stale cache entry for `target_name`.
     - Return authentication error tuple.

---

## 5. CLI & UX Specifications

### 5.1 `promux quota`
* **Syntax:** `promux quota [name] [--no-cache] [--json]`
* **Behavior:**
  * When run without `--no-cache`, accounts within TTL are loaded instantly from cache.
  * When run with `--no-cache`, forces fresh queries to Cloud Code Assist for all requested accounts and updates cache.
* **JSON Output Schema:**
  Adds `"cached": true | false` to each profile record:
  ```json
  [
    {
      "account": "personal",
      "active": true,
      "cached": false,
      "project_id": "proj-123",
      "gemini": { ... },
      "third_party": { ... }
    }
  ]
  ```
* **Text Output Table:**
  Remains clean and readable. Cached values display seamlessly; if all accounts are cached or fetched, table headers and cells render with identical precision.

### 5.2 `promux switch --smart`
* **Syntax:** `promux switch --smart [--model {gemini,claude,gpt}] [--no-cache] [--json]`
* **Behavior:**
  * Uses cached quotas for candidate standby accounts to make failover decisions instant.
  * `--no-cache` forces candidate evaluation to query live APIs.

### 5.3 Shell Completion & Help
* Register `--no-cache` option in `src/promux/completion.py` for bash and zsh scripts under `quota` and `switch`.
* Document `--no-cache` flag in `src/promux/help.py`.

---

## 6. Concurrency & Safety

* **Multi-threading:** When `ThreadPoolExecutor` fans out `_fetch_account_quota` across accounts in `promux quota`, writes to `cache/quota.json` are synchronized using `threading.Lock()` inside `QuotaCache`.
* **Multi-process:** In multi-process CLI invocations, updates read existing content, update the specific account key, and perform atomic replace (`.tmp` -> `quota.json`) under POSIX file lock (`manager.lock` or `cache/quota.lock`).
* **Permissions:** All directories and cache files are strictly mode `0700` and `0600`.

---

## 7. Testing Strategy

1. **Unit Tests (`tests/test_cache.py`):**
   * Default TTL resolution (300 seconds).
   * Config file override (`config.json` with custom TTL).
   * Environment variable override (`PROMUX_QUOTA_CACHE_TTL`).
   * Cache hit within TTL window.
   * Cache expiration after TTL window has elapsed.
   * Atomic file writes and directory creation.
   * Corrupt config / cache handling (graceful fallback).
   * Concurrency safety under multi-threaded writes.
2. **CLI Integration Tests (`tests/test_cli.py`):**
   * `promux quota` produces cache hit on second run without network call.
   * `promux quota --no-cache` forces network call even within TTL.
   * `promux switch --smart --no-cache` passes `no_cache=True` to quota evaluation.
   * JSON output includes `"cached": true/false`.
3. **Full Suite Regression:**
   * Verify all 210 existing tests continue to pass.
