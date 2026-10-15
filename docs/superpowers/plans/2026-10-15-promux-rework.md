# Promux OSS Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform promux from a monolithic 1,569-line cli.py into a professional, cross-platform, observable, agentic-friendly OSS Python CLI tool across 5 mergeable phases.

**Architecture:** Surgical module extraction — move cohesive responsibilities out of `cli.py` one phase at a time. Each phase ends with `pytest` green and the CLI fully functional. Phase 1 (auth.py) unblocks all others by eliminating circular imports. Phases 2–5 are largely independent after Phase 1 merges.

**Tech Stack:** Python 3.10+, stdlib only (zero new runtime deps), setuptools + setuptools-scm, ruff, mypy, pytest.

## Global Constraints

- Python `>=3.10` — use `X | Y` union syntax (not `Optional[X]`), `match` is available but not required
- **Zero new runtime dependencies** — only stdlib. `setuptools-scm` and `wheel` are build-time only.
- All 230 existing tests must pass after every task. Never delete a test.
- `ruff check src tests` must report 0 errors after every commit (or fix lint in same commit)
- `mypy src` must report 0 errors after every commit
- Atomic writes pattern: write to `.tmp` file → `os.replace(tmp, dst)` → `chmod 0o600` (skip chmod on Windows). Never break this pattern.
- `sys.stdout` is reserved for data output. All diagnostic/log output goes to `sys.stderr`.
- Commit messages: conventional commits format (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`)
- Design spec: `docs/superpowers/specs/2026-10-15-promux-rework-design.md`

---

## PHASE 1 — Extract `auth.py` (Eliminate Circular Dependencies)

**Branch:** `phase/1-auth-extraction`

**Context:** `cli.py` is 1,569 lines. Token management functions (lines 54–377) are needed by `storage.py` and `watch.py`, causing circular imports via deferred local imports (`from .cli import _get_or_refresh_access_token` and `from . import cli`). Extract these into `src/promux/auth.py` with `TYPE_CHECKING` guard for `StorageEngine` to avoid re-introducing the cycle.

---

### Task 1.1: Create `auth.py` with token-reading primitives

**Files:**
- Create: `src/promux/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Produces:
  - `read_token_data(token_path: Path) -> dict[str, Any] | None`
  - `extract_access_token(token_data: dict[str, Any] | None) -> str | None`
  - `extract_expiry(token_data: dict[str, Any] | None) -> str | None`
  - `extract_refresh_token(token_data: dict[str, Any] | None) -> str | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth.py
import json
from pathlib import Path
import pytest
from promux import auth


def test_read_token_data_missing_file(tmp_path):
    assert auth.read_token_data(tmp_path / "missing.json") is None


def test_read_token_data_valid(tmp_path):
    p = tmp_path / "token.json"
    p.write_text(json.dumps({"token": {"access_token": "abc"}}))
    data = auth.read_token_data(p)
    assert data == {"token": {"access_token": "abc"}}


def test_read_token_data_invalid_json(tmp_path):
    p = tmp_path / "token.json"
    p.write_text("not-json{{{")
    assert auth.read_token_data(p) is None


def test_read_token_data_non_dict(tmp_path):
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


def test_extract_expiry_nested():
    data = {"token": {"expiry": "2030-01-01T00:00:00Z"}}
    assert auth.extract_expiry(data) == "2030-01-01T00:00:00Z"


def test_extract_expiry_flat():
    data = {"expiry": "2030-01-01T00:00:00Z"}
    assert auth.extract_expiry(data) == "2030-01-01T00:00:00Z"


def test_extract_refresh_token_nested():
    data = {"token": {"refresh_token": "ref_tok"}}
    assert auth.extract_refresh_token(data) == "ref_tok"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_auth.py -v
```
Expected: `ModuleNotFoundError: No module named 'promux.auth'` or `ImportError`.

- [ ] **Step 3: Create `auth.py` — copy token-reading primitives from cli.py**

Create `src/promux/auth.py`. Copy functions verbatim from `cli.py` (lines 54–99), renaming them to drop the `_` prefix:

```python
"""Token I/O and extraction primitives for promux OAuth tokens."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .constants import (
    DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_TOKEN_URL,
)
from .lock import file_lock

if TYPE_CHECKING:
    from .storage import StorageEngine

logger = logging.getLogger(__name__)


def read_token_data(token_path: Path) -> dict[str, Any] | None:
    """Safely read and parse OAuth token JSON."""
    if not token_path.exists():
        return None
    try:
        with open(token_path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def extract_access_token(token_data: dict[str, Any] | None) -> str | None:
    """Extract access_token string from raw token dictionary."""
    if not token_data:
        return None
    tok = token_data.get("token")
    if isinstance(tok, dict):
        return tok.get("access_token")
    if "access_token" in token_data:
        return token_data.get("access_token")
    return None


def extract_expiry(token_data: dict[str, Any] | None) -> str | None:
    """Extract expiry timestamp from raw token dictionary."""
    if not token_data:
        return None
    tok = token_data.get("token")
    if isinstance(tok, dict):
        return tok.get("expiry")
    if "expiry" in token_data:
        return token_data.get("expiry")
    return None


def extract_refresh_token(token_data: dict[str, Any] | None) -> str | None:
    """Extract refresh_token string from raw token dictionary."""
    if not token_data:
        return None
    tok = token_data.get("token")
    if isinstance(tok, dict):
        return tok.get("refresh_token")
    if "refresh_token" in token_data:
        return token_data.get("refresh_token")
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_auth.py -v
```
Expected: All 11 tests PASS.

- [ ] **Step 5: Run full suite to verify nothing broke**

```bash
pytest -x -q
```
Expected: All 230 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/promux/auth.py tests/test_auth.py
git commit -m "feat(auth): create auth module with token-reading primitives"
```

---

### Task 1.2: Add token copy, expiry, and credential functions to `auth.py`

**Files:**
- Modify: `src/promux/auth.py`
- Modify: `tests/test_auth.py`

**Interfaces:**
- Consumes: `read_token_data`, `extract_expiry`, `extract_refresh_token` (from Task 1.1)
- Produces:
  - `atomic_copy_token(src: Path, dst: Path) -> None`
  - `sync_active_tokens(src: Path, dst: Path) -> None`
  - `get_oauth_credentials() -> tuple[str, str]`
  - `parse_token_expiry_dt(expiry_str: str | None) -> datetime | None`
  - `is_token_expired(token_data: dict[str, Any] | None, buffer_seconds: int = DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py`:

```python
import os
from datetime import datetime, timedelta, timezone


def test_atomic_copy_token(tmp_path):
    src = tmp_path / "src_token.json"
    src.write_text('{"token": {"access_token": "abc"}}')
    dst = tmp_path / "dst" / "token.json"
    auth.atomic_copy_token(src, dst)
    assert dst.exists()
    assert json.loads(dst.read_text())["token"]["access_token"] == "abc"
    # On POSIX: check permissions
    if os.name != "nt":
        assert oct(dst.stat().st_mode)[-3:] == "600"


def test_sync_active_tokens_no_src(tmp_path):
    # No source file → no-op, no error
    auth.sync_active_tokens(tmp_path / "missing.json", tmp_path / "dst.json")


def test_sync_active_tokens_same_refresh_token(tmp_path):
    token = {"token": {"refresh_token": "shared", "access_token": "old"}}
    src = tmp_path / "src.json"
    src.write_text(json.dumps(token))
    dst = tmp_path / "dst.json"
    dst.write_text(json.dumps({**token, "token": {**token["token"], "access_token": "old"}}))
    auth.sync_active_tokens(src, dst)
    # Copied because same refresh token
    assert auth.extract_access_token(auth.read_token_data(dst)) == "old"


def test_sync_active_tokens_different_refresh_skips(tmp_path):
    src_token = {"token": {"refresh_token": "rA", "access_token": "new"}}
    dst_token = {"token": {"refresh_token": "rB", "access_token": "old"}}
    src = tmp_path / "src.json"
    src.write_text(json.dumps(src_token))
    dst = tmp_path / "dst.json"
    dst.write_text(json.dumps(dst_token))
    auth.sync_active_tokens(src, dst)
    # Different refresh tokens → skip
    assert auth.extract_access_token(auth.read_token_data(dst)) == "old"


def test_parse_token_expiry_dt_valid():
    result = auth.parse_token_expiry_dt("2030-01-01T00:00:00Z")
    assert result is not None
    assert result.tzinfo is not None


def test_parse_token_expiry_dt_invalid():
    assert auth.parse_token_expiry_dt("not-a-date") is None
    assert auth.parse_token_expiry_dt(None) is None


def test_is_token_expired_no_data():
    assert auth.is_token_expired(None) is True


def test_is_token_expired_future():
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    data = {"token": {"expiry": future}}
    assert auth.is_token_expired(data) is False


def test_is_token_expired_past():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    data = {"token": {"expiry": past}}
    assert auth.is_token_expired(data) is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_auth.py -v -k "copy_token or sync_active or expiry_dt or is_token_expired"
```
Expected: FAIL — `AttributeError: module 'promux.auth' has no attribute 'atomic_copy_token'`.

- [ ] **Step 3: Append functions to `auth.py`**

Copy these functions from `cli.py` (lines 102–171), adapting names:

```python
def atomic_copy_token(src: Path, dst: Path) -> None:
    """Atomically copy a token file ensuring 0600 permissions."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_dst = dst.with_suffix(f".tmp_{os.getpid()}")
    shutil.copy2(src, tmp_dst)
    if os.name != "nt":
        tmp_dst.chmod(0o600)
    os.replace(tmp_dst, dst)


def sync_active_tokens(src: Path, dst: Path) -> None:
    """Atomically copy refreshed token from src to dst if tokens share identity or dst is missing."""
    try:
        if not src.exists():
            return
        if dst.exists():
            src_td = read_token_data(src)
            dst_td = read_token_data(dst)
            sr = extract_refresh_token(src_td)
            dr = extract_refresh_token(dst_td)
            if sr and dr and sr != dr:
                return
        atomic_copy_token(src, dst)
    except Exception:
        logger.debug("sync_active_tokens failed src=%s dst=%s", src, dst, exc_info=True)


def get_oauth_credentials() -> tuple[str, str]:
    """Retrieve OAuth client ID and secret from environment or ~/.promux/oauth.json."""
    from .constants import PROMUX_HOME
    client_id = os.environ.get("PROMUX_OAUTH_CLIENT_ID", OAUTH_CLIENT_ID)
    client_secret = os.environ.get("PROMUX_OAUTH_CLIENT_SECRET", OAUTH_CLIENT_SECRET)
    if not client_id or not client_secret:
        cfg_file = PROMUX_HOME / "oauth.json"
        if cfg_file.exists():
            try:
                with open(cfg_file, encoding="utf-8") as f:
                    data = json.load(f)
                    client_id = client_id or data.get("client_id", "")
                    client_secret = client_secret or data.get("client_secret", "")
            except Exception:
                logger.debug("Failed to read oauth.json", exc_info=True)
    return client_id, client_secret


def parse_token_expiry_dt(expiry_str: str | None) -> datetime | None:
    """Parse ISO8601 expiry string into timezone-aware UTC datetime."""
    if not expiry_str or not isinstance(expiry_str, str):
        return None
    try:
        clean_exp = expiry_str.replace("Z", "+00:00")
        exp_dt = datetime.fromisoformat(clean_exp)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return exp_dt
    except Exception:
        return None


def is_token_expired(
    token_data: dict[str, Any] | None,
    buffer_seconds: int = DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS,
) -> bool:
    """Check if token is expired or within buffer_seconds of expiration."""
    if not token_data or not isinstance(token_data, dict):
        return True
    expiry_str = extract_expiry(token_data)
    exp_dt = parse_token_expiry_dt(expiry_str)
    if exp_dt is None:
        return True
    now = datetime.now(timezone.utc)
    return now + timedelta(seconds=buffer_seconds) >= exp_dt
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_auth.py -v
```
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/promux/auth.py tests/test_auth.py
git commit -m "feat(auth): add token copy, expiry, and credential functions"
```

---

### Task 1.3: Add token refresh orchestration to `auth.py`

**Files:**
- Modify: `src/promux/auth.py`
- Modify: `tests/test_auth.py`

**Interfaces:**
- Consumes: all functions from Tasks 1.1–1.2
- Produces:
  - `get_or_refresh_access_token(token_path: Path, storage: StorageEngine | None = None) -> str | None`
  - (private) `_refresh_token_native(token_path, token_data) -> str | None`
  - (private) `_refresh_token_fallback_agy(token_path, storage) -> str | None`
  - (private) `_refresh_token_file(token_path, token_data, storage=None) -> str | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py`:

```python
def test_get_or_refresh_access_token_missing_file(tmp_path):
    result = auth.get_or_refresh_access_token(tmp_path / "no_token.json")
    assert result is None


def test_get_or_refresh_access_token_valid_unexpired(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    token_data = {"token": {"access_token": "valid_tok", "expiry": future}}
    p = tmp_path / "token.json"
    p.write_text(json.dumps(token_data))
    result = auth.get_or_refresh_access_token(p)
    assert result == "valid_tok"


def test_get_or_refresh_access_token_expired_no_refresh(tmp_path):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    token_data = {"token": {"access_token": "old_tok", "expiry": past}}
    # No refresh_token → can't refresh → returns existing access token
    p = tmp_path / "token.json"
    p.write_text(json.dumps(token_data))
    result = auth.get_or_refresh_access_token(p)
    assert result == "old_tok"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_auth.py -v -k "get_or_refresh"
```
Expected: FAIL — `AttributeError: module 'promux.auth' has no attribute 'get_or_refresh_access_token'`.

- [ ] **Step 3: Append refresh orchestration to `auth.py`**

Copy the thread-local state, `_refresh_token_native`, `_refresh_token_fallback_agy`, `_refresh_token_file`, and `get_or_refresh_access_token` from `cli.py` (lines 310–377) **verbatim** — only change:
- private helper names stay private (keep `_` prefix)
- `_get_or_refresh_access_token` → `get_or_refresh_access_token` (public)
- Replace all calls to `_read_token_data`, `_extract_*`, `_atomic_copy_token` etc. with their new public names
- Replace `from .constants import ...` with the already-imported names at top of file

```python
# Thread-local state for refresh tracking
_auth_local = threading.local()


def _get_last_refresh_method() -> str:
    return getattr(_auth_local, "last_refresh_method", "native")


def _set_last_refresh_method(val: str) -> None:
    _auth_local.last_refresh_method = val


def _get_last_refresh_revoked() -> bool:
    return getattr(_auth_local, "last_refresh_revoked", False)


def _set_last_refresh_revoked(val: bool) -> None:
    _auth_local.last_refresh_revoked = val


def _refresh_token_native(token_path: Path, token_data: dict[str, Any]) -> str | None:
    """Tier 1: Execute direct HTTP OAuth refresh using Google OAuth token endpoint."""
    if not isinstance(token_data, dict):
        return None
    tok = token_data.get("token")
    if not isinstance(tok, dict):
        return None
    refresh_token = tok.get("refresh_token")
    if not refresh_token:
        return None
    client_id, client_secret = get_oauth_credentials()
    if not client_id or not client_secret:
        return None
    try:
        data = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        req = urllib.request.Request(OAUTH_TOKEN_URL, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        new_acc = res.get("access_token") if isinstance(res, dict) else None
        if isinstance(new_acc, str):
            tok["access_token"] = new_acc
            expires_in = res.get("expires_in", 3600) if isinstance(res, dict) else 3600
            now = datetime.now(timezone.utc)
            tok["expiry"] = (now + timedelta(seconds=expires_in)).isoformat().replace("+00:00", "Z")
            if isinstance(res, dict) and "refresh_token" in res and isinstance(res["refresh_token"], str):
                tok["refresh_token"] = res["refresh_token"]
            tmp_path = token_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(token_data, f, indent=2)
            if os.name != "nt":
                tmp_path.chmod(0o600)
            os.replace(tmp_path, token_path)
            return new_acc
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            if isinstance(body, dict) and body.get("error") == "invalid_grant":
                _set_last_refresh_revoked(True)
        except Exception:
            if "invalid_grant" in str(e).lower():
                _set_last_refresh_revoked(True)
    except Exception as e:
        if "invalid_grant" in str(e).lower():
            _set_last_refresh_revoked(True)
        logger.debug("Native token refresh failed for %s: %s", token_path, e, exc_info=True)
    return None


def _refresh_token_fallback_agy(token_path: Path, storage: StorageEngine) -> str | None:
    """Tier 2: Fallback renewal via headless agy CLI under storage file lock."""
    import subprocess
    import shutil as _shutil
    from contextlib import nullcontext
    if not _shutil.which("agy"):
        return None
    if not token_path.exists():
        return None
    try:
        lock_ctx = (
            nullcontext()
            if getattr(storage, "_tx_state", None) is not None
            else file_lock(storage.lock_file)
        )
        with lock_ctx:
            old_data = read_token_data(token_path)
            old_acc = extract_access_token(old_data)
            old_exp = extract_expiry(old_data)
            is_same = False
            try:
                is_same = token_path.resolve() == storage.live_token.resolve()
            except Exception:
                pass
            if is_same:
                try:
                    subprocess.run(["agy", "models"], capture_output=True, timeout=10)
                except (subprocess.SubprocessError, OSError):
                    pass
                new_data = read_token_data(storage.live_token)
                new_acc = extract_access_token(new_data)
                new_exp = extract_expiry(new_data)
                if new_acc and (old_acc is None or new_acc != old_acc or new_exp != old_exp):
                    return new_acc
                return None
            storage.live_token.parent.mkdir(parents=True, exist_ok=True)
            backup_path = storage.live_token.with_name(
                f"{storage.live_token.name}.fallback_bak_{os.getpid()}"
            )
            live_existed = storage.live_token.exists()
            if live_existed:
                _shutil.copy2(storage.live_token, backup_path)
                if os.name != "nt":
                    backup_path.chmod(0o600)
            refreshed_acc: str | None = None
            try:
                tmp_live = storage.live_token.with_name(f".tmp_live_{os.getpid()}")
                _shutil.copy2(token_path, tmp_live)
                if os.name != "nt":
                    tmp_live.chmod(0o600)
                os.replace(tmp_live, storage.live_token)
                try:
                    subprocess.run(["agy", "models"], capture_output=True, timeout=10)
                except (subprocess.SubprocessError, OSError):
                    pass
                new_data = read_token_data(storage.live_token)
                new_acc = extract_access_token(new_data)
                new_exp = extract_expiry(new_data)
                if new_acc and (old_acc is None or new_acc != old_acc or new_exp != old_exp):
                    tmp_path = token_path.with_suffix(f".tmp_{os.getpid()}")
                    _shutil.copy2(storage.live_token, tmp_path)
                    if os.name != "nt":
                        tmp_path.chmod(0o600)
                    os.replace(tmp_path, token_path)
                    refreshed_acc = new_acc
            finally:
                if live_existed:
                    if backup_path.exists():
                        os.replace(backup_path, storage.live_token)
                        if os.name != "nt":
                            storage.live_token.chmod(0o600)
                else:
                    if storage.live_token.exists():
                        storage.live_token.unlink()
                    if backup_path.exists():
                        backup_path.unlink()
            return refreshed_acc
    except Exception:
        logger.debug("agy fallback refresh failed for %s", token_path, exc_info=True)
        return None


def _refresh_token_file(
    token_path: Path,
    token_data: dict[str, Any],
    storage: StorageEngine | None = None,
) -> str | None:
    """Attempt to refresh an expired token using its refresh_token or agy fallback."""
    _set_last_refresh_method("failed")
    _set_last_refresh_revoked(False)
    try:
        refreshed = _refresh_token_native(token_path, token_data)
    except Exception as e:
        if "invalid_grant" in str(e).lower():
            _set_last_refresh_revoked(True)
        refreshed = None
    if refreshed:
        _set_last_refresh_method("native")
        _set_last_refresh_revoked(False)
        return refreshed
    if _get_last_refresh_revoked():
        _set_last_refresh_method("revoked")
        return None
    if storage is not None:
        refreshed = _refresh_token_fallback_agy(token_path, storage)
        if refreshed:
            _set_last_refresh_method("fallback (agy)")
            return refreshed
    return None


def get_or_refresh_access_token(
    token_path: Path,
    storage: StorageEngine | None = None,
) -> str | None:
    """Get access token, refreshing it if expired and refresh_token is present."""
    token_data = read_token_data(token_path)
    if not token_data:
        return None
    access_token = extract_access_token(token_data)
    if is_token_expired(token_data, buffer_seconds=DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS):
        refreshed = _refresh_token_file(token_path, token_data, storage=storage)
        if refreshed:
            return refreshed
    return access_token
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_auth.py -v
```
Expected: All tests PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest -x -q
```
Expected: All 230 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/promux/auth.py tests/test_auth.py
git commit -m "feat(auth): add token refresh orchestration (native + agy fallback)"
```

---

### Task 1.4: Wire `storage.py` and `watch.py` to use `auth.py`; prune `cli.py`

**Files:**
- Modify: `src/promux/storage.py`
- Modify: `src/promux/watch.py`
- Modify: `src/promux/cli.py`

**Interfaces:**
- Consumes: all public functions from `auth.py` (Tasks 1.1–1.3)

- [ ] **Step 1: Update `storage.py`**

Find `storage.py:147`:
```python
from .cli import _get_or_refresh_access_token
refreshed_token = _get_or_refresh_access_token(src_token, storage=self)
```

Replace with:
```python
from .auth import get_or_refresh_access_token
refreshed_token = get_or_refresh_access_token(src_token, storage=self)
```

Also anywhere `storage.py` calls `_atomic_copy_token`, `_read_token_data`, or other helpers from cli — update to `from .auth import ...` equivalents.

- [ ] **Step 2: Update `watch.py`**

Find `watch.py:158`:
```python
from . import cli
# ...
token_data = cli._read_token_data(token_path)
```

Replace entire `from . import cli` block with:
```python
from . import auth
# ...
token_data = auth.read_token_data(token_path)
```

Update every `cli._*` call in `watch.py` to `auth.*` (drop the `_` prefix).

- [ ] **Step 3: Remove duplicated functions from `cli.py`**

Delete lines 54–377 from `cli.py` (the token functions). Replace with a thin re-export shim for any remaining internal callers that haven't been updated yet:

```python
# cli.py — compatibility shim (remove after Phase 4)
from .auth import (
    read_token_data as _read_token_data,
    extract_access_token as _extract_access_token,
    extract_expiry as _extract_expiry,
    extract_refresh_token as _extract_refresh_token,
    atomic_copy_token as _atomic_copy_token,
    sync_active_tokens as _sync_active_tokens,
    get_oauth_credentials as _get_oauth_credentials,
    parse_token_expiry_dt as _parse_token_expiry_dt,
    is_token_expired as _is_token_expired,
    get_or_refresh_access_token as _get_or_refresh_access_token,
    _refresh_token_file,
    _get_last_refresh_method,
    _get_last_refresh_revoked,
    _set_last_refresh_revoked,
)
```

This shim means existing `cli.py` command handlers (`cmd_save`, `cmd_quota`, etc.) don't need to change yet — they'll get updated in Phase 4.

- [ ] **Step 4: Run full test suite**

```bash
pytest -x -q
```
Expected: All 230 tests pass. No import errors.

- [ ] **Step 5: Verify no circular imports**

```bash
python -c "from promux import storage; from promux import watch; from promux import cli; print('OK')"
```
Expected: `OK` with no `ImportError`.

- [ ] **Step 6: Run lint and typecheck**

```bash
ruff check src tests
mypy src
```
Fix any new violations before committing.

- [ ] **Step 7: Commit**

```bash
git add src/promux/storage.py src/promux/watch.py src/promux/cli.py
git commit -m "refactor: wire storage/watch to auth.py, add cli.py compat shim

Eliminates circular imports: storage.py and watch.py no longer import
from cli.py. Token logic now lives in auth.py.
cli.py retains compatibility shim until Phase 4."
```

- [ ] **Step 8: Open PR and merge**

```bash
git push -u origin phase/1-auth-extraction
# Open PR: "Phase 1: Extract auth.py, eliminate circular dependencies"
# Merge after CI passes
git checkout main && git pull
```

---

## PHASE 2 — Cross-Platform `lock.py`

**Branch:** `phase/2-crossplatform-lock`  
**Prerequisite:** Phase 1 merged to `main`.

**Context:** `lock.py` line 1 is `import fcntl` — crashes on Windows. Split into platform-dispatched files using `LockFileEx` via `ctypes` on Windows (not `msvcrt.locking` — see spec §4.2 for rationale).

---

### Task 2.1: Create `_lock_unix.py` and `_lock_windows.py`

**Files:**
- Create: `src/promux/_lock_unix.py`
- Create: `src/promux/_lock_windows.py`

**Interfaces:**
- Produces (both files expose the same interface):
  - `_acquire_lock(fd: int, non_blocking: bool) -> bool`
  - `_release_lock(fd: int) -> None`

- [ ] **Step 1: Create `_lock_unix.py`**

```python
"""POSIX advisory file locking via fcntl.flock."""

from __future__ import annotations

import errno
import fcntl

_CONTENTION_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})


def _acquire_lock(fd: int, non_blocking: bool) -> bool:
    """Acquire an exclusive lock on fd. Returns False if non_blocking and contended."""
    flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if non_blocking else 0)
    try:
        fcntl.flock(fd, flags)
        return True
    except OSError as exc:
        if non_blocking and exc.errno in _CONTENTION_ERRNOS:
            return False
        raise


def _release_lock(fd: int) -> None:
    """Release the lock held on fd."""
    fcntl.flock(fd, fcntl.LOCK_UN)
```

- [ ] **Step 2: Create `_lock_windows.py`**

```python
"""Windows file locking via kernel32.LockFileEx (ctypes, no external deps)."""

from __future__ import annotations

import ctypes
import msvcrt
from ctypes import wintypes

_LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
_LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
_ERROR_LOCK_VIOLATION = 33


class _OVERLAPPED(ctypes.Structure):
    """Windows OVERLAPPED structure for async I/O (used for lock byte offset)."""

    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_LockFileEx = _kernel32.LockFileEx
_LockFileEx.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(_OVERLAPPED),
]
_LockFileEx.restype = wintypes.BOOL

_UnlockFileEx = _kernel32.UnlockFileEx
_UnlockFileEx.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(_OVERLAPPED),
]
_UnlockFileEx.restype = wintypes.BOOL


def _acquire_lock(fd: int, non_blocking: bool) -> bool:
    """Acquire an exclusive lock on fd using LockFileEx.

    Uses OVERLAPPED with Offset=0 so lock position is absolute (no seek() needed).
    Returns False if non_blocking and the lock is contended.
    """
    handle = msvcrt.get_osfhandle(fd)
    flags = _LOCKFILE_EXCLUSIVE_LOCK
    if non_blocking:
        flags |= _LOCKFILE_FAIL_IMMEDIATELY
    overlapped = _OVERLAPPED()  # Offset=0, OffsetHigh=0 → lock byte 0
    success = _LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlapped))
    if not success:
        err = ctypes.get_last_error()
        if non_blocking and err == _ERROR_LOCK_VIOLATION:
            return False
        raise ctypes.WinError(err)
    return True


def _release_lock(fd: int) -> None:
    """Release the lock held on fd."""
    handle = msvcrt.get_osfhandle(fd)
    overlapped = _OVERLAPPED()
    if not _UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
        raise ctypes.WinError(ctypes.get_last_error())
```

- [ ] **Step 3: Commit**

```bash
git add src/promux/_lock_unix.py src/promux/_lock_windows.py
git commit -m "feat(lock): add platform-specific lock implementations (fcntl + LockFileEx)"
```

---

### Task 2.2: Rewrite `lock.py` with platform dispatch

**Files:**
- Modify: `src/promux/lock.py`
- Create: `tests/test_lock.py`

**Interfaces:**
- Consumes: `_acquire_lock`, `_release_lock` from `_lock_unix.py` or `_lock_windows.py`
- Produces: `file_lock(lock_path, timeout, poll_interval) -> Generator[Path, None, None]` — **same signature as before**

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_lock.py
import threading
import time
from pathlib import Path
import pytest
from promux.lock import file_lock


def test_lock_acquire_and_release(tmp_path):
    lock_path = tmp_path / "test.lock"
    with file_lock(lock_path, timeout=2.0):
        assert lock_path.exists()
    # Should not raise


def test_lock_creates_parent_dirs(tmp_path):
    lock_path = tmp_path / "nested" / "dir" / "test.lock"
    with file_lock(lock_path, timeout=2.0):
        assert lock_path.exists()


def test_lock_timeout_raises(tmp_path):
    lock_path = tmp_path / "contended.lock"
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock():
        with file_lock(lock_path, timeout=5.0):
            acquired.set()
            release.wait(timeout=5.0)

    t = threading.Thread(target=hold_lock)
    t.start()
    acquired.wait(timeout=3.0)

    with pytest.raises(TimeoutError):
        with file_lock(lock_path, timeout=0.2, poll_interval=0.05):
            pass

    release.set()
    t.join(timeout=3.0)


def test_lock_released_on_exception(tmp_path):
    lock_path = tmp_path / "release.lock"
    try:
        with file_lock(lock_path, timeout=2.0):
            raise ValueError("intentional")
    except ValueError:
        pass
    # Should be able to re-acquire immediately
    with file_lock(lock_path, timeout=2.0):
        pass  # No TimeoutError means lock was released


def test_lock_file_not_unlinked(tmp_path):
    """Lock files must never be deleted on release (inode-splitting race prevention)."""
    lock_path = tmp_path / "stay.lock"
    with file_lock(lock_path, timeout=2.0):
        pass
    assert lock_path.exists(), "Lock file must not be unlinked on release"
```

- [ ] **Step 2: Run tests to verify some fail** (they currently pass on POSIX but the point is the timeout test proves isolation)

```bash
pytest tests/test_lock.py -v
```

- [ ] **Step 3: Rewrite `lock.py`**

```python
"""Cross-platform advisory file lock. Dispatches to fcntl (POSIX) or LockFileEx (Windows)."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from .constants import DEFAULT_LOCK_TIMEOUT

if sys.platform == "win32":
    from ._lock_windows import _acquire_lock, _release_lock
else:
    from ._lock_unix import _acquire_lock, _release_lock


@contextmanager
def file_lock(
    lock_path: str | Path,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
    poll_interval: float = 0.05,
) -> Generator[Path, None, None]:
    """Advisory file lock context manager. Works on POSIX (fcntl) and Windows (LockFileEx).

    IMPORTANT: The lock file is never deleted on release. Deleting it would introduce
    an inode-splitting race condition: two processes could hold the lock simultaneously
    on the same path but different inodes.

    Args:
        lock_path: Path of the file to use as lock holder.
        timeout: Seconds to wait before raising TimeoutError. Default 10s.
        poll_interval: Sleep between non-blocking retry attempts.

    Yields:
        The resolved lock_path as a Path.

    Raises:
        TimeoutError: If lock cannot be acquired within timeout.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    open_flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW  # POSIX: prevent symlink TOCTOU attacks

    fd = os.open(str(path), open_flags, 0o666)
    acquired = False
    try:
        start = time.monotonic()
        while True:
            if _acquire_lock(fd, non_blocking=True):
                acquired = True
                break
            if time.monotonic() - start >= timeout:
                raise TimeoutError(
                    f"Timed out after {timeout}s waiting for lock: {path}"
                )
            time.sleep(poll_interval)

        yield path
    finally:
        if acquired:
            try:
                _release_lock(fd)
            except OSError:
                pass
        os.close(fd)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_lock.py -v
```
Expected: All 5 tests PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest -x -q
mypy src
ruff check src tests
```
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/promux/lock.py tests/test_lock.py
git commit -m "refactor(lock): platform-dispatch to fcntl (POSIX) or LockFileEx (Windows)"
```

---

### Task 2.3: Fix Windows-incompatible patterns; add CI matrix

**Files:**
- Modify: `src/promux/auth.py` — wrap `chmod` calls
- Modify: `src/promux/storage.py` — wrap `chmod` and `st_ino` calls
- Modify: `.github/workflows/*.yml` — add `windows-latest`
- Modify: `pyproject.toml` — add Windows classifier

- [ ] **Step 1: Fix `chmod` calls to be no-ops on Windows**

Search for all `chmod(0o600)` calls across `src/`. Replace each with:

```python
if os.name != "nt":
    some_path.chmod(0o600)
```

Files to check: `auth.py`, `storage.py`, `cli.py`.

- [ ] **Step 2: Fix `st_ino` usage**

Search for `st_ino` in `src/`. If used for identity checks (e.g., "is this the same file?"), add guard:

```python
stat_a = path_a.stat()
stat_b = path_b.stat()
# st_ino is 0 on Windows NTFS — skip inode comparison on Windows
if os.name != "nt" and stat_a.st_ino != 0:
    is_same = stat_a.st_ino == stat_b.st_ino and stat_a.st_dev == stat_b.st_dev
else:
    is_same = path_a.resolve() == path_b.resolve()
```

- [ ] **Step 3: Add `windows-latest` to GitHub Actions CI**

Find the existing workflow file (e.g. `.github/workflows/ci.yml`). Locate the `strategy.matrix` section and add `windows-latest`:

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest, windows-latest]
    python-version: ["3.10", "3.12", "3.13"]
runs-on: ${{ matrix.os }}
```

- [ ] **Step 4: Update `pyproject.toml` classifiers**

```toml
classifiers = [
    ...
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS",
    "Operating System :: Microsoft :: Windows",
    ...
]
```

- [ ] **Step 5: Run full suite**

```bash
pytest -x -q
ruff check src tests
mypy src
```

- [ ] **Step 6: Commit and merge**

```bash
git add src/promux/auth.py src/promux/storage.py src/promux/cli.py \
        pyproject.toml .github/workflows/ci.yml
git commit -m "fix: Windows compatibility — chmod guards, inode fallback, CI matrix

- Wrap chmod(0o600) with os.name != 'nt' guard
- Guard st_ino identity checks (returns 0 on NTFS)
- Add windows-latest + macos-latest to CI matrix
- Add Windows PyPI classifier"
git push -u origin phase/2-crossplatform-lock
# Open PR, merge after CI passes
git checkout main && git pull
```

---

## PHASE 3 — Logging Framework

**Branch:** `phase/3-logging`  
**Prerequisite:** Phase 1 merged. (Phase 2 can be in flight concurrently.)

**Context:** Zero `import logging` in the entire codebase. 30+ silent `except Exception: pass` blocks. Add stdlib `logging` without any new runtime deps.

---

### Task 3.1: Create `logging_config.py` and wire to `main()`

**Files:**
- Create: `src/promux/logging_config.py`
- Modify: `src/promux/cli.py`
- Create: `tests/test_logging_config.py`

**Interfaces:**
- Produces:
  - `configure_cli_logging(verbose: int = 0, quiet: bool = False) -> None`
  - `configure_daemon_logging(log_file: Path | None = None, debug: bool = False) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_logging_config.py
import logging
import sys
import pytest
from promux import logging_config


def _clear_promux_logger():
    logger = logging.getLogger("promux")
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)


def test_cli_logging_default_is_warning(capsys):
    _clear_promux_logger()
    logging_config.configure_cli_logging(verbose=0)
    logger = logging.getLogger("promux.test")
    logger.info("should not appear")
    logger.warning("should appear")
    captured = capsys.readouterr()
    assert "should not appear" not in captured.err
    assert "should appear" in captured.err


def test_cli_logging_verbose_1_is_info(capsys):
    _clear_promux_logger()
    logging_config.configure_cli_logging(verbose=1)
    logger = logging.getLogger("promux.test")
    logger.info("info message")
    captured = capsys.readouterr()
    assert "info message" in captured.err


def test_cli_logging_verbose_2_is_debug(capsys):
    _clear_promux_logger()
    logging_config.configure_cli_logging(verbose=2)
    logger = logging.getLogger("promux.test")
    logger.debug("debug message")
    captured = capsys.readouterr()
    assert "debug message" in captured.err


def test_cli_logging_quiet_is_error_only(capsys):
    _clear_promux_logger()
    logging_config.configure_cli_logging(quiet=True)
    logger = logging.getLogger("promux.test")
    logger.warning("should not appear")
    logger.error("should appear")
    captured = capsys.readouterr()
    assert "should not appear" not in captured.err
    assert "should appear" in captured.err


def test_cli_logging_writes_to_stderr_not_stdout(capsys):
    _clear_promux_logger()
    logging_config.configure_cli_logging(verbose=1)
    logging.getLogger("promux.test").info("stderr only")
    captured = capsys.readouterr()
    assert "stderr only" in captured.err
    assert "stderr only" not in captured.out
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_logging_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'promux.logging_config'`.

- [ ] **Step 3: Create `logging_config.py`**

```python
"""Logging configuration for promux CLI and daemon modes."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_cli_logging(verbose: int = 0, quiet: bool = False) -> None:
    """Configure interactive CLI logging. Call once from main(). Logs go to stderr.

    Args:
        verbose: 0=WARNING (default), 1=INFO (-v), 2+=DEBUG (-vv)
        quiet: If True, only ERROR and above are shown.
    """
    if quiet:
        level = logging.ERROR
    elif verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    else:
        level = logging.WARNING

    pkg = logging.getLogger("promux")
    pkg.handlers.clear()
    pkg.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    if level == logging.DEBUG:
        fmt = "%(levelname)s [%(name)s:%(lineno)d] %(message)s"
    else:
        fmt = "%(levelname)s: %(message)s"

    handler.setFormatter(logging.Formatter(fmt))
    pkg.addHandler(handler)
    pkg.propagate = False


def configure_daemon_logging(
    log_file: Path | None = None,
    debug: bool = False,
) -> None:
    """Configure daemon (watch) mode logging with timestamps and PID.

    Args:
        log_file: If given, log to rotating file. Otherwise log to stderr.
        debug: If True, use DEBUG level. Otherwise INFO.
    """
    level = logging.DEBUG if debug else logging.INFO

    pkg = logging.getLogger("promux")
    pkg.handlers.clear()
    pkg.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [pid:%(process)d] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    if log_file:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            log_file, maxBytes=10_000_000, backupCount=5, encoding="utf-8"
        )
    else:
        handler = logging.StreamHandler(sys.stderr)

    handler.setLevel(level)
    handler.setFormatter(formatter)
    pkg.addHandler(handler)
    pkg.propagate = False
```

- [ ] **Step 4: Add NullHandler to `__init__.py`**

```python
# src/promux/__init__.py
"""Promux: Antigravity CLI profile multiplexer & quota failover daemon."""

import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())

__version__ = "0.4.0"
```

- [ ] **Step 5: Wire `configure_cli_logging` into `main()`**

In `cli.py`, add `-v`/`--verbose` and `-q`/`--quiet` to `build_parser()` common parser, and call `configure_cli_logging` at the start of `main()`:

```python
# In build_parser(), add to common_parser:
common_parser.add_argument(
    "-v", "--verbose",
    action="count",
    default=0,
    help="Increase verbosity (-v for INFO, -vv for DEBUG)",
)
common_parser.add_argument(
    "-q", "--quiet",
    action="store_true",
    default=False,
    help="Suppress all output except errors",
)

# In main(), before the first try block:
from .logging_config import configure_cli_logging
verbose = getattr(args, "verbose", 0) if "args" in dir() else 0
quiet = getattr(args, "quiet", False) if "args" in dir() else False
configure_cli_logging(verbose=verbose, quiet=quiet)
```

> **Note:** `configure_cli_logging` must be called after `args = parser.parse_args(remaining_args)` so we have the flag values. Place it immediately after the `parse_args` line.

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/test_logging_config.py -v
pytest -x -q
```
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add src/promux/logging_config.py src/promux/__init__.py src/promux/cli.py \
        tests/test_logging_config.py
git commit -m "feat(logging): add stdlib logging framework with CLI/daemon modes

- New logging_config.py with configure_cli_logging() and configure_daemon_logging()
- NullHandler in __init__.py (library best practice)
- -v/--verbose and -q/--quiet flags in CLI
- Logs go to stderr only (stdout reserved for data)"
```

---

### Task 3.2: Add `logger = logging.getLogger(__name__)` to every module; replace silent swallows

**Files:**
- Modify: `src/promux/auth.py`, `storage.py`, `watch.py`, `failover.py`, `quota.py`, `cache.py`, `constants.py`, `formatters.py`, `lock.py`, `adapters/*.py`

**Interfaces:**
- No new public API. Internal change only.

- [ ] **Step 1: Add logger to each module**

For each file in `src/promux/` (except `__init__.py`, `logging_config.py`, and private `_lock_*.py`):

Add near the top, after imports:
```python
import logging
logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Replace silent swallows — auth.py**

Find every `except Exception: pass` or `except Exception as e: pass` in `auth.py`. Apply the triage table from spec §5.4:

| Pattern | Replacement |
|---------|------------|
| Token I/O read failure | `logger.debug("Failed to read token data from %s", token_path, exc_info=True)` |
| HTTP 401 / network | `logger.warning("Token refresh HTTP error: %s", e, exc_info=True)` |
| `invalid_grant` detection | `logger.info("Refresh token revoked (invalid_grant) for %s", token_path)` |
| agy subprocess failure | `logger.info("agy fallback subprocess failed: %s", e)` |
| Sync copy failure | `logger.debug("sync_active_tokens failed src=%s dst=%s", src, dst, exc_info=True)` |

- [ ] **Step 3: Replace silent swallows — storage.py, watch.py, failover.py, cache.py**

Apply same pattern — every `except Exception: pass` becomes at minimum `logger.debug("...", exc_info=True)`. For errors the operator should know about (e.g., config read failure), use `logger.warning(...)`.

- [ ] **Step 4: Run full suite to verify nothing broke**

```bash
pytest -x -q
```
Expected: All 230 tests pass. The logging calls are inert unless a test specifically configures logging.

- [ ] **Step 5: Run lint and typecheck**

```bash
ruff check src tests
mypy src
```

- [ ] **Step 6: Commit**

```bash
git add src/promux/
git commit -m "refactor: add per-module loggers, replace silent exception swallows

Every module now has logging.getLogger(__name__). 30+ except Exception: pass
blocks replaced with appropriate logger.debug/warning/error calls.
No behavior change — logging is at WARNING by default."
```

- [ ] **Step 7: Update `cmd_watch` to use `configure_daemon_logging`**

In `cli.py` or `commands/watch.py` (if Phase 4 already landed), the `cmd_watch` function should call `configure_daemon_logging()` instead of `configure_cli_logging()`:

```python
def cmd_watch(failover: FailoverEngine, poll_seconds: float, cooldown: int | None) -> int:
    from .logging_config import configure_daemon_logging
    configure_daemon_logging()  # Switches to timestamped, PID-tagged format
    logger.info("Starting log watcher (poll: %ss, cooldown: %s)", poll_seconds, cooldown or "auto")
    # ... rest of function unchanged
```

- [ ] **Step 8: Commit and merge**

```bash
git add src/promux/cli.py
git commit -m "feat(watch): use daemon logging mode with timestamps and PID"
git push -u origin phase/3-logging
# Open PR, merge after CI passes
git checkout main && git pull
```

---

## PHASE 4 — Extract `commands/` Package

**Branch:** `phase/4-commands-package`  
**Prerequisite:** Phase 1 merged. Phase 3 strongly recommended (logging calls in command handlers).

**Context:** After Phase 1, `cli.py` is ~1,200 lines. Extract command handlers into `src/promux/commands/`. Fix the double-print bug and `_fetch_account_quota` 3-vs-4-tuple inconsistency. Remove compat shim from Phase 1. `cli.py` becomes ~250 lines.

---

### Task 4.1: Scaffold `commands/` package and extract profile commands

**Files:**
- Create: `src/promux/commands/__init__.py`
- Create: `src/promux/commands/profile.py`
- Modify: `src/promux/cli.py`

**Interfaces:**
- Produces:
  - `commands.profile.cmd_list(storage: StorageEngine, json_out: bool) -> int`
  - `commands.profile.cmd_save(storage, name, email, json_out) -> int`
  - `commands.profile.cmd_switch(storage, name, json_out, smart, model, failover, no_cache) -> int`
  - `commands.profile.cmd_remove(storage, name, json_out) -> int`
  - `commands.profile.cmd_whoami(storage, json_out) -> int`

- [ ] **Step 1: Create `commands/__init__.py`**

```python
"""Command handlers for promux CLI. One module per command group."""
```

- [ ] **Step 2: Create `commands/profile.py`**

Move `cmd_list`, `cmd_save`, `cmd_switch`, `cmd_remove`, `cmd_whoami` from `cli.py` into `commands/profile.py`. Each function keeps its exact signature. Add imports they need at the top of `profile.py`:

```python
"""Profile management commands: list, save, switch, remove, whoami."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from ..auth import get_or_refresh_access_token
from ..cache import QuotaCache
from ..failover import FailoverEngine
from ..formatters import format_relative_countdown
from ..models import AccountMeta
from ..quota import QuotaClient
from ..storage import StorageEngine

logger = logging.getLogger(__name__)
```

**Fix the double-print bug while moving `cmd_remove`:**

```python
def cmd_remove(storage: StorageEngine, name: str, json_out: bool) -> int:
    success = storage.remove_profile(name)
    if success:
        QuotaCache(storage.home).invalidate(name)
        if json_out:
            print(json.dumps({"success": True, "removed": name}, indent=2))
        else:
            print(f"Removed profile '{name}' from vault.")
        return 0
    else:
        if json_out:
            print(json.dumps({"success": False, "error": f"Account '{name}' not found"}, indent=2))
        else:                                          # ← was missing, causing double-print
            print(f"Error: Account '{name}' not found", file=sys.stderr)
        return 1
```

Apply the same `else:` fix to `cmd_switch` (the failure branch at the end) and anywhere else the pattern appears.

- [ ] **Step 3: Update `cli.py` to import from `commands.profile`**

Replace the function bodies in `cli.py` with re-exports or update dispatch in `main()` to call `commands.profile.cmd_list(...)` etc. The cleanest approach: delete the function bodies from `cli.py` and add to the dispatch section:

```python
from .commands.profile import cmd_list, cmd_save, cmd_switch, cmd_remove, cmd_whoami
```

- [ ] **Step 4: Run full suite**

```bash
pytest -x -q
```
Expected: All 230 pass.

- [ ] **Step 5: Commit**

```bash
git add src/promux/commands/ src/promux/cli.py
git commit -m "refactor: extract profile commands into commands/profile.py

Moves cmd_list, cmd_save, cmd_switch, cmd_remove, cmd_whoami.
Fixes double-print bug in cmd_remove and cmd_switch (missing else branch
caused stderr print even when json_out=True)."
```

---

### Task 4.2: Extract quota commands, fix `_fetch_account_quota` return type

**Files:**
- Create: `src/promux/commands/quota.py`
- Modify: `src/promux/cli.py`

**Interfaces:**
- Produces:
  - `commands.quota.cmd_quota(storage, name, json_out, no_cache) -> int`
  - `commands.quota.cmd_refresh(storage, name, force, json_out) -> int`
  - (private) `_fetch_account_quota(storage, target_name, no_cache, cache) -> tuple[QuotaSummary | None, str | None, str | None, bool]`

- [ ] **Step 1: Create `commands/quota.py` with standardized `_fetch_account_quota`**

Move `_fetch_account_quota`, `_refresh_account_token`, `cmd_quota`, `cmd_refresh` from `cli.py` into `commands/quota.py`.

**Standardize `_fetch_account_quota` to always return a 4-tuple:**

```python
def _fetch_account_quota(
    storage: StorageEngine,
    target_name: str,
    no_cache: bool = False,
    cache: QuotaCache | None = None,
) -> tuple[QuotaSummary | None, str | None, str | None, bool]:
    """Fetch quota for an account.

    Returns:
        (quota_summary, project_id, error_message, is_cached)
        error_message is None on success; quota_summary is None on failure.
    """
    # ... move body verbatim from cli.py ...
    # The function always returns (qs, project_id, None, False) or (None, ..., err, False)
    # or (cached_qs, project_id, None, True)
```

**Delete all `len(res) == 4` guards and `TypeError` shims from callers.** Now that the return type is always a 4-tuple, this code is:

```python
# BEFORE (defensive guards):
try:
    res = _fetch_account_quota(storage, name, no_cache=no_cache, cache=cache)
except TypeError:
    res = _fetch_account_quota(storage, name)
if len(res) == 4:
    qs, project_id, err, is_cached = res
else:
    qs, project_id, err = res[0], res[1], res[2]
    is_cached = False

# AFTER:
qs, project_id, err, is_cached = _fetch_account_quota(storage, name, no_cache=no_cache, cache=cache)
```

Also **fix the double-print in `cmd_quota`** (L808–810): add `else:` before the stderr print.

- [ ] **Step 2: Move `_format_expiry_table` to `formatters.py`**

```python
# formatters.py — append this function
def format_expiry_table(expiry_str: str | None) -> str:
    """Format an ISO timestamp to 'YYYY-MM-DD HH:MM:SS' for table display."""
    # ... copy verbatim from cli.py _format_expiry_table ...
```

Update callers in `commands/quota.py` to call `from ..formatters import format_expiry_table`.

- [ ] **Step 3: Update `cli.py`**

Import from `commands.quota`:
```python
from .commands.quota import cmd_quota, cmd_refresh
```

- [ ] **Step 4: Run full suite**

```bash
pytest -x -q
```

- [ ] **Step 5: Commit**

```bash
git add src/promux/commands/quota.py src/promux/formatters.py src/promux/cli.py
git commit -m "refactor: extract quota commands, standardize _fetch_account_quota return type

- _fetch_account_quota always returns 4-tuple (removes 5 TypeError shims
  and all len(res)==4 guards)
- _format_expiry_table moved to formatters.py
- Fixes double-print in cmd_quota failure branch"
```

---

### Task 4.3: Extract remaining commands and slim `cli.py` to dispatcher

**Files:**
- Create: `src/promux/commands/watch.py`
- Create: `src/promux/commands/tools.py`
- Modify: `src/promux/cli.py`

**Interfaces:**
- Produces:
  - `commands.watch.cmd_watch(failover, poll_seconds, cooldown) -> int`
  - `commands.tools.cmd_tools(registry, json_out) -> int`
  - `commands.tools.cmd_completion(shell) -> int`

- [ ] **Step 1: Create `commands/watch.py`**

```python
"""Watch daemon command."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..failover import FailoverEngine
from ..logging_config import configure_daemon_logging
from ..watch import LogMatch, LogWatcher

logger = logging.getLogger(__name__)


def cmd_watch(failover: FailoverEngine, poll_seconds: float, cooldown: int | None) -> int:
    configure_daemon_logging()

    gemini_home = None
    if "PROMUX_GEMINI_HOME" in os.environ:
        gemini_home = Path(os.environ["PROMUX_GEMINI_HOME"])
    elif hasattr(failover, "storage") and hasattr(failover.storage, "gemini_home"):
        gemini_home = failover.storage.gemini_home

    watcher = LogWatcher(failover=failover, poll_seconds=poll_seconds, gemini_home=gemini_home)

    def on_match(match: LogMatch) -> None:
        logger.warning("Quota exhausted: %s in %s", match.pattern, match.file_path)
        if match.reset_hint:
            logger.info("Reset hint detected: %s", match.reset_hint)

    cool_str = f"{cooldown}m" if cooldown else "auto"
    logger.info("Starting log watcher (poll: %ss, cooldown: %s)", poll_seconds, cool_str)
    print("[promux-watch] Starting log watcher (press Ctrl+C to stop).", file=__import__("sys").stderr)

    try:
        watcher.run_forever(on_match=on_match, cooldown_minutes=cooldown)
    except KeyboardInterrupt:
        pass
    logger.info("Watcher stopped.")
    return 0
```

- [ ] **Step 2: Create `commands/tools.py`**

```python
"""Tool registry and shell completion commands."""

from __future__ import annotations

import json
import sys

from ..adapters import ToolRegistry
from ..completion import generate_bash_completion, generate_zsh_completion

# Move cmd_tools and cmd_completion bodies verbatim from cli.py
```

- [ ] **Step 3: Remove Phase 1 compat shim from `cli.py`**

Delete the `from .auth import (... as _read_token_data, ...)` compatibility shim added in Task 1.4. By now, all command handlers have been moved to `commands/` and import directly from `auth`. Update any remaining references in `cli.py`.

- [ ] **Step 4: Slim `cli.py` to ~250 lines**

`cli.py` should now contain only:
1. Imports (`from .commands.*` etc.)
2. `COMMAND_CAPABILITIES` dict
3. `build_parser()` function
4. `main()` function

If any command handler bodies are still in `cli.py`, move them now.

- [ ] **Step 5: Run full suite**

```bash
pytest -x -q
ruff check src tests
mypy src
```

- [ ] **Step 6: Verify `cli.py` line count**

```bash
wc -l src/promux/cli.py
```
Expected: ≤300 lines.

- [ ] **Step 7: Commit and merge**

```bash
git add src/promux/commands/ src/promux/cli.py
git commit -m "refactor: extract watch/tools commands, remove Phase 1 compat shim

cli.py is now ~250 lines: build_parser() + main() dispatch only.
All command logic lives in commands/ package."
git push -u origin phase/4-commands-package
# Open PR, merge
git checkout main && git pull
```

---

## PHASE 5 — Housekeeping & Agentic Friendliness

**Branch:** `phase/5-housekeeping`  
**Prerequisite:** All previous phases merged. (Can start Phase 5 earlier for doc/AGENTS.md tasks.)

---

### Task 5.1: Single-source version with `setuptools-scm`

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/promux/__init__.py`
- Modify: `src/promux/version.py`
- Modify: `.gitignore`

- [ ] **Step 1: Update `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68", "setuptools-scm>=8.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "promux"
# Remove: version = "0.4.0"
dynamic = ["version"]

[tool.setuptools-scm]
version_file = "src/promux/_version.py"
```

- [ ] **Step 2: Update `__init__.py`**

```python
"""Promux: Antigravity CLI profile multiplexer & quota failover daemon."""

import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())

try:
    from ._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"
```

- [ ] **Step 3: Update `version.py` for lazy resolution**

```python
"""Version resolution — lazy to avoid startup overhead."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def get_version() -> str:
    """Return the installed package version, resolved lazily."""
    try:
        from ._version import __version__
        return __version__
    except ImportError:
        pass
    try:
        from importlib.metadata import version, PackageNotFoundError
        return version("promux")
    except Exception:
        return "0.0.0+unknown"


def cmd_version(json_out: bool = False, no_color: bool = False) -> int:
    """Print promux version. Existing function — update to use get_version()."""
    v = get_version()
    if json_out:
        print(json.dumps({"version": v}))
    else:
        print(f"promux {v}")
    return 0
```

- [ ] **Step 4: Add `_version.py` to `.gitignore`**

```bash
echo "src/promux/_version.py" >> .gitignore
```

- [ ] **Step 5: Tag and verify**

```bash
# If no tag exists for current version:
git tag v0.4.0
pip install -e ".[dev]"
python -c "import promux; print(promux.__version__)"
```
Expected: `0.4.0` or `0.4.0.devN+gHASH`.

- [ ] **Step 6: Run full suite**

```bash
pytest -x -q
ruff check src tests
mypy src
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/promux/__init__.py src/promux/version.py .gitignore
git commit -m "chore: single-source version via setuptools-scm + git tags

Removes hardcoded version from __init__.py and pyproject.toml.
Version now comes from git tags. _version.py is auto-generated at build time."
```

---

### Task 5.2: Fix `constants.py` import-time side effects

**Files:**
- Modify: `src/promux/constants.py`

- [ ] **Step 1: Replace eager `PROMUX_HOME` with module `__getattr__`**

At the top of `constants.py`, find:
```python
PROMUX_HOME = Path(os.environ.get("PROMUX_HOME", Path.home() / ".promux"))
```

Replace the entire eager evaluation with a lazy pattern using module `__getattr__` (PEP 562):

```python
# constants.py — lazy path resolution via module __getattr__

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# Note: PROMUX_HOME, ACCOUNTS_DIR, STATE_FILE, etc. are defined lazily via __getattr__
# to avoid Path.home() being called at import time. Use `from .constants import PROMUX_HOME`
# as before — the first access triggers evaluation and caches the result.

_LAZY_PATHS: dict[str, Any] = {}


def _build_lazy_paths() -> dict[str, Any]:
    home = Path(os.environ.get("PROMUX_HOME", Path.home() / ".promux"))
    gemini_home = Path(
        os.environ.get("PROMUX_GEMINI_HOME", Path.home() / ".gemini" / "antigravity-cli")
    )
    cache_dir = home / "cache"
    return {
        "PROMUX_HOME": home,
        "ACCOUNTS_DIR": home / "accounts",
        "STATE_FILE": home / "state.json",
        "LOCK_FILE": home / "manager.lock",
        "CONFIG_FILE": home / "config.json",
        "CACHE_DIR": cache_dir,
        "QUOTA_CACHE_FILE": cache_dir / "quota.json",
        "GEMINI_CLI_HOME": gemini_home,
        "LIVE_TOKEN": gemini_home / "antigravity-oauth-token",
        "CLI_LOG": gemini_home / "cli.log",
        "LOG_DIR": gemini_home / "log",
    }

_LAZY_NAMES = frozenset(_build_lazy_paths.__code__.co_consts)  # approximation; set explicitly:
_LAZY_NAMES = frozenset({
    "PROMUX_HOME", "ACCOUNTS_DIR", "STATE_FILE", "LOCK_FILE", "CONFIG_FILE",
    "CACHE_DIR", "QUOTA_CACHE_FILE", "GEMINI_CLI_HOME", "LIVE_TOKEN", "CLI_LOG", "LOG_DIR",
})


def __getattr__(name: str) -> Any:
    if name in _LAZY_NAMES:
        if not _LAZY_PATHS:
            _LAZY_PATHS.update(_build_lazy_paths())
        val = _LAZY_PATHS[name]
        globals()[name] = val  # cache in module namespace
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

Also move `DEFAULT_HTTP_TIMEOUT = get_default_http_timeout()` (line 102) behind the same lazy pattern.

- [ ] **Step 2: Run full suite**

```bash
pytest -x -q
ruff check src tests
mypy src
```

- [ ] **Step 3: Commit**

```bash
git add src/promux/constants.py
git commit -m "fix(constants): defer Path.home() and config.json reads to first access

Uses module __getattr__ (PEP 562) so import-time side effects are eliminated.
Speeds up CLI startup and fixes isolated test environments."
```

---

### Task 5.3: Lint cleanup (ruff)

**Files:**
- Modify: multiple files in `src/` and `tests/`

- [ ] **Step 1: Auto-fix import sorting and safe fixes**

```bash
ruff check --fix src tests
```

- [ ] **Step 2: Fix remaining violations manually**

Run `ruff check src tests` and fix each remaining violation:
- E501 (line too long): break the line at a logical boundary — use parentheses to wrap
- F401 (unused import): remove the import or add to `__all__`

- [ ] **Step 3: Verify zero violations**

```bash
ruff check src tests
```
Expected: no output (zero violations).

- [ ] **Step 4: Run full suite and typecheck**

```bash
pytest -x -q
mypy src
```

- [ ] **Step 5: Commit**

```bash
git add src/ tests/
git commit -m "chore: fix all 80 ruff violations (import sorting, line length, unused imports)"
```

---

### Task 5.4: Create `AGENTS.md` and update stale docs

**Files:**
- Create: `AGENTS.md`
- Modify: `SECURITY.md`
- Modify: `CONTRIBUTING.md`
- Modify: `README.md`

- [ ] **Step 1: Create `AGENTS.md`**

Create at repo root with the content from spec §7.4 (the full AGENTS.md template). Adapt any command examples to reflect the final state (e.g., `tests/test_auth.py` should exist, `commands/` package should exist).

- [ ] **Step 2: Update `SECURITY.md`**

Find the "Supported Versions" table. Change:
```markdown
| 0.1.x | :white_check_mark: |
```
To reflect the current supported release line (e.g., `0.4.x`). Mark older versions as `:x:`.

- [ ] **Step 3: Update `CONTRIBUTING.md`**

Find the development setup section. Add `uv` instructions alongside `pip`:

```markdown
### With pip (classic)
```bash
pip install -e ".[dev]"
```

### With uv (faster)
```bash
uv pip install -e ".[dev]"
# or
uv sync --extra dev
```
```

- [ ] **Step 4: Update `README.md`**

Find any section listing adapters (agy, claude, codex, cursor). Add a note:

```markdown
> **Note:** The `claude`, `codex`, and `cursor` adapters are planned and currently
> scaffolded as stubs. Only the `agy` adapter is fully implemented.
```

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md SECURITY.md CONTRIBUTING.md README.md
git commit -m "docs: add AGENTS.md, update stale SECURITY.md/CONTRIBUTING.md/README.md

AGENTS.md: universal agent-friendly project context (commands, architecture,
constraints, definition of done).
SECURITY.md: update supported versions to current release line.
CONTRIBUTING.md: add uv instructions alongside pip.
README.md: mark claude/codex/cursor adapters as planned stubs."
```

---

### Task 5.5: Establish coverage baseline; merge Phase 5

**Files:**
- Modify: `pyproject.toml` (optional: add `--cov-fail-under`)

- [ ] **Step 1: Run coverage report**

```bash
pytest --cov=promux --cov-report=term-missing -q
```

Note the coverage percentage.

- [ ] **Step 2: Optionally enforce coverage floor in CI**

If coverage is ≥75%, add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = "--cov=promux --cov-fail-under=75"
```

Adjust threshold to the actual baseline (round down 5%).

- [ ] **Step 3: Final smoke test**

```bash
promux --help
promux list
promux version
ruff check src tests
mypy src
pytest -v
```

- [ ] **Step 4: Commit and merge**

```bash
git add pyproject.toml
git commit -m "chore: establish coverage baseline and enforce floor in CI"
git push -u origin phase/5-housekeeping
# Open PR: "Phase 5: Housekeeping, AGENTS.md, single-source version, lint"
# Merge after CI passes
git checkout main && git pull
```

---

## Summary: Phase × Task Matrix

| Phase | Tasks | Key Deliverable | cli.py LOC |
|-------|-------|-----------------|-----------|
| Phase 1 | 1.1–1.4 | `auth.py` + circular dep fix | ~1,200 |
| Phase 2 | 2.1–2.3 | Cross-platform `lock.py` + Windows CI | ~1,200 |
| Phase 3 | 3.1–3.2 | Logging framework + silent swallow fix | ~1,200 |
| Phase 4 | 4.1–4.3 | `commands/` package | **~250** |
| Phase 5 | 5.1–5.5 | Version, lint, AGENTS.md, docs | ~250 |

**After all phases:** 230+ tests green, `ruff` clean, `mypy` clean, cross-platform, agent-friendly.
