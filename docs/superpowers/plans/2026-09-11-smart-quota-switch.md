# Smart Quota-Aware Profile Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `promux switch --smart [--model {gemini,claude,gpt}]` to automatically evaluate live API quotas across candidate standby accounts, select the account with highest available quota, hot-swap the active profile, and release version 0.3.0.

**Architecture:** Extend `FailoverEngine` with `rotate_smart()` which queries live quotas via a passed `quota_fetcher`, filters out 0%-quota accounts, ranks candidates primarily by 5-hour remaining fraction (with weekly remaining and LRU as tie-breakers), and conditionally quarantines the departed active account only if it was exhausted. Wire this into `cmd_switch()` in `cli.py`, update help and completion scripts, update documentation, and cut release `v0.3.0`.

**Tech Stack:** Python 3.10+ standard library (`dataclasses`, `datetime`, `argparse`, `json`, `pathlib`), pytest.

## Global Constraints

- Zero external runtime dependencies (strictly standard library).
- Default model is `gemini`; `claude` and `gpt` map to the shared third-party model quota bucket.
- Must verify `weekly_remaining > 0` and `five_hour_remaining > 0` for an account to be eligible.
- Candidate ranking: primary by `5h_remaining` descending, secondary by `weekly_remaining` descending, tertiary by `last_used_at` ascending (None first).
- Active account gets cooldown only if exhausted (`<= 0.0`); otherwise stays clean in standby.
- All existing tests (185+) must continue to pass without regressions.

---

### Task 1: Add `SmartRotationResult` Data Model

**Files:**
- Modify: `src/promux/models.py:100-106`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class SmartRotationResult(RotationResult):
      model: str = "gemini"
      five_hour_remaining: float | None = None
      weekly_remaining: float | None = None
  ```

- [ ] **Step 1: Write the failing test in `tests/test_models.py`**

```python
def test_smart_rotation_result_model():
    from promux.models import SmartRotationResult, RotationResult

    res = SmartRotationResult(
        success=True,
        from_account="main",
        to_account="backup1",
        reason="smart",
        model="gemini",
        five_hour_remaining=0.95,
        weekly_remaining=0.80,
    )
    assert isinstance(res, RotationResult)
    assert res.success is True
    assert res.from_account == "main"
    assert res.to_account == "backup1"
    assert res.model == "gemini"
    assert res.five_hour_remaining == 0.95
    assert res.weekly_remaining == 0.80
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -k test_smart_rotation_result_model`  
Expected: FAIL with `ImportError: cannot import name 'SmartRotationResult' from 'promux.models'`

- [ ] **Step 3: Implement `SmartRotationResult` in `src/promux/models.py`**

Append to `src/promux/models.py`:
```python
@dataclass
class SmartRotationResult(RotationResult):
    model: str = "gemini"
    five_hour_remaining: float | None = None
    weekly_remaining: float | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -k test_smart_rotation_result_model`  
Expected: PASS

- [ ] **Step 5: Commit changes**

```bash
git add src/promux/models.py tests/test_models.py
git commit -m "feat(models): add SmartRotationResult model"
```

---

### Task 2: Implement `rotate_smart` in `FailoverEngine`

**Files:**
- Modify: `src/promux/failover.py`
- Test: `tests/test_failover.py`

**Interfaces:**
- Consumes: `SmartRotationResult`, `QuotaSummary`, `AccountMeta`, `StorageEngine`
- Produces:
  ```python
  def rotate_smart(
      self,
      quota_fetcher: Callable[[str], tuple[QuotaSummary | None, str | None, str | None]],
      model: str = "gemini",
      default_cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
  ) -> SmartRotationResult:
      ...
  ```

- [ ] **Step 1: Write failing tests in `tests/test_failover.py`**

Add tests covering:
1. `test_rotate_smart_selects_highest_5h_quota`: Acc1 (5h: 0.5, wk: 0.8), Acc2 (5h: 0.9, wk: 0.8) &rarr; selects Acc2.
2. `test_rotate_smart_tiebreak_weekly_and_lru`: Acc1 (5h: 0.8, wk: 0.9), Acc2 (5h: 0.8, wk: 0.7) &rarr; selects Acc1.
3. `test_rotate_smart_filters_zero_quota`: Acc with 0.0 in 5h or weekly is excluded.
4. `test_rotate_smart_model_mapping`: `--model claude` and `--model gpt` inspect `third_party_*` quota.
5. `test_rotate_smart_active_cooldown_only_when_exhausted`: Active account with 0.0 quota gets cooldown; active account with >0 quota stays clean.
6. `test_rotate_smart_no_eligible_candidates`: Returns `success=False` with descriptive reason.

```python
def test_rotate_smart_selects_highest_5h_quota():
    from promux.models import QuotaSummary

    storage = FakeStorage()
    now = datetime.now(timezone.utc)
    storage.state["active"] = "acc_active"
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True, last_used_at=now).to_dict(),
        "acc_low": AccountMeta(name="acc_low", enabled=True, last_used_at=now - timedelta(hours=3)).to_dict(),
        "acc_high": AccountMeta(name="acc_high", enabled=True, last_used_at=now - timedelta(hours=2)).to_dict(),
    }

    quotas = {
        "acc_active": QuotaSummary(gemini_5h_remaining=0.0, gemini_weekly_remaining=0.5),
        "acc_low": QuotaSummary(gemini_5h_remaining=0.4, gemini_weekly_remaining=0.9),
        "acc_high": QuotaSummary(gemini_5h_remaining=0.9, gemini_weekly_remaining=0.9),
    }

    def fetcher(acct_name):
        return quotas.get(acct_name), "proj-1", None

    engine = FailoverEngine(storage)
    res = engine.rotate_smart(quota_fetcher=fetcher, model="gemini")

    assert res.success is True
    assert res.from_account == "acc_active"
    assert res.to_account == "acc_high"
    assert res.five_hour_remaining == 0.9
    assert res.weekly_remaining == 0.9
    assert storage.state["active"] == "acc_high"

    # Active account had 0.0 quota, so must be in cooldown
    active_meta = AccountMeta.from_dict(storage.state["accounts"]["acc_active"])
    assert active_meta.state == AccountState.COOLDOWN


def test_rotate_smart_active_account_retains_standby_if_quota_available():
    from promux.models import QuotaSummary

    storage = FakeStorage()
    now = datetime.now(timezone.utc)
    storage.state["active"] = "acc_active"
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True, last_used_at=now).to_dict(),
        "acc_other": AccountMeta(name="acc_other", enabled=True, last_used_at=now - timedelta(hours=1)).to_dict(),
    }

    quotas = {
        "acc_active": QuotaSummary(gemini_5h_remaining=0.5, gemini_weekly_remaining=0.5),
        "acc_other": QuotaSummary(gemini_5h_remaining=1.0, gemini_weekly_remaining=1.0),
    }

    def fetcher(acct_name):
        return quotas.get(acct_name), "proj-1", None

    engine = FailoverEngine(storage)
    res = engine.rotate_smart(quota_fetcher=fetcher, model="gemini")

    assert res.success is True
    assert res.to_account == "acc_other"
    # acc_active had 0.5 quota > 0, so should NOT be in cooldown
    active_meta = AccountMeta.from_dict(storage.state["accounts"]["acc_active"])
    assert active_meta.state == AccountState.STANDBY


def test_rotate_smart_third_party_models():
    from promux.models import QuotaSummary

    storage = FakeStorage()
    now = datetime.now(timezone.utc)
    storage.state["active"] = "acc_active"
    storage.state["accounts"] = {
        "acc_active": AccountMeta(name="acc_active", enabled=True).to_dict(),
        "acc1": AccountMeta(name="acc1", enabled=True).to_dict(),
        "acc2": AccountMeta(name="acc2", enabled=True).to_dict(),
    }

    # acc1 has high Gemini but 0 Claude; acc2 has high Claude
    quotas = {
        "acc_active": QuotaSummary(third_party_5h_remaining=0.0, third_party_weekly_remaining=0.1),
        "acc1": QuotaSummary(gemini_5h_remaining=1.0, third_party_5h_remaining=0.0),
        "acc2": QuotaSummary(gemini_5h_remaining=0.1, third_party_5h_remaining=0.8, third_party_weekly_remaining=0.9),
    }

    def fetcher(acct_name):
        return quotas.get(acct_name), "proj-1", None

    engine = FailoverEngine(storage)
    res = engine.rotate_smart(quota_fetcher=fetcher, model="claude")

    assert res.success is True
    assert res.to_account == "acc2"
    assert res.five_hour_remaining == 0.8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_failover.py -k rotate_smart`  
Expected: FAIL with `AttributeError: 'FailoverEngine' object has no attribute 'rotate_smart'`

- [ ] **Step 3: Implement `rotate_smart` in `src/promux/failover.py`**

In `src/promux/failover.py`:
- Import `SmartRotationResult` from `.models`.
- Add helper method `_parse_iso_reset_minutes(reset_iso: str | None) -> int`:
  Parses ISO 8601 string (e.g. `2026-09-11T20:22:00Z` or `...T...+00:00`), calculates difference from `datetime.now(timezone.utc)` in minutes. If <= 0 or invalid, returns `DEFAULT_COOLDOWN_MINUTES`.
- Implement `rotate_smart()`:
  1. Retrieve `eligible = self.get_eligible_standby(exclude=active_name)`.
  2. If not `eligible`: return `SmartRotationResult(success=False, from_account=active_name, to_account=None, model=model, reason="No eligible standby accounts")`.
  3. Query each candidate using `quota_fetcher(candidate.name)`.
  4. Determine `five_hour` and `weekly` from `QuotaSummary`:
     If `model.lower() == "gemini"`:
       `five_hour = qs.gemini_5h_remaining`, `weekly = qs.gemini_weekly_remaining`, `reset_time = qs.gemini_5h_reset`
     Else (`claude` / `gpt`):
       `five_hour = qs.third_party_5h_remaining`, `weekly = qs.third_party_weekly_remaining`, `reset_time = qs.third_party_5h_reset`
  5. Filter candidates: keep only those with `five_hour > 0.0` and `weekly > 0.0`.
  6. If no candidates pass: return `SmartRotationResult(success=False, from_account=active_name, to_account=None, model=model, reason=f"No standby accounts found with available quota for '{model}' models")`.
  7. Sort candidates:
     Key: `(-five_hour, -weekly, 0 if c.last_used_at is None else 1, c.last_used_at or datetime.min.replace(tzinfo=timezone.utc))`.
  8. Choose top candidate: `best = scored_candidates[0]`.
  9. Check active account's quota via `quota_fetcher(active_name)`:
     If `active_five_hour <= 0.0` or `active_weekly <= 0.0`:
       Calculate `cool_mins = self._parse_iso_reset_minutes(active_reset_time)`.
       Apply cooldown: `state["accounts"][active_name]["cooldown_until"] = (datetime.now(timezone.utc) + timedelta(minutes=cool_mins)).isoformat()`.
  10. Hot-swap profile: `self.storage.switch_profile(best.name)`.
  11. Set `best.last_used_at = datetime.now(timezone.utc)`.
  12. Return `SmartRotationResult(success=True, from_account=active_name, to_account=best.name, model=model, five_hour_remaining=best_five_hour, weekly_remaining=best_weekly, cooldown_until=cooldown_until)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_failover.py -k rotate_smart`  
Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `pytest tests/test_failover.py`  
Expected: PASS

- [ ] **Step 6: Commit changes**

```bash
git add src/promux/failover.py tests/test_failover.py
git commit -m "feat(failover): implement rotate_smart quota-aware candidate selection"
```

---

### Task 3: CLI Parser, Dispatch & Formatting for `switch --smart`

**Files:**
- Modify: `src/promux/cli.py`
- Modify: `src/promux/help.py`
- Modify: `src/promux/completion.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_help.py`

**Interfaces:**
- CLI Syntax:
  `promux switch [name] [--smart] [--model {gemini,claude,gpt}] [--json]`
- `cmd_switch(storage, name, smart, model, json_out, failover)`

- [ ] **Step 1: Write failing CLI tests in `tests/test_cli.py`**

Add tests:
1. `test_cmd_switch_smart_gemini_success`: verifies human-readable output includes profile name and remaining percentages.
2. `test_cmd_switch_smart_json_output`: verifies JSON structure has `success`, `from_account`, `to_account`, `model`, `quota`.
3. `test_cmd_switch_smart_unsupported_quota_tool`: verifies error if tool does not support quota.
4. `test_cmd_switch_missing_name_without_smart`: returns error code 2 when neither `name` nor `--smart` is passed.
5. `test_cmd_switch_smart_no_candidates`: returns error code 1 and error message when all accounts are exhausted.

```python
def test_cmd_switch_missing_name_without_smart(capsys):
    from promux.cli import main
    code = main(["switch"])
    assert code == 2
    captured = capsys.readouterr()
    assert "Error: must specify account name or pass --smart" in captured.err


def test_cmd_switch_smart_success(monkeypatch, tmp_path, capsys):
    from promux.cli import main
    from promux.models import QuotaSummary, AccountMeta
    from promux.storage import StorageEngine

    # setup storage with active and standby
    promux_home = tmp_path / "promux"
    gemini_home = tmp_path / "gemini"
    promux_home.mkdir()
    gemini_home.mkdir()
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    storage = StorageEngine(promux_home=promux_home / "tools" / "agy", gemini_home=gemini_home)
    (storage.accounts_dir / "acc1").mkdir(parents=True)
    (storage.accounts_dir / "acc1" / "antigravity-oauth-token").write_text('{"access_token":"t1"}')
    (storage.accounts_dir / "acc2").mkdir(parents=True)
    (storage.accounts_dir / "acc2" / "antigravity-oauth-token").write_text('{"access_token":"t2"}')
    storage.save_state({
        "active": "acc1",
        "accounts": {
            "acc1": AccountMeta(name="acc1", enabled=True).to_dict(),
            "acc2": AccountMeta(name="acc2", enabled=True).to_dict(),
        }
    })
    storage.live_token.write_text('{"access_token":"t1"}')

    # Mock _fetch_account_quota
    from promux import cli
    def mock_fetch(st, name):
        if name == "acc1":
            return QuotaSummary(gemini_5h_remaining=0.0, gemini_weekly_remaining=0.5), "p1", None
        return QuotaSummary(gemini_5h_remaining=0.9, gemini_weekly_remaining=0.85), "p2", None

    monkeypatch.setattr(cli, "_fetch_account_quota", mock_fetch)

    code = main(["switch", "--smart"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Switched active profile to 'acc2'" in captured.out
    assert "gemini 5h: 90.0%" in captured.out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -k "test_cmd_switch_smart"`  
Expected: FAIL (unrecognized arguments or argument parsing failure)

- [ ] **Step 3: Update `build_parser()` in `src/promux/cli.py`**

Modify `switch` parser:
```python
    switch_p = sub.add_parser("switch", parents=[common_parser], help="Hot-swap to a named profile or auto-switch to best quota")
    switch_p.add_argument("name", nargs="?", default=None, help="Profile name to switch to (optional if --smart is set)")
    switch_p.add_argument("--smart", action="store_true", default=False, help="Automatically switch to the standby account with highest available quota")
    switch_p.add_argument("--model", choices=["gemini", "claude", "gpt"], default="gemini", help="Target model tier to evaluate for --smart (default: gemini)")
```

- [ ] **Step 4: Update `cmd_switch()` and dispatch in `src/promux/cli.py`**

In `main()`:
- Check if `args.command == "switch"`:
  If `args.smart`:
    Validate tool capability: if `"quota" not in target_tool.list_capabilities()`:
      raise `RuntimeError(f"Tool '{target_tool.name}' does not support smart quota switching ('quota' capability required).")`
  If not `args.smart` and not `args.name`:
    print `Error: must specify account name or pass --smart`, return 2.
- Pass `smart=args.smart`, `model=args.model`, `failover=failover` into `cmd_switch()`.

In `cmd_switch()`:
```python
def cmd_switch(
    storage: StorageEngine,
    name: str | None,
    json_out: bool,
    smart: bool = False,
    model: str = "gemini",
    failover: FailoverEngine | None = None,
) -> int:
```
When `smart=True`:
- Call `res = failover.rotate_smart(quota_fetcher=lambda acct: _fetch_account_quota(storage, acct), model=model)`
- If `res.success`:
  - If `json_out`:
    print JSON `{"success": True, "from_account": res.from_account, "to_account": res.to_account, "model": res.model, "quota": {"5h_remaining": res.five_hour_remaining, "weekly_remaining": res.weekly_remaining}, "cooldown_until": res.cooldown_until.isoformat() if res.cooldown_until else None}`
  - Else:
    print user-friendly string with formatted percentages and optional cooldown notice.
  - Return 0
- If not `res.success`:
  - If `json_out`:
    print JSON `{"success": False, "error": res.reason}`
  - Else:
    print `Error: {res.reason}` to `stderr`
  - Return 1

- [ ] **Step 5: Update `help.py` and `completion.py`**

- In `src/promux/help.py`:
  Update `COMMAND_DETAILS["switch"]` to `"Hot-swap the active profile, or auto-switch to highest quota with --smart."`
- In `src/promux/completion.py`:
  Update `switch` command options to include `--smart` and `--model`.

- [ ] **Step 6: Run tests to verify CLI tests pass**

Run: `pytest tests/test_cli.py -k switch`  
Expected: PASS

- [ ] **Step 7: Run all help and completion tests**

Run: `pytest tests/test_help.py tests/test_completion.py`  
Expected: PASS

- [ ] **Step 8: Commit changes**

```bash
git add src/promux/cli.py src/promux/help.py src/promux/completion.py tests/test_cli.py
git commit -m "feat(cli): add --smart and --model flags to promux switch"
```

---

### Task 4: Documentation, Version Bump to 0.3.0 & Tagging

**Files:**
- Modify: `src/promux/__init__.py:3`
- Modify: `pyproject.toml:7`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `SPEC.md`
- Test: `tests/test_version.py`

**Interfaces:**
- `__version__ = "0.3.0"`
- `promux version` outputs `0.3.0`

- [ ] **Step 1: Update version in code and config**

- `src/promux/__init__.py`: `__version__ = "0.3.0"`
- `pyproject.toml`: `version = "0.3.0"`

- [ ] **Step 2: Update `CHANGELOG.md`**

Add entry:
```markdown
## [0.3.0] - 2026-09-11

### Added
- Smart quota-aware profile switching via `promux switch --smart [--model {gemini,claude,gpt}]`.
- Automated candidate evaluation querying live Cloud Code Assist API quotas across standby profiles.
- Model tier filtering supporting `gemini` (default) and third-party models (`claude`, `gpt`).
- Smart ranking metric prioritizing 5-hour available quota fraction, weekly quota, and LRU tie-breaker.
- Conditional active profile cooldown: quarantines departed account only if its quota is exhausted.
```

- [ ] **Step 3: Update `README.md` and `SPEC.md`**

- In `README.md`: add documentation for `promux switch --smart` under CLI Command Reference and Quick Start.
- In `SPEC.md`: update the CLI Command Reference matrix and add Section on Smart Quota Switching.

- [ ] **Step 4: Run test suite to verify version and all tests pass**

Run: `pytest`  
Expected: 190+ tests passed.

- [ ] **Step 5: Verify CLI version command**

Run: `python -m promux.cli version`  
Expected: Displays `promux 0.3.0`

- [ ] **Step 6: Commit and Tag**

```bash
git add src/promux/__init__.py pyproject.toml CHANGELOG.md README.md SPEC.md
git commit -m "chore(release): bump version to 0.3.0"
git tag -a v0.3.0 -m "Release v0.3.0: Smart quota-aware profile switching"
```
