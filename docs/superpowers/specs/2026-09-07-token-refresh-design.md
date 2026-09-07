# Automated Token Refresh & Auth Renewal Design Specification

**Date:** 2026-09-07  
**Status:** Approved for implementation planning  
**Authors:** Antigravity & Alireza Opmc  

---

## 1. Objectives & Overview

Currently, `promux` accounts experience authentication failures when access tokens expire (typically after 1 hour). When tokens expire:
- `promux quota` fails with `[AUTH ERROR]` (`401 Unauthorized`) because `_refresh_token_file()` lacks default Google OAuth client credentials.
- Running `agy` temporarily fixes only the active profile because `agy` operates exclusively on `~/.gemini/antigravity-cli/antigravity-oauth-token`, leaving all standby vault profiles in `~/.promux/accounts/<name>/` unrefreshed.
- Switching to an expired standby account causes immediate authentication errors in downstream tools.

This feature implements an end-to-end, resilient **Two-Tier Token Renewal Engine**:
1. **Tier 1 (Primary — Native OAuth2 Refresh)**: Direct HTTP POST to `https://oauth2.googleapis.com/token` using Antigravity's official OAuth client credentials. Refreshes tokens in ~200ms without subprocess overhead for any profile in the vault.
2. **Tier 2 (Secondary Fallback — Headless `agy` Probe)**: If native refresh fails (e.g. unexpected network error or Google endpoint credential updates), Promux safely stages the token, triggers a non-interactive probe (`agy models`), retrieves the refreshed token, and restores the active session.
3. **On-Demand Renewal in Quota & Switch**:
   - `promux quota` automatically checks expiry before querying backend APIs and renews expired accounts transparently.
   - `promux switch` guarantees that an account is renewed before hot-swapping into the active path.
4. **Dedicated CLI Command (`promux refresh`)**:
   - `promux refresh` to renew all accounts in the vault.
   - `promux refresh <name>` to renew a specific account.
   - `--force` to renew immediately even if not expired.
   - `--json` for automation and script integration.
5. **Proactive Renewal in `promux watch`**:
   - Background daemon periodically inspects account tokens (every 15 minutes) and renews any token within 10 minutes of expiry, preventing accounts from ever expiring while the watcher is running.

---

## 2. Research & Root Cause Findings

### 2.1 Credential Discovery
Disassembly of `/home/opmc/.local/bin/agy` revealed Antigravity CLI's official OAuth 2.0 client credentials:
- **Client ID**: `REDACTED_OAUTH_CLIENT_ID`
- **Client Secret**: `REDACTED_OAUTH_CLIENT_SECRET`
- **Token Endpoint**: `https://oauth2.googleapis.com/token`

Live verification confirmed that Google's OAuth2 service accepts refresh tokens with these credentials and returns fresh `access_token`, `expires_in` (3600s), and `token_type: Bearer`.

### 2.2 Token JSON Structure
The stored token files match Go's `golang.org/x/oauth2.Token` JSON schema:
```json
{
  "token": {
    "access_token": "<token_str>",
    "token_type": "Bearer",
    "refresh_token": "<refresh_token_str>",
    "expiry": "2026-09-07T19:54:12.123456Z"
  },
  "auth_method": "consumer"
}
```

---

## 3. Architecture & Two-Tier Renewal Flow

```mermaid
flowchart TD
    Start([Token Refresh Needed]) --> CheckExpiry{Token Expired or Near Expiry?}
    CheckExpiry -- No (and not --force) --> Success([Use Existing Token])
    CheckExpiry -- Yes --> Tier1[Tier 1: Native HTTP Refresh]
    
    Tier1 --> CallGoogle[POST https://oauth2.googleapis.com/token\nclient_id + client_secret + refresh_token]
    CallGoogle --> RespOk{HTTP 200 OK?}
    
    RespOk -- Yes --> UpdateDisk[Atomically update token file\naccess_token, expiry, and optional refresh_token]
    UpdateDisk --> Success
    
    RespOk -- No --> Tier2Check{agy binary available on PATH?}
    Tier2Check -- No --> FailAuth[Return Error / 401]
    Tier2Check -- Yes --> Tier2[Tier 2 Fallback: Headless agy Probe]
    
    Tier2 --> AcquireLock[Acquire manager.lock]
    AcquireLock --> BackupLive[Backup ~/.gemini/.../antigravity-oauth-token]
    BackupLive --> StageTarget[Copy target profile token to live path]
    StageTarget --> RunAgy[Run: agy models with 10s timeout]
    RunAgy --> ReadRefreshed[Read refreshed token from live path]
    ReadRefreshed --> SaveVault[Copy back to vault ~/.promux/accounts/<name>/]
    SaveVault --> RestoreLive[Restore original backup to live path]
    RestoreLive --> ReleaseLock[Release manager.lock]
    ReleaseLock --> VerifyTier2{Token refreshed?}
    VerifyTier2 -- Yes --> Success
    VerifyTier2 -- No --> FailAuth
```

---

## 4. Detailed Component Specifications

### 4.1 `constants.py`
Set the official defaults while preserving environment variable and `oauth.json` overrides:
```python
DEFAULT_OAUTH_CLIENT_ID = "REDACTED_OAUTH_CLIENT_ID"
DEFAULT_OAUTH_CLIENT_SECRET = "REDACTED_OAUTH_CLIENT_SECRET"

OAUTH_CLIENT_ID = os.environ.get("PROMUX_OAUTH_CLIENT_ID", DEFAULT_OAUTH_CLIENT_ID)
OAUTH_CLIENT_SECRET = os.environ.get("PROMUX_OAUTH_CLIENT_SECRET", DEFAULT_OAUTH_CLIENT_SECRET)
```

### 4.2 Core Renewal Function (`_refresh_token_file` & fallback)
1. **Tier 1 (Native HTTP)**:
   - Reads `refresh_token` from token dict.
   - Executes `POST` to `OAUTH_TOKEN_URL` with urlencoded form data.
   - Updates `access_token`, `expiry` (`now + timedelta(seconds=expires_in)`), and `refresh_token` (if returned by Google).
   - Writes atomically via `.tmp` file and `os.replace` with `0600` permissions.
2. **Tier 2 (Headless Subprocess Fallback)**:
   - Invoked if Tier 1 returns `None` or raises an exception.
   - Checks `shutil.which("agy")`.
   - Uses `StorageEngine` locks to safely stage and restore the active session.
   - Runs `agy models` with `capture_output=True, timeout=10`.
   - Inspects the staged token to confirm expiry advanced before saving back to the target profile.

### 4.3 Expiry Detection Buffer
In `_is_token_expired(token_data: dict, buffer_seconds: int = 60) -> bool`:
- Parses ISO-8601 UTC timestamp from `token.expiry`.
- Considers token expired if `datetime.now(timezone.utc) + timedelta(seconds=buffer_seconds) >= expiry_dt`.
- This 60-second buffer prevents race conditions where a token expires mid-flight during a multi-call API session.

### 4.4 CLI Integration Points

#### 1. `promux quota [name]`
- In `_fetch_account_quota`:
  - Before querying `loadCodeAssist` or `retrieveUserQuotaSummary`, inspects token expiry using the 60s buffer.
  - Automatically refreshes if expired.
  - If a `401 Unauthorized` occurs despite expiry check, triggers a retry refresh and re-attempts the request once.
  - Syncs refreshed token back to both the vault and `live_token` if it is the currently active profile.

#### 2. `promux switch <name>`
- In `StorageEngine.switch_profile`:
  - Before hot-swapping `src_token` to `live_token`, checks if `src_token` is expired.
  - Refreshes `src_token` in place if expired.
  - Hot-swaps the fresh token into `live_token`.

#### 3. New Command: `promux refresh [name]`
- `promux refresh` without argument iterates over all accounts in the vault.
- `promux refresh <name>` refreshes a single account.
- Options:
  - `--force`: Refreshes immediately even if the token has not expired.
  - `--json`: Outputs structured JSON results with account names, old/new expiry, and success status.
- CLI Table Output:
  ```
  ACCOUNT           STATUS       EXPIRY (UTC)              METHOD
  -----------------------------------------------------------------
  main              REFRESHED    2026-09-07 19:54:12       native
  hard              UNCHANGED    2026-09-07 20:12:00       valid
  backup            REFRESHED    2026-09-07 19:54:15       fallback (agy)
  ```

#### 4. `promux watch` (Proactive Background Renewal)
- `LogWatcher` executes a proactive maintenance cycle every 15 minutes (or configurable).
- Any account in the vault whose token expires in < 10 minutes is automatically refreshed.
- Logs informational message: `[promux-watch] Proactively renewed token for account '<name>'`.

---

## 5. Security, Concurrency & Error Handling

1. **POSIX Permissions & Atomic Writes**:
   - Every token file creation and update strictly enforces `0600` permissions.
   - Writes to a `.tmp` sibling file before atomic `os.replace` to prevent partial reads by concurrent processes.
2. **Global Mutex (`manager.lock`)**:
   - File locking prevents concurrent CLI operations or daemons from colliding during live token swaps.
3. **Revocation & Invalid Grants**:
   - If Google returns `invalid_grant` (refresh token revoked by user or session terminated):
     - Displays: `Error: Refresh token for '<account>' has expired or been revoked. Please re-authenticate via 'agy' and save using 'promux save <account>'.`
     - Marks account status cleanly without crashing or entering infinite loops.

---

## 6. Verification & Test Plan

### 6.1 Automated Unit Tests (`pytest`)
- `test_constants_default_credentials`: Verifies default client ID and secret exist and can be overridden by env or `oauth.json`.
- `test_is_token_expired_buffer`: Validates 60-second threshold logic.
- `test_refresh_token_file_native_success`: Mocks `urllib.request.urlopen` to test access token update and expiry update.
- `test_refresh_token_file_rotates_refresh_token`: Tests updating `refresh_token` when Google supplies a replacement.
- `test_refresh_token_fallback_to_agy`: Mocks Tier 1 failure and verifies Tier 2 invokes `agy` subprocess correctly under lock.
- `test_cmd_refresh_all_and_single`: Tests `promux refresh` CLI with table and JSON outputs.
- `test_switch_refreshes_expired_profile`: Confirms switching to an expired profile auto-refreshes before copying to live path.
- `test_quota_auto_refreshes_expired_profile`: Confirms `promux quota` transparently updates expired accounts and displays quota.
- `test_watch_proactive_refresh_check`: Verifies `promux watch` background loop triggers refresh for accounts nearing expiry.

### 6.2 Live Manual Verification
- Execute `promux refresh --json` on real user accounts in the vault.
- Execute `promux quota` to verify that all accounts display real quota percentages without `[AUTH ERROR]`.
- Verify `promux switch` on a standby account.
