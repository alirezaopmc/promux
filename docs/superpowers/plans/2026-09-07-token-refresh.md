# Automated Token Refresh & Auth Renewal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement resilient two-tier automated OAuth token refresh and renewal (Tier 1: Native HTTP OAuth refresh with Antigravity credentials; Tier 2: Headless `agy` subprocess fallback) across `promux quota`, `promux switch`, new `promux refresh` CLI subcommand, and proactive background maintenance in `promux watch`.

**Architecture:** Default to Antigravity CLI's official Google OAuth2 client credentials for fast, native (~200ms) direct HTTP token refreshing without subprocesses. If native refresh encounters an unexpected failure, fall back to safely staging the token and triggering a headless `agy` probe under the `manager.lock` mutex. Expose this renewal logic on-demand in `quota` and `switch`, via a new `promux refresh` command, and periodically in `promux watch`.

**Tech Stack:** Python 3.10+ (Standard Library: `urllib.request`, `urllib.parse`, `json`, `subprocess`, `shutil`, `datetime`, `pathlib`, `argparse`), `pytest`.

## Global Constraints
- Zero external dependencies beyond Python standard library.
- Atomic file writes for all token manipulations (`.tmp` file with `0600` permissions replaced via `os.replace`).
- Strict single-active account model: live token at `~/.gemini/antigravity-cli/antigravity-oauth-token` must be preserved/restored during any fallback operations.
- Full backwards compatibility with `PROMUX_OAUTH_CLIENT_ID`, `PROMUX_OAUTH_CLIENT_SECRET`, and `~/.promux/oauth.json`.

---

### Task 1: Add Default Antigravity OAuth Credentials and Buffer Constants

**Files:**
- Modify: `src/promux/constants.py:27-32`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Environment variables `PROMUX_OAUTH_CLIENT_ID`, `PROMUX_OAUTH_CLIENT_SECRET`.
- Produces: `DEFAULT_OAUTH_CLIENT_ID`, `DEFAULT_OAUTH_CLIENT_SECRET`, `DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS`, `DEFAULT_PROACTIVE_REFRESH_INTERVAL_SECONDS`.

- [ ] **Step 1: Write the failing test**
In `tests/test_cli.py`, add `test_constants_default_credentials`:
```python
def test_constants_default_credentials(monkeypatch):
    import importlib
    import promux.constants

    monkeypatch.delenv("PROMUX_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("PROMUX_OAUTH_CLIENT_SECRET", raising=False)
    importlib.reload(promux.constants)

    assert "apps.googleusercontent.com" in promux.constants.OAUTH_CLIENT_ID
    assert promux.constants.OAUTH_CLIENT_SECRET.startswith("GOCSPX-")
    assert promux.constants.DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS == 60
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_cli.py::test_constants_default_credentials -v`
Expected: FAIL with assertion error because `OAUTH_CLIENT_ID` is empty.

- [ ] **Step 3: Write minimal implementation in `src/promux/constants.py`**
```python
DEFAULT_OAUTH_CLIENT_ID = "REDACTED_OAUTH_CLIENT_ID"
DEFAULT_OAUTH_CLIENT_SECRET = "REDACTED_OAUTH_CLIENT_SECRET"

OAUTH_TOKEN_URL = os.environ.get("PROMUX_OAUTH_TOKEN_URL", "https://oauth2.googleapis.com/token")
OAUTH_CLIENT_ID = os.environ.get("PROMUX_OAUTH_CLIENT_ID", DEFAULT_OAUTH_CLIENT_ID)
OAUTH_CLIENT_SECRET = os.environ.get("PROMUX_OAUTH_CLIENT_SECRET", DEFAULT_OAUTH_CLIENT_SECRET)
DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS = 60
DEFAULT_PROACTIVE_REFRESH_INTERVAL_SECONDS = 900  # 15 minutes
```

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_cli.py::test_constants_default_credentials -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/constants.py tests/test_cli.py
git commit -m "feat(constants): configure default Antigravity OAuth client credentials"
```

---

### Task 2: Expiry Detection and Tier 1 Native OAuth Refresh

**Files:**
- Modify: `src/promux/cli.py:93-155`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `token_data`, `token_path: Path`.
- Produces: `_is_token_expired(token_data: dict[str, Any] | None, buffer_seconds: int = 60) -> bool`, `_refresh_token_native(token_path: Path, token_data: dict[str, Any]) -> str | None`.

- [ ] **Step 1: Write failing tests**
In `tests/test_cli.py`, add:
```python
def test_is_token_expired_buffer():
    from datetime import datetime, timedelta, timezone
    from promux.cli import _is_token_expired

    now = datetime.now(timezone.utc)
    # Token expiring in 30s is expired under 60s buffer
    tok_near = {"token": {"expiry": (now + timedelta(seconds=30)).isoformat()}}
    assert _is_token_expired(tok_near, buffer_seconds=60) is True

    # Token expiring in 120s is NOT expired under 60s buffer
    tok_far = {"token": {"expiry": (now + timedelta(seconds=120)).isoformat()}}
    assert _is_token_expired(tok_far, buffer_seconds=60) is False

    # Missing or invalid expiry
    assert _is_token_expired({}) is True
    assert _is_token_expired({"token": {}}) is True


def test_native_refresh_updates_refresh_token_if_provided(tmp_path, monkeypatch):
    from promux.cli import _refresh_token_native

    token_path = tmp_path / "antigravity-oauth-token"
    token_data = {
        "token": {
            "access_token": "old_acc",
            "refresh_token": "old_refresh",
            "expiry": "2020-01-01T00:00:00Z",
        },
        "auth_method": "consumer",
    }
    token_path.write_text(json.dumps(token_data))

    class MockResp:
        def read(self):
            return json.dumps(
                {
                    "access_token": "new_acc",
                    "refresh_token": "rotated_refresh",
                    "expires_in": 3600,
                }
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=10: MockResp())

    new_acc = _refresh_token_native(token_path, token_data)
    assert new_acc == "new_acc"

    saved = json.loads(token_path.read_text())
    assert saved["token"]["access_token"] == "new_acc"
    assert saved["token"]["refresh_token"] == "rotated_refresh"
    assert saved["auth_method"] == "consumer"
```

- [ ] **Step 2: Run tests to verify failure**
Run: `pytest tests/test_cli.py::test_is_token_expired_buffer tests/test_cli.py::test_native_refresh_updates_refresh_token_if_provided -v`
Expected: FAIL (functions not defined)

- [ ] **Step 3: Implement `_is_token_expired` and `_refresh_token_native` in `src/promux/cli.py`**
Implement expiry calculation with buffer, and direct OAuth POST updating `access_token`, `expiry`, and rotated `refresh_token`. Ensure atomic write to `.tmp` with `0600` permissions.

- [ ] **Step 4: Run tests to verify they pass**
Run: `pytest tests/test_cli.py::test_is_token_expired_buffer tests/test_cli.py::test_native_refresh_updates_refresh_token_if_provided -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/cli.py tests/test_cli.py
git commit -m "feat(cli): add token expiry buffer check and native refresh with rotation"
```

---

### Task 3: Tier 2 Headless `agy` Fallback Renewal

**Files:**
- Modify: `src/promux/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `token_path: Path`, `storage: StorageEngine`.
- Produces: `_refresh_token_fallback_agy(token_path: Path, storage: StorageEngine) -> str | None`.
- Modifies: `_refresh_token_file(token_path: Path, token_data: dict[str, Any], storage: StorageEngine | None = None) -> str | None` to execute Tier 1 native, falling back to Tier 2 `agy`.

- [ ] **Step 1: Write failing tests**
In `tests/test_cli.py`, add `test_refresh_token_fallback_agy_success`:
```python
def test_refresh_token_fallback_agy_success(tmp_path, monkeypatch):
    import subprocess
    from promux.cli import _refresh_token_fallback_agy, get_storage

    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = get_storage()
    live_token = storage.live_token
    live_token.write_text(json.dumps({"token": {"access_token": "original_live"}}))

    target_dir = storage.accounts_dir / "target_acct"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_token = target_dir / "antigravity-oauth-token"
    target_token.write_text(json.dumps({"token": {"access_token": "target_old", "expiry": "2020-01-01T00:00:00Z"}}))

    # Mock shutil.which to say 'agy' exists
    monkeypatch.setattr("shutil.which", lambda cmd: "/mock/bin/agy" if cmd == "agy" else None)

    # Mock subprocess.run to simulate agy updating the staged live token
    def mock_run(cmd, capture_output=True, timeout=10, check=False):
        # Simulate agy refreshing the live token file
        live_token.write_text(json.dumps({"token": {"access_token": "target_renewed_by_agy", "expiry": "2030-01-01T00:00:00Z"}}))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"gemini-3.8-flash", stderr=b"")

    monkeypatch.setattr("subprocess.run", mock_run)

    new_acc = _refresh_token_fallback_agy(target_token, storage)
    assert new_acc == "target_renewed_by_agy"

    # Verify target token in vault was updated
    vault_data = json.loads(target_token.read_text())
    assert vault_data["token"]["access_token"] == "target_renewed_by_agy"

    # Verify original live token was restored!
    live_data = json.loads(live_token.read_text())
    assert live_data["token"]["access_token"] == "original_live"
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_cli.py::test_refresh_token_fallback_agy_success -v`
Expected: FAIL

- [ ] **Step 3: Implement `_refresh_token_fallback_agy` and wire up `_refresh_token_file`**
In `src/promux/cli.py`:
Implement staging under file lock (`storage.lock_file`), running `agy models` with 10s timeout, saving renewed token to target path, and restoring live token.
Update `_refresh_token_file`:
```python
def _refresh_token_file(token_path: Path, token_data: dict[str, Any], storage: StorageEngine | None = None) -> str | None:
    # Tier 1: Native HTTP refresh
    refreshed = _refresh_token_native(token_path, token_data)
    if refreshed:
        return refreshed
    # Tier 2: Headless agy fallback
    if storage is not None:
        return _refresh_token_fallback_agy(token_path, storage)
    return None
```

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_cli.py::test_refresh_token_fallback_agy_success -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/cli.py tests/test_cli.py
git commit -m "feat(cli): add Tier 2 headless agy fallback renewal"
```

---

### Task 4: Implement `promux refresh` CLI Subcommand

**Files:**
- Modify: `src/promux/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `cmd_refresh(storage: StorageEngine, name: str | None, force: bool, json_out: bool) -> int`.
- Adds `refresh` parser to `build_parser()`.

- [ ] **Step 1: Write failing tests**
In `tests/test_cli.py`, add:
```python
def test_cli_refresh_single_and_all(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main

    assert main(["save", "acct1", "--email", "a1@test.com"]) == 0
    assert main(["save", "acct2", "--email", "a2@test.com"]) == 0
    capsys.readouterr()

    # Mock native refresh
    monkeypatch.setattr("promux.cli._refresh_token_native", lambda p, td: "refreshed_acc")

    # Refresh specific with --force
    rc = main(["refresh", "acct1", "--force"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "acct1" in out
    assert "REFRESHED" in out

    # Refresh all with --json
    rc = main(["refresh", "--force", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert len(data) == 2
    assert all(d["status"] == "REFRESHED" for d in data)
```

- [ ] **Step 2: Run tests to verify failure**
Run: `pytest tests/test_cli.py::test_cli_refresh_single_and_all -v`
Expected: FAIL (invalid choice: 'refresh')

- [ ] **Step 3: Implement `cmd_refresh` and add CLI parser**
In `src/promux/cli.py`:
- Add `cmd_refresh` displaying formatted table or JSON array.
- Add `refresh` subcommand to `build_parser()` with `name` (optional), `--force` (`store_true`), and `--json`.
- Wire `args.command == "refresh"` in `main()`.

- [ ] **Step 4: Run tests to verify they pass**
Run: `pytest tests/test_cli.py::test_cli_refresh_single_and_all -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'promux refresh' command with table and JSON modes"
```

---

### Task 5: Auto-Refresh in `promux switch`

**Files:**
- Modify: `src/promux/storage.py:113-155`
- Modify: `src/promux/cli.py:244-263`
- Test: `tests/test_storage.py`, `tests/test_cli.py`

**Interfaces:**
- Modifies: `StorageEngine.switch_profile(name: str) -> bool` to ensure `src_token` is refreshed if expired before hot-swapping into `live_token`.

- [ ] **Step 1: Write failing tests**
In `tests/test_storage.py`, add `test_switch_profile_auto_refreshes_expired_target`:
```python
def test_switch_profile_auto_refreshes_expired_target(tmp_path, sample_token_dict, monkeypatch):
    from datetime import datetime, timezone
    from promux.storage import StorageEngine

    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True, exist_ok=True)
    live_token = gemini_home / "antigravity-oauth-token"
    live_token.write_text(json.dumps(sample_token_dict))

    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    storage.save_profile("profile_a")

    # Profile B with expired token
    token_b = dict(sample_token_dict)
    token_b["token"] = {
        "access_token": "expired_b",
        "refresh_token": "ref_b",
        "expiry": "2020-01-01T00:00:00Z",
    }
    live_token.write_text(json.dumps(token_b))
    storage.save_profile("profile_b")

    # Switch to profile A
    storage.switch_profile("profile_a")

    # Mock refresh to return refreshed token for profile_b
    monkeypatch.setattr("promux.cli._get_or_refresh_access_token", lambda p, storage=None: "new_b_token")

    # Switch to profile B - should auto-refresh
    success = storage.switch_profile("profile_b")
    assert success is True
    live_data = json.loads(live_token.read_text())
    assert live_data["token"]["access_token"] == "new_b_token"
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_storage.py::test_switch_profile_auto_refreshes_expired_target -v`
Expected: FAIL (live_data["token"]["access_token"] == "expired_b")

- [ ] **Step 3: Implement auto-refresh during `switch_profile`**
In `src/promux/storage.py`, import `_get_or_refresh_access_token` and invoke it on `src_token` prior to swapping into `live_token`.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_storage.py::test_switch_profile_auto_refreshes_expired_target -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/storage.py tests/test_storage.py
git commit -m "feat(storage): auto-refresh expired profile token on switch"
```

---

### Task 6: Resilient Auto-Refresh in `promux quota`

**Files:**
- Modify: `src/promux/cli.py:341-402`
- Test: `tests/test_cli.py`

**Interfaces:**
- Modifies: `_fetch_account_quota(storage: StorageEngine, target_name: str)` to pass `storage` to renewal functions, handle buffer expiry, and sync live token if target is active.

- [ ] **Step 1: Write failing tests**
In `tests/test_cli.py`, add `test_quota_auto_refreshes_vault_and_syncs_live`:
```python
def test_quota_auto_refreshes_vault_and_syncs_live(tmp_path, sample_token_dict, monkeypatch, capsys):
    _setup_env(tmp_path, monkeypatch, sample_token_dict)
    from promux.cli import main, get_storage
    from promux.quota import QuotaClient

    # Save active profile
    assert main(["save", "live_acct"]) == 0
    storage = get_storage()

    # Expire the token
    live_data = json.loads(storage.live_token.read_text())
    live_data["token"]["expiry"] = "2020-01-01T00:00:00Z"
    storage.live_token.write_text(json.dumps(live_data))

    # Mock refresh to renew
    def mock_refresh_native(p, td):
        td["token"]["access_token"] = "refreshed_live_quota"
        td["token"]["expiry"] = "2030-01-01T00:00:00Z"
        p.write_text(json.dumps(td))
        return "refreshed_live_quota"

    monkeypatch.setattr("promux.cli._refresh_token_native", mock_refresh_native)

    # Mock QuotaClient get_quota
    qs = QuotaSummary()
    qs.gemini_5h_remaining = 0.95
    monkeypatch.setattr(QuotaClient, "get_quota", lambda s, p: qs)

    rc = main(["quota", "live_acct", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["gemini"]["5h_remaining"] == 0.95

    # Check that live token was updated
    updated_live = json.loads(storage.live_token.read_text())
    assert updated_live["token"]["access_token"] == "refreshed_live_quota"
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_cli.py::test_quota_auto_refreshes_vault_and_syncs_live -v`
Expected: FAIL

- [ ] **Step 3: Implement quota auto-refresh and live sync in `src/promux/cli.py`**
Pass `storage` into `_get_or_refresh_access_token` so fallback is available, sync refreshed token to `live_token` when `target_name == active_profile`, and retry once on 401.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_cli.py::test_quota_auto_refreshes_vault_and_syncs_live -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/cli.py tests/test_cli.py
git commit -m "feat(cli): enhance quota auto-refresh with two-tier renewal and live sync"
```

---

### Task 7: Proactive Token Maintenance in `promux watch`

**Files:**
- Modify: `src/promux/watch.py`
- Test: `tests/test_watch.py`

**Interfaces:**
- Consumes: `failover.storage: StorageEngine`.
- Produces: `LogWatcher.check_and_renew_tokens(expiry_threshold_seconds: int = 600) -> list[str]`.

- [ ] **Step 1: Write failing tests**
In `tests/test_watch.py`, add `test_watch_proactive_token_renewal`:
```python
def test_watch_proactive_token_renewal(tmp_path, monkeypatch):
    from promux.watch import LogWatcher
    from promux.failover import FailoverEngine
    from promux.storage import StorageEngine
    import json

    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True, exist_ok=True)
    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    failover = FailoverEngine(storage)

    # Create account expiring in 5 minutes
    acct_dir = storage.accounts_dir / "near_exp"
    acct_dir.mkdir(parents=True, exist_ok=True)
    tok_file = acct_dir / "antigravity-oauth-token"
    from datetime import datetime, timedelta, timezone
    tok_file.write_text(json.dumps({
        "token": {
            "access_token": "near_acc",
            "expiry": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        }
    }))
    state = storage.load_state()
    state["accounts"]["near_exp"] = {"name": "near_exp", "enabled": True}
    storage.save_state(state)

    watcher = LogWatcher(failover=failover, poll_seconds=0.1, gemini_home=gemini_home)

    monkeypatch.setattr("promux.cli._refresh_token_file", lambda p, td, s: "renewed_by_watch")

    renewed = watcher.check_and_renew_tokens(expiry_threshold_seconds=600)
    assert "near_exp" in renewed
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_watch.py::test_watch_proactive_token_renewal -v`
Expected: FAIL (AttributeError: 'LogWatcher' object has no attribute 'check_and_renew_tokens')

- [ ] **Step 3: Implement `check_and_renew_tokens` and scheduled trigger in `LogWatcher`**
In `src/promux/watch.py`:
- Implement `check_and_renew_tokens(self, expiry_threshold_seconds: int = 600) -> list[str]`
- In `run_forever()`, track `last_renew_check = time.time()` and invoke `check_and_renew_tokens` every 15 minutes.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_watch.py::test_watch_proactive_token_renewal -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/watch.py tests/test_watch.py
git commit -m "feat(watch): add proactive background token renewal in log watcher"
```

---

### Task 8: Full Test Suite and Live Verification

**Files:**
- Run full pytest test suite
- Execute live CLI commands

- [ ] **Step 1: Run full test suite**
Run: `pytest`
Expected: All tests PASS with zero regressions.

- [ ] **Step 2: Run live manual verification commands**
Execute:
1. `promux refresh --json`
2. `promux quota`
3. `promux whoami`
Verify that tokens renew cleanly and `promux quota` outputs valid numbers for all registered accounts with no `[AUTH ERROR]`.

- [ ] **Step 3: Update documentation and commit**
Update `README.md` and `CHANGELOG.md` with details of `promux refresh` and automated token renewal.
```bash
git add README.md CHANGELOG.md
git commit -m "docs: document automated token refresh and promux refresh command"
```
