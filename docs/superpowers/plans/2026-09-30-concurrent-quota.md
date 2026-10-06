# Concurrent Quota Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `promux quota` command and the `switch --smart` failover engine query accounts concurrently for dramatically faster execution, while guaranteeing thread-safety.

**Architecture:** We will adopt `concurrent.futures.ThreadPoolExecutor` for parallel HTTP execution (fanning out account queries). To support this safely, we will migrate `promux`'s non-thread-safe state mechanisms (module globals in CLI, class instance state in Storage) to `threading.local()`, and wrap previously unsafe cache updates in full storage transactions.

**Tech Stack:** Python 3 standard library (`concurrent.futures`, `threading.local()`).

## Global Constraints

- No new external dependencies (stick to Python standard library).
- Output order in the terminal must remain deterministic and identical to the original implementation.

---

### Task 1: Make StorageEngine Transactions Thread-Safe

**Files:**
- Modify: `src/promux/storage.py`

**Interfaces:**
- Consumes: `threading.local()`
- Produces: A thread-safe `_tx_state` that allows multiple threads to safely run independent transactions over the single `StorageEngine` instance.

- [ ] **Step 1: Import threading**

```python
import os
import shutil
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
```

- [ ] **Step 2: Initialize thread-local storage in `__init__`**

Modify `__init__` to instantiate a thread-local object instead of a direct dictionary for `_tx_state`.

```python
    def __init__(self, home: str | Path | None = None):
        self.home = Path(home or os.environ.get("PROMUX_HOME", PROMUX_HOME)).expanduser().resolve()
        self.accounts_dir = self.home / "accounts"
        self.state_file = self.home / "state.json"
        self.lock_file = self.home / "manager.lock"
        self.live_token = self.home / "antigravity-oauth-token"
        
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
```

- [ ] **Step 3: Add properties for `_tx_state` access**

Add `@property` and `@_tx_state.setter` methods to proxy the old `self._tx_state` access transparently to `self._local.tx_state`.

```python
    @property
    def _tx_state(self) -> dict[str, Any] | None:
        return getattr(self._local, "tx_state", None)

    @_tx_state.setter
    def _tx_state(self, value: dict[str, Any] | None) -> None:
        self._local.tx_state = value
```

- [ ] **Step 4: Run tests to verify `StorageEngine` behavior remains intact**

Run: `pytest tests/test_storage.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/promux/storage.py
git commit -m "refactor(storage): make _tx_state thread-local for concurrent transactions"
```

### Task 2: Make CLI Token Refresh State Thread-Safe

**Files:**
- Modify: `src/promux/cli.py`

**Interfaces:**
- Consumes: The `_last_refresh_method` and `_last_refresh_revoked` globals currently used in `cli.py`.
- Produces: Thread-safe equivalents replacing the `global` definitions.

- [ ] **Step 1: Import threading and replace global declarations**

At the top of `src/promux/cli.py`, add the `threading` import.
Find lines around 308 (where `_last_refresh_method: str = "native"` is declared).
Remove the bare module-level variable definitions and replace them with a `threading.local()` instance.

```python
import threading

_cli_local = threading.local()

def _get_last_refresh_method() -> str:
    return getattr(_cli_local, "last_refresh_method", "native")

def _set_last_refresh_method(val: str) -> None:
    _cli_local.last_refresh_method = val

def _get_last_refresh_revoked() -> bool:
    return getattr(_cli_local, "last_refresh_revoked", False)

def _set_last_refresh_revoked(val: bool) -> None:
    _cli_local.last_refresh_revoked = val
```

- [ ] **Step 2: Replace all `global` references**

Search `src/promux/cli.py` and replace all usages of:
`global _last_refresh_revoked` -> Remove
`global _last_refresh_method, _last_refresh_revoked` -> Remove
`_last_refresh_revoked = True` -> `_set_last_refresh_revoked(True)`
`if _last_refresh_revoked:` -> `if _get_last_refresh_revoked():`
`_last_refresh_method = ...` -> `_set_last_refresh_method(...)`
`_last_refresh_method` (when reading) -> `_get_last_refresh_method()`

Ensure you hit `_fetch_account_quota`, `_refresh_token_file`, `_refresh_token_native`, `_refresh_account_token`, etc.

- [ ] **Step 3: Run tests to verify token logic is intact**

Run: `pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/promux/cli.py
git commit -m "refactor(cli): migrate token refresh state tracking to thread-local storage"
```

### Task 3: Wrap Project ID Caching in a Transaction

**Files:**
- Modify: `src/promux/cli.py`

**Interfaces:**
- Consumes: `_fetch_account_quota`
- Produces: Thread-safe cache persistence for dynamically discovered project IDs.

- [ ] **Step 1: Modify `_fetch_account_quota` caching logic**

In `_fetch_account_quota`, locate the block where the loaded `project_id` is cached to the state file (around line 688).

Replace:
```python
            if project_id:
                state = storage.load_state()
                if target_name in state.get("accounts", {}):
                    state["accounts"][target_name]["project_id"] = project_id
                    storage.save_state(state)
```

With a transactional update:
```python
            if project_id:
                with storage.transaction() as state:
                    if target_name in state.get("accounts", {}):
                        state["accounts"][target_name]["project_id"] = project_id
```

- [ ] **Step 2: Run tests to verify no regressions**

Run: `pytest tests/test_cli.py -k "test_cmd_quota" -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/promux/cli.py
git commit -m "fix(cli): use safe storage transaction when caching project_id"
```

### Task 4: Parallelize `promux quota` Multi-Profile Overview

**Files:**
- Modify: `src/promux/cli.py`

**Interfaces:**
- Consumes: `concurrent.futures.ThreadPoolExecutor`
- Produces: Concurrent network fetching for the `accounts` loop inside `cmd_quota`.

- [ ] **Step 1: Import concurrent.futures in cli.py**

```python
import concurrent.futures
```

- [ ] **Step 2: Refactor `cmd_quota` multi-profile loop**

In `cmd_quota`, replace the sequential `for acct_meta in accounts:` loop with a `ThreadPoolExecutor` while preserving output order.

Replace:
```python
    for acct_meta in accounts:
        acct_name = acct_meta.name
        is_active = acct_name == active_profile
        qs, project_id, err = _fetch_account_quota(storage, acct_name)
        
        # ... JSON / text output packing
```

With:
```python
    def fetch_for_account(acct_meta):
        acct_name = acct_meta.name
        is_active = acct_name == active_profile
        qs, project_id, err = _fetch_account_quota(storage, acct_name)
        return acct_name, is_active, qs, project_id, err

    max_workers = min(10, max(1, len(accounts)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_for_account, acct) for acct in accounts]

        for future in futures:  # Preserves correct vault order
            acct_name, is_active, qs, project_id, err = future.result()

            if json_out:
                record: dict[str, Any] = {
                    "account": acct_name,
                    "active": is_active,
                    "project_id": project_id,
                }
                if qs:
                    record["gemini"] = {
                        "5h_remaining": qs.gemini_5h_remaining,
                        "5h_reset": qs.gemini_5h_reset,
                        "5h_reset_relative": format_relative_countdown(qs.gemini_5h_reset),
                        "weekly_remaining": qs.gemini_weekly_remaining,
                        "weekly_reset": qs.gemini_weekly_reset,
                        "weekly_reset_relative": format_relative_countdown(qs.gemini_weekly_reset),
                    }
                    record["third_party"] = {
                        "5h_remaining": qs.third_party_5h_remaining,
                        "5h_reset": qs.third_party_5h_reset,
                        "5h_reset_relative": format_relative_countdown(qs.third_party_5h_reset),
                        "weekly_remaining": qs.third_party_weekly_remaining,
                        "weekly_reset": qs.third_party_weekly_reset,
                        "weekly_reset_relative": format_relative_countdown(qs.third_party_weekly_reset),
                    }
                else:
                    record["error"] = err
                json_records.append(record)
            else:
                active_mark = "*" if is_active else ""
                if qs:
                    g5 = format_quota_cell(qs.gemini_5h_remaining, qs.gemini_5h_reset)
                    gw = format_quota_cell(qs.gemini_weekly_remaining, qs.gemini_weekly_reset)
                    c5 = format_quota_cell(qs.third_party_5h_remaining, qs.third_party_5h_reset)
                    cw = format_quota_cell(qs.third_party_weekly_remaining, qs.third_party_weekly_reset)
                else:
                    g5 = "[AUTH ERROR]" if err and "401" in str(err) else "[ERROR]"
                    gw = "-"
                    c5 = "-"
                    cw = "-"
                text_rows.append((active_mark, acct_name, g5, gw, c5, cw))
```

- [ ] **Step 3: Run tests to verify quota output logic**

Run: `pytest tests/test_cli.py -k "test_cmd_quota" -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/promux/cli.py
git commit -m "perf(cli): parallelize promux quota multi-profile view"
```

### Task 5: Parallelize `rotate_smart` Evaluation

**Files:**
- Modify: `src/promux/failover.py`

**Interfaces:**
- Consumes: `concurrent.futures.ThreadPoolExecutor`
- Produces: Faster resolution for `promux switch --smart` by fetching candidate quotas concurrently.

- [ ] **Step 1: Import concurrent.futures in failover.py**

```python
import concurrent.futures
```

- [ ] **Step 2: Refactor `rotate_smart` loops**

In `src/promux/failover.py`, locate the sequential `for candidate in eligible:` loop inside `rotate_smart` (around line 167).

Replace the sequential fetch loop with `ThreadPoolExecutor`:

```python
            scored_candidates: list[tuple[AccountMeta, float, float, str | None]] = []
            
            def fetch_candidate(candidate):
                try:
                    qs, _, _ = quota_fetcher(candidate.name)
                    return candidate, qs
                except Exception:
                    return candidate, None

            max_workers = min(10, max(1, len(eligible)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(fetch_candidate, c) for c in eligible]
                
                for future in futures:
                    candidate, qs = future.result()
                    if qs is None:
                        continue

                    if model.lower() == "gemini":
                        five_hour = qs.gemini_5h_remaining
                        weekly = qs.gemini_weekly_remaining
                        reset_time = qs.gemini_5h_reset
                    else:
                        five_hour = qs.third_party_5h_remaining
                        weekly = qs.third_party_weekly_remaining
                        reset_time = qs.third_party_5h_reset

                    if five_hour is None or weekly is None:
                        continue
                    if five_hour <= 0.0 or weekly <= 0.0:
                        continue

                    scored_candidates.append((candidate, five_hour, weekly, reset_time))
```

- [ ] **Step 3: Run tests to verify smart switching behavior**

Run: `pytest tests/test_failover.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/promux/failover.py
git commit -m "perf(failover): parallelize candidate quota evaluation for smart switching"
```
