# Weekly Quota Priming Strategy: Architecture & Design Specification

> **Document:** Design Specification  
> **Status:** Approved for Implementation  
> **Date:** 2026-10-19  
> **Target Version:** 1.1.0  
> **Authors:** Antigravity & Alireza Opmc  

---

## 1. Problem Statement & Motivation

Google Antigravity CLI (`agy`) and Google Cloud Code Assist enforce two rolling quota windows:
1. **5-Hour Rolling Window:** Continuously refreshed every 5 hours.
2. **Weekly Rolling Window:** 7-day (168-hour) quota allocation.

### The Floating Reset Delay
When an account's weekly quota resets (returning to 100% capacity), Google's backend **does not fix or start the next 7-day reset countdown** until the account consumes its first token via a user message or prompt turn.

If an account resets but sits idle for 2 days before the user sends a message, its next weekly reset is pushed out by an extra 2 days (7 days from first message turn, rather than 7 days from the reset event). Over time, multi-account rotation users forfeit valuable quota cycles.

### The Objective
Introduce an automated and manual **"Weekly Quota Priming"** strategy that detects accounts whose weekly quota has reset (or is floating without an active countdown) and automatically dispatches a minimal headless prompt turn (`agy -p "ping" --model gemini-2.5-flash`) under advisory lock. This "primes" the account, fixes the new 7-day countdown immediately, and prevents wasted reset cycle intervals.

---

## 2. CLI Interface & User Contracts

### 2.1 Dedicated Command: `promux prime`

```bash
# Prime all eligible vaulted accounts that have reset or have floating weekly quota
promux prime

# Prime a specific vaulted account
promux prime mahan

# Optional Flags:
#   -f, --force    Force priming even if an active countdown is already ticking
#   -n, --dry-run  List accounts eligible for priming without sending tokens
#   --json         Output results in structured machine-readable JSON format
```

#### Example Output:
```
Checking weekly quota status across 8 profiles...
✓ armin: Active countdown (2d 14h left) - skipped
✓ main:  Active countdown (2d 14h left) - skipped
⚡ mahan: Reset reached (100% floating) -> Priming via agy...
   [mahan] Token primed! New weekly reset: 2026-10-26T20:30:00Z (6d 23h)
✓ meow:  Active countdown (6d 23h left) - skipped

Summary: 1 account primed, 7 accounts skipped.
```

### 2.2 Flag in Quota Inspection: `promux quota --all --prime`

```bash
# Inspect all accounts, prime any eligible accounts, and render the updated table
promux quota --all --prime

# Prime and display quota for a single profile
promux quota mahan --prime
```

When `--prime` is supplied to `promux quota`:
1. It queries account quotas (bypassing stale cache for eligible accounts).
2. It detects accounts needing priming and executes the priming flow.
3. It refreshes their quota and renders the unified terminal table with updated `(6d 23h)` countdowns.

### 2.3 Daemon Automation: `promux watch --auto-prime`

```bash
# Start watcher daemon with automated weekly reset priming enabled
promux watch --auto-prime
```

When `--auto-prime` is enabled on the watcher daemon:
- On each poll interval (default 30s) or when an account's `weekly_reset` timestamp passes (`now >= weekly_reset`), the daemon triggers `PrimeAccount` under lock.
- Logs the event to `~/.promux/logs/promux.log`:
  ```
  [auto-prime] Account 'aj' reached weekly reset. Triggered headless prime turn.
  [auto-prime] Account 'aj' weekly reset fixed to 2026-10-26T21:50:22Z (6d 23h).
  ```

---

## 3. Core Architecture & Eligibility Logic

### 3.1 Eligibility Rule
An account profile $A$ is eligible for weekly priming if and only if:
1. It exists in the vault with an accessible OAuth token.
2. If `--force` is NOT set:
   - **Condition 1 (Expired Reset):** The weekly reset timestamp is non-empty and has passed ($T_{\text{reset}} \le T_{\text{now}}$).
   - **Condition 2 (Floating Window):** The weekly remaining fraction is 1.0 (100%), and the weekly reset timestamp is `nil`, empty, or in the past (indicating an unfixed new cycle).
3. If an account has an active future weekly reset timestamp ($T_{\text{reset}} > T_{\text{now}}$) and remaining fraction is not floating, it is **skipped** to preserve tokens.

### 3.2 Execution Sequence (`PrimeAccount`)

```
   [Target Account]
          │
          ▼
   Acquire File Lock (manager.lock)
          │
          ▼
   Check Active Profile
    ├── Target is Active ──────► Use live token directly
    └── Target is Inactive ────► Safe Backup Live Token
                                 Copy Vault Token -> Live Path
          │
          ▼
   Validate / Refresh Token via OAuth if expired
          │
          ▼
   Execute Headless Prompt Turn:
     agy --model gemini-2.5-flash --effort low -p "ping" --disable-slash-commands
     (with 20-second timeout context)
          │
          ▼
   Sync renewed token from Live Path back to Vault
          │
          ▼
   Atomic Restore:
     Restore original Active Profile Token from Backup (in defer)
     Remove temporary backup file
          │
          ▼
   Release File Lock
          │
          ▼
   Live Quota Verification:
     Query Google Code Assist API
     Update cache/quota.json with new 6d 23h countdown
```

---

## 4. Concurrency, Locking & Failure Recovery

1. **Advisory File Lock (`manager.lock`):**
   - The entire token-swap and `agy` invocation sequence executes inside `lock.WithLock(ctx, store.LockFile, ...)`.
   - Prevents concurrent profile switches (`promux switch`), failovers, or CLI turns from conflicting with the temporary token swap.

2. **Guaranteed Restoration (Panic & Signal Safety):**
   - Temporary live token backups are written to:
     `store.LiveToken + fmt.Sprintf(".prime_bak_%d_%d", os.Getpid(), time.Now().UnixNano())`
   - Token restoration is placed in a Go `defer` closure immediately following backup creation. Even on runtime panic, execution timeout, or SIGINT, the active profile credentials are automatically restored.

3. **Stall & Timeout Mitigation:**
   - The `agy` child process is executed with `context.WithTimeout(ctx, 20*time.Second)`. If the network hangs, the process is terminated and the live token is cleanly restored.

4. **Error Isolation in Batch Operations:**
   - In `promux prime` (all accounts), an error on one account (e.g. revoked OAuth refresh token) is logged as a failure for that account and does not abort priming for subsequent accounts.

---

## 5. Implementation Layout

### 5.1 Go Native Engine & CLI (`pkg/`)
- `pkg/quota/prime.go`:
  - `IsEligibleForPrime(qs *models.QuotaSummary, now time.Time) bool`
  - `PrimeAccount(store *storage.StorageEngine, cache *quota.QuotaCache, accountName string, force bool) (*PrimeResult, error)`
  - `PrimeAllEligible(store *storage.StorageEngine, cache *quota.QuotaCache, force bool) ([]PrimeResult, error)`
- `pkg/cmd/prime.go`:
  - Registers `promux prime [account]` command with `--force`, `--dry-run`, and `--json`.
- `pkg/cmd/quota.go`:
  - Adds `--prime` flag to `newQuotaCmd()` to trigger priming before table rendering.
- `pkg/watcher/watcher.go`:
  - Adds `--auto-prime` checking to the background watch loop.

### 5.2 Python Parity (`src/promux/`)
- `src/promux/prime.py`:
  - Core helper functions matching Go `PrimeAccount` and `is_eligible_for_prime`.
- `src/promux/commands/prime.py` & `src/promux/commands/quota.py`:
  - CLI argument wiring for `promux prime` and `promux quota --all --prime`.

---

## 6. Test & Verification Plan

1. **Eligibility Unit Tests (`pkg/quota/prime_test.go` & `tests/test_prime.py`):**
   - Test expired reset timestamp -> `eligible = true`.
   - Test floating 100% with nil reset -> `eligible = true`.
   - Test healthy future countdown (`6d 22h`) -> `eligible = false`.
   - Test `--force` flag -> `eligible = true` even with future countdown.
2. **Safe Swap Unit Tests:**
   - Mock `exec.CommandContext` / `subprocess.run`.
   - Verify inactive account token is backed up, swapped, and restored on normal execution and on error.
3. **CLI Tests (`pkg/cmd/prime_test.go` & `tests/test_cli.py`):**
   - Verify `promux prime --dry-run` outputs expected eligible accounts without spawning processes.
   - Verify `promux prime --json` outputs valid JSON records with `account`, `primed`, and `new_reset`.
   - Verify `promux quota --all --prime` triggers priming and updates rendered matrix table.
4. **Daemon Integration Tests (`pkg/watcher/watcher_test.go`):**
   - Verify watcher triggers priming when `weekly_reset` passes during tick loop.
5. **Quality Gates:**
   - `go test -v ./...` passes.
   - `pytest -v` passes (all 284+ tests).
   - `ruff check src tests` -> 0 errors.
   - `mypy src` -> 0 errors.
