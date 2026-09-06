# Multi-Profile Quota Display Design Specification

**Date:** 2026-09-06  
**Status:** Approved for implementation planning  
**Authors:** Antigravity & Alireza Opmc  

---

## 1. Objectives & Overview

Currently, `promux quota` without arguments only queries the active profile and outputs an isolated 4-row table that omits clear multi-account comparison.

This feature enhances `promux quota` into a dual-mode command:
1. **Overview Mode (`promux quota`)**: When invoked without an account name, fetches and displays a unified matrix table of all profiles in the vault, with active profile identification (`*`), individual quota columns (Gemini 5h/weekly, Claude 5h/weekly), and upcoming reset timestamps.
2. **Detail Mode (`promux quota <name>`)**: When invoked with a specific account name, displays the full granular breakdown for that account, including companion project ID and explicit window intervals.
3. **Resilient Error Isolation**: Stale tokens or authentication failures for one profile do not break the overall command; errors are isolated and reported inline (`[AUTH ERROR]` / `[ERROR]`).
4. **Structured JSON Output**:
   - `promux quota --json` returns a list of quota summary dictionaries for all accounts.
   - `promux quota <name> --json` returns a single quota summary dictionary for backward compatibility.

---

## 2. Table Layouts & Visual Contracts

### 2.1 Multi-Profile Matrix Table (`promux quota`)

```
ACTIVE  PROFILE           GEMINI (5H)   GEMINI (WK)   CLAUDE (5H)   CLAUDE (WK)   NEXT RESET (UTC)
---------------------------------------------------------------------------------------------------
*       main              96.1%         82.0%         100.0%        31.7%         16:58:03
        backup1           100.0%        95.0%         80.0%         60.0%         17:30:00
        personal          [AUTH ERROR]  -             -             -             -
```

#### Column Definitions:
- `ACTIVE` (width 7): `*` for current active account, otherwise blank.
- `PROFILE` (width 17): Name of the account profile.
- `GEMINI (5H)` (width 13): 5-hour window remaining percentage or error label.
- `GEMINI (WK)` (width 13): Weekly window remaining percentage or `-`.
- `CLAUDE (5H)` (width 13): 3rd-party models 5-hour window remaining percentage or `-`.
- `CLAUDE (WK)` (width 13): 3rd-party models weekly window remaining percentage or `-`.
- `NEXT RESET (UTC)`: Time string (HH:MM:SS or ISO suffix) of earliest upcoming reset among 5h windows, or `-`.

### 2.2 Single-Profile Detailed Table (`promux quota <name>`)

```
Quota for account 'main' (project: aicode-consumers) [ACTIVE]:

MODEL GROUP              WINDOW     REMAINING    RESET TIME
----------------------------------------------------------------------
Gemini Models            5h         96.1%        2026-09-06T16:58:03Z
Gemini Models            weekly     82.0%        2026-09-10T18:21:54Z
Claude & GPT Models      5h         100.0%       2026-09-06T17:03:07Z
Claude & GPT Models      weekly     31.7%        2026-09-09T01:11:22Z
```

---

## 3. Component Architecture & Data Flow

### 3.1 Modular Helper: `_fetch_account_quota`

```python
def _fetch_account_quota(
    storage: StorageEngine,
    target_name: str,
) -> tuple[QuotaSummary | None, str | None, str | None]:
    """Fetch quota for an account, handling token resolution, refresh, and project ID discovery.
    
    Returns:
        (quota_summary, project_id, error_message)
    """
```

1. **Token Resolution**:
   - If `target_name == storage.get_active_profile()` and `storage.live_token.exists()`: uses `storage.live_token`.
   - Otherwise: uses `storage.accounts_dir / target_name / "antigravity-oauth-token"`.
2. **Token Refresh**:
   - Calls `_get_or_refresh_access_token(token_path)`.
   - Handles HTTP 401 retry by triggering `_refresh_token_file`.
3. **Project Metadata**:
   - Reads `acct.project_id`. If missing, calls `client.load_metadata()` and updates `state.json`.
4. **Error Isolation**:
   - Catches token, project, or API errors and returns descriptive error string instead of raising uncaught exceptions.

### 3.2 Dual-Mode Command Dispatcher: `cmd_quota`

- **If `name` is provided**:
  - Validates account exists in vault.
  - Fetches quota via `_fetch_account_quota`.
  - If error: outputs error (JSON `{"error": msg}` or stderr) and returns 1.
  - If success: renders single-account detailed table or JSON object.
- **If `name` is None**:
  - Retrieves all accounts via `storage.list_accounts()`.
  - If no accounts exist: returns error message.
  - Iterates through each account and collects `(acct_name, is_active, qs, project_id, err_msg)`.
  - In JSON mode: prints JSON array of account quota objects.
  - In Text mode: renders multi-profile matrix table. Returns 0.

---

## 4. Testing & Quality Assurance Plan

1. **Unit Tests (`tests/test_cli.py`)**:
   - `test_cli_quota_all_profiles_matrix_table`: Verifies `promux quota` without arguments renders the unified matrix table with `*` marker, account names, and percentage columns.
   - `test_cli_quota_all_profiles_json`: Verifies `promux quota --json` outputs a list of account objects containing `account`, `active`, `gemini`, `third_party`.
   - `test_cli_quota_single_profile_detail`: Verifies `promux quota <name>` maintains the 4-row detailed table and single JSON object contract.
   - `test_cli_quota_all_profiles_with_isolated_error`: Verifies that if one account has an invalid token or network error, other accounts still render cleanly with an error tag for the failing account.
2. **Static Quality**:
   - Passes `ruff check src tests` with 0 warnings.
   - Passes `ruff format --check src tests`.
   - Passes `mypy src` with 0 type errors.
   - Passes full test suite (`pytest -v`).

---

## 5. Acceptance Criteria

- [x] Multi-profile matrix table format approved.
- [ ] `_fetch_account_quota` implemented with error isolation and token refresh.
- [ ] `cmd_quota` updated to support dual-mode (all-profile matrix vs single-profile breakdown).
- [ ] JSON output structured as array for all-profile and dict for single-profile.
- [ ] Comprehensive unit tests added in `tests/test_cli.py`.
- [ ] `README.md` updated with new `promux quota` output example.
- [ ] 100% test pass rate with full Ruff and Mypy compliance.
