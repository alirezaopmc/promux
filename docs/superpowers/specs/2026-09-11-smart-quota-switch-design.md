# Design Specification: Smart Quota-Aware Profile Switching (`promux switch --smart`)

> **Date:** 2026-09-11  
> **Target Version:** 0.3.0  
> **Status:** Approved  
> **Author:** Alireza Opmc & Antigravity  

---

## 1. Overview & Objectives

`promux` provides multi-account profile management and automated failover for AI developer CLIs. When rotating or switching accounts, users currently either:
1. Manually select an account (`promux switch <name>`) without knowing its remaining quota beforehand.
2. Rely on LRU standby rotation (`promux next`), which selects candidate accounts without checking whether the standby account has available API quota.

This feature introduces **smart quota-aware switching** directly under the `switch` command:
```bash
promux switch --smart [--model {gemini,claude,gpt}]
```
It queries live API quotas across candidate standby accounts, filters out exhausted profiles, ranks accounts by highest available quota, hot-swaps to the best candidate, and applies cooldowns intelligently.

---

## 2. Requirements & User Intent

1. **CLI Syntax:**
   - Extend `promux switch` with `--smart` and `--model`.
   - Positional `name` becomes optional when `--smart` is supplied:
     ```bash
     promux switch [name] [--smart] [--model {gemini,claude,gpt}]
     promux agy switch [name] [--smart] [--model {gemini,claude,gpt}]
     ```
   - If neither `name` nor `--smart` is passed, display an error and return exit code 2:
     `Error: must specify account name or pass --smart`
2. **Model Selection:**
   - `--model` flag defaults to `gemini`.
   - Supported values: `gemini`, `claude`, `gpt`.
   - Mappings:
     - `gemini` evaluates `gemini_5h_remaining` and `gemini_weekly_remaining`.
     - `claude` and `gpt` evaluate `third_party_5h_remaining` and `third_party_weekly_remaining` (the shared third-party model quota bucket in Cloud Code Assist).
3. **Candidate Filtering & Selection Strategy:**
   - Excludes the currently active account.
   - Evaluates only standby accounts in `AccountState.STANDBY` (enabled and not in active cooldown).
   - An account is **eligible** if and only if both `five_hour_remaining > 0.0` AND `weekly_remaining > 0.0`.
   - Accounts with `0.0` quota or query errors are disqualified.
   - **Ranking Metric:**
     - Primary: Highest 5-hour remaining quota (`five_hour_remaining` descending).
     - Secondary: Highest weekly remaining quota (`weekly_remaining` descending).
     - Tertiary (tie-breaker): Least-recently used (oldest `last_used_at` or `None` first).
4. **Departing Active Account Cooldown:**
   - If the active account being switched away from has exhausted quota (`five_hour <= 0.0` or `weekly <= 0.0` for the chosen model), it is placed into a cooldown quarantine (derived from its reset timestamp hint or defaulting to 60 minutes).
   - If the active account still has available quota (> 0% in both windows), it is **not** placed into cooldown and remains in clean `STANDBY` state.
5. **No Candidates Found:**
   - If no standby account has available quota, no switch occurs, a descriptive error message is output, and exit code 1 is returned.
6. **JSON Output Support:**
   - When `--json` is specified, outputs structured JSON with operation status, accounts, model, and remaining quota metrics.
7. **Release Management:**
   - Bump version to `0.3.0` across `src/promux/__init__.py`, `pyproject.toml`, `CHANGELOG.md`, `README.md`, and `SPEC.md`.
   - Create git commit and tag `v0.3.0`.

---

## 3. Architecture & Detailed Design

### 3.1 Data Models (`src/promux/models.py`)

Add or extend rotation models to capture quota metrics:

```python
@dataclass
class SmartRotationResult:
    success: bool
    from_account: str | None
    to_account: str | None
    model: str
    five_hour_remaining: float | None = None
    weekly_remaining: float | None = None
    reason: str = "smart"
    cooldown_until: datetime | None = None
    error: str | None = None
```

### 3.2 Failover Engine (`src/promux/failover.py`)

Extend `FailoverEngine` with:

```python
class FailoverEngine:
    ...
    def rotate_smart(
        self,
        quota_fetcher: Callable[[str], tuple[QuotaSummary | None, str | None, str | None]],
        model: str = "gemini",
    ) -> SmartRotationResult:
        """Find the standby account with highest available quota and hot-swap."""
```

#### Selection Flow:
1. Load state inside storage mutex/transaction.
2. Identify `active_name`.
3. Candidate accounts = `get_eligible_standby(exclude=active_name)`.
4. If no candidate accounts exist:
   return failure `SmartRotationResult(success=False, from_account=active_name, to_account=None, model=model, error="No eligible standby accounts")`.
5. For each candidate account:
   - Call `quota_fetcher(candidate.name)`.
   - Extract `five_hour` and `weekly` based on `model`.
   - If error or `five_hour <= 0.0` or `weekly <= 0.0`: discard candidate.
   - If valid, record candidate along with its remaining quota.
6. If no eligible candidates remain after quota check:
   return failure `SmartRotationResult(success=False, from_account=active_name, to_account=None, model=model, error=f"No standby accounts found with available quota for '{model}' models")`.
7. Sort eligible candidates by:
   - `five_hour` descending
   - `weekly` descending
   - `last_used_at` ascending (None first)
8. Pick `best_candidate = candidates[0]`.
9. Check active account's quota using `quota_fetcher(active_name)`:
   - If active account quota is exhausted (`<= 0.0`), apply cooldown until its reset timestamp (or default 60 minutes).
10. Execute `storage.switch_profile(best_candidate.name)`.
11. Update `best_candidate.last_used_at = datetime.now(timezone.utc)`.
12. Return `SmartRotationResult(success=True, from_account=active_name, to_account=best_candidate.name, model=model, five_hour_remaining=..., weekly_remaining=...)`.

### 3.3 CLI Interface (`src/promux/cli.py`)

1. **Parser Configuration:**
   ```python
   switch_p = sub.add_parser("switch", parents=[common_parser], help="Hot-swap to a named profile or auto-switch to best quota")
   switch_p.add_argument("name", nargs="?", default=None, help="Profile name to switch to (optional if --smart is set)")
   switch_p.add_argument("--smart", action="store_true", default=False, help="Automatically switch to the standby account with highest available quota")
   switch_p.add_argument("--model", choices=["gemini", "claude", "gpt"], default="gemini", help="Target model tier to evaluate for --smart (default: gemini)")
   ```

2. **Command Handler:**
   ```python
   def cmd_switch(
       storage: StorageEngine,
       name: str | None,
       smart: bool,
       model: str,
       json_out: bool,
       failover: FailoverEngine | None = None,
   ) -> int:
   ```
   - If `smart` is True:
     - Check tool capability: if tool does not support quota (e.g. scaffolded tools), fail with descriptive error.
     - Call `failover.rotate_smart(quota_fetcher=lambda acct: _fetch_account_quota(storage, acct), model=model)`.
     - Handle presentation for text and JSON.
   - Else:
     - If `name` is None: print error `Error: must specify account name or pass --smart` and return 2.
     - Normal manual hot-swap to `name`.

### 3.4 Help & Shell Completions

- Update `HELP_SECTIONS` and `COMMAND_DETAILS` in [`src/promux/help.py`](file:///home/opmc/Dev/promux/src/promux/help.py).
- Update argument flags in [`src/promux/completion.py`](file:///home/opmc/Dev/promux/src/promux/completion.py) so `switch` completes `--smart` and `--model`.

---

## 4. Testing Strategy

1. **Unit Tests (`tests/test_failover.py`):**
   - Candidate with highest 5h quota is chosen among multiple options.
   - Weekly quota tie-breaker when 5h quotas are identical.
   - LRU tie-breaker when both 5h and weekly quotas are identical.
   - Candidates with 0% 5h or 0% weekly quota are filtered out.
   - Departing active account gets cooldown only when exhausted; remains standby when positive quota remains.
   - Model switching: `--model claude` and `--model gpt` correctly read third-party bucket.
   - Handled cleanly when all candidates are exhausted.
2. **CLI Tests (`tests/test_cli.py`):**
   - `promux switch --smart` succeeds and hot-swaps to top candidate.
   - `promux switch --smart --model claude` selects candidate with highest Claude/GPT quota.
   - `promux switch` without name or `--smart` outputs error with code 2.
   - `--json` formatting for both success and failure cases.
   - Subcommand help displays `--smart` and `--model`.
3. **Regression Tests:**
   - Full test suite run (`pytest`) verifying all 185+ tests continue to pass.

---

## 5. Release Plan (v0.3.0)

1. Bump version to `0.3.0` in:
   - `src/promux/__init__.py`
   - `pyproject.toml`
2. Update documentation:
   - `CHANGELOG.md`: document `[0.3.0]` release features.
   - `README.md`: document `promux switch --smart` with examples.
   - `SPEC.md`: update CLI command matrix and descriptions.
3. Commit and Tag:
   - Commit: `feat(cli): add smart quota-aware switching via switch --smart`
   - Tag: `v0.3.0`
