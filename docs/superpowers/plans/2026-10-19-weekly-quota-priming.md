# Weekly Quota Priming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement automated and manual weekly quota priming (`promux prime`, `promux quota --all --prime`, and `promux watch --auto-prime`) to immediately activate and fix 7-day quota reset countdowns when accounts reset.

**Architecture:**
A dedicated priming engine in `pkg/quota/prime.go` inspects account quota states to find accounts whose weekly quota has reset (or has floating 100% capacity). Under advisory file lock, it safely swaps the target account's token into the live path, executes a minimal headless prompt turn (`agy --model gemini-2.5-flash --effort low -p "ping" --disable-slash-commands`), restores the active session token atomically, and updates the quota cache with the newly fixed 7-day countdown.

**Tech Stack:** Go 1.22+, Cobra CLI, Python 3.10+ (parity), Advisory File Lock, Google Cloud Code Assist REST API.

## Global Constraints
- **Zero Runtime Dependencies:** Zero new external runtime dependencies added.
- **Lock Safety:** Every credential swap must be wrapped in `lock.WithLock(ctx, store.LockFile, ...)`.
- **Guaranteed Token Restoration:** Active profile token backup and restoration must be handled in a `defer` closure.
- **Headless Flag:** Prompt execution must use `agy -p "ping" --model gemini-2.5-flash --effort low --disable-slash-commands`.
- **Definition of Done:** `go test ./...` passes; `pytest` passes; `ruff check src tests` passes; `mypy src` passes.

---

### Task 1: Go Core Priming Engine & Eligibility Logic

**Files:**
- Create: `pkg/quota/prime.go`
- Test: `pkg/quota/prime_test.go`
- Modify: `pkg/models/quota.go` (if helper fields needed)

**Interfaces:**
- Produces:
  ```go
  type PrimeResult struct {
      Account    string  `json:"account"`
      Primed     bool    `json:"primed"`
      Reason     string  `json:"reason"`
      NewReset   *string `json:"new_reset,omitempty"`
      Error      string  `json:"error,omitempty"`
  }
  func IsEligibleForPrime(qs *models.QuotaSummary, now time.Time, force bool) (bool, string)
  func PrimeAccount(ctx context.Context, store *storage.StorageEngine, cache *QuotaCache, accountName string, force bool) (*PrimeResult, error)
  func PrimeAllEligible(ctx context.Context, store *storage.StorageEngine, cache *QuotaCache, force bool) ([]PrimeResult, error)
  ```

- [ ] **Step 1: Write the failing unit tests for eligibility and priming**

In `pkg/quota/prime_test.go`:
```go
package quota

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/alirezaopmc/promux/pkg/models"
	"github.com/alirezaopmc/promux/pkg/storage"
)

func TestIsEligibleForPrime(t *testing.T) {
	now := time.Date(2026, 10, 19, 12, 0, 0, 0, time.UTC)

	// Case 1: Past weekly reset -> eligible
	past := "2026-10-19T10:00:00Z"
	qs1 := &models.QuotaSummary{
		GeminiWeeklyRemaining: 1.0,
		GeminiWeeklyReset:     &past,
	}
	eligible, _ := IsEligibleForPrime(qs1, now, false)
	if !eligible {
		t.Errorf("expected qs1 with past reset to be eligible")
	}

	// Case 2: Future weekly reset (> now) -> ineligible
	future := "2026-10-25T12:00:00Z"
	qs2 := &models.QuotaSummary{
		GeminiWeeklyRemaining: 0.95,
		GeminiWeeklyReset:     &future,
	}
	eligible, _ = IsEligibleForPrime(qs2, now, false)
	if eligible {
		t.Errorf("expected qs2 with future reset to be ineligible")
	}

	// Case 3: Future weekly reset with force=true -> eligible
	eligible, _ = IsEligibleForPrime(qs2, now, true)
	if !eligible {
		t.Errorf("expected qs2 with force=true to be eligible")
	}

	// Case 4: 100% quota with nil reset -> eligible (floating)
	qs3 := &models.QuotaSummary{
		GeminiWeeklyRemaining: 1.0,
		GeminiWeeklyReset:     nil,
	}
	eligible, _ = IsEligibleForPrime(qs3, now, false)
	if !eligible {
		t.Errorf("expected qs3 with nil reset and 100%% remaining to be eligible")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test -v ./pkg/quota -run TestIsEligibleForPrime`  
Expected: FAIL with undefined `IsEligibleForPrime`.

- [ ] **Step 3: Implement `pkg/quota/prime.go`**

Implement eligibility detection, `PrimeAccount`, and `PrimeAllEligible`:
- Check eligibility via `IsEligibleForPrime`.
- Acquire lock via `lock.WithLock(ctx, store.LockFile, ...)`.
- Check active profile via `store.LoadState()`.
- If account is not active, safely backup `store.LiveToken` to `.prime_bak_<pid>_<nanos>`, copy vault token to `store.LiveToken`, and register `defer` cleanup restoring the backup.
- Check token expiry via `auth.IsTokenExpired`; if expired, refresh via `auth.RefreshAccountToken`.
- Run child command `agy --model gemini-2.5-flash --effort low -p "ping" --disable-slash-commands`.
- Copy updated live token back to account vault.
- Restore backup in defer.
- Query live quota and update `cache.Set(accountName, qs)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `go test -v ./pkg/quota/...`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pkg/quota/prime.go pkg/quota/prime_test.go
git commit -m "feat(quota): implement weekly quota priming engine and eligibility logic"
```

---

### Task 2: Go CLI `promux prime` Command

**Files:**
- Create: `pkg/cmd/prime.go`
- Test: `pkg/cmd/prime_test.go`
- Modify: `pkg/cmd/root.go`

**Interfaces:**
- Consumes: `pkg/quota.PrimeAccount`, `pkg/quota.PrimeAllEligible`, `pkg/quota.IsEligibleForPrime`
- Produces: CLI subcommand `promux prime [account] [--force] [--dry-run] [--json]`

- [ ] **Step 1: Write the failing CLI tests**

In `pkg/cmd/prime_test.go`:
```go
package cmd

import (
	"bytes"
	"testing"
)

func TestPrimeCmdHelp(t *testing.T) {
	cmd := newPrimeCmd()
	buf := new(bytes.Buffer)
	cmd.SetOut(buf)
	cmd.SetArgs([]string{"--help"})
	err := cmd.Execute()
	if err != nil {
		t.Fatalf("unexpected error on help: %v", err)
	}
	out := buf.String()
	if !bytes.Contains(buf.Bytes(), []byte("Prime weekly quota")) {
		t.Errorf("expected help text to mention Prime weekly quota, got: %s", out)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test -v ./pkg/cmd -run TestPrimeCmdHelp`  
Expected: FAIL with undefined `newPrimeCmd`.

- [ ] **Step 3: Implement `pkg/cmd/prime.go` and register in `pkg/cmd/root.go`**

Implement:
- Flags: `-f, --force`, `-n, --dry-run`, `--json`.
- Argument: optional `[account]`.
- Output formatting: progress indicators for each profile, summary of primed vs skipped.
- Register `rootCmd.AddCommand(newPrimeCmd())` in `pkg/cmd/root.go`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `go test -v ./pkg/cmd/...`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pkg/cmd/prime.go pkg/cmd/prime_test.go pkg/cmd/root.go
git commit -m "feat(cli): add promux prime command with dry-run and json support"
```

---

### Task 3: Quota Integration: `promux quota --all --prime`

**Files:**
- Modify: `pkg/cmd/quota.go`
- Test: `pkg/cmd/prime_test.go`

**Interfaces:**
- Consumes: `pkg/quota.PrimeAccount`, `pkg/quota.PrimeAllEligible`
- Produces: `--prime` flag on `promux quota`

- [ ] **Step 1: Write the failing test for `quota --prime` flag**

In `pkg/cmd/prime_test.go`:
```go
func TestQuotaCmdPrimeFlag(t *testing.T) {
	cmd := newQuotaCmd()
	flag := cmd.Flags().Lookup("prime")
	if flag == nil {
		t.Fatal("expected --prime flag to be registered on quota command")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test -v ./pkg/cmd -run TestQuotaCmdPrimeFlag`  
Expected: FAIL with nil flag.

- [ ] **Step 3: Implement `--prime` flag in `pkg/cmd/quota.go`**

In `pkg/cmd/quota.go`:
- Register flag: `cmd.Flags().BoolVarP(&prime, "prime", "p", false, "Prime accounts that have reset weekly quota before displaying")`.
- In `RunE`:
  - If `prime` is set, invoke `quota.PrimeAllEligible` (when `allAccounts` is true) or `quota.PrimeAccount` (for single target).
  - Print status lines or update results before rendering table.

- [ ] **Step 4: Run tests to verify they pass**

Run: `go test -v ./pkg/cmd/...`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pkg/cmd/quota.go pkg/cmd/prime_test.go
git commit -m "feat(quota): add --prime flag to promux quota command"
```

---

### Task 4: Watcher Daemon Integration: `promux watch --auto-prime`

**Files:**
- Modify: `pkg/cmd/watch.go`
- Modify: `pkg/watcher/watcher.go`
- Test: `pkg/watcher/watcher_test.go`

**Interfaces:**
- Consumes: `pkg/quota.PrimeAllEligible`
- Produces: `--auto-prime` flag and tick evaluation in watcher daemon.

- [ ] **Step 1: Write the failing test for watcher auto-prime flag**

In `pkg/watcher/watcher_test.go`:
```go
func TestWatcherAutoPrimeConfig(t *testing.T) {
	w := NewWatcher("/tmp/test", 10*time.Second, true)
	if !w.AutoPrime {
		t.Errorf("expected AutoPrime to be true")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test -v ./pkg/watcher -run TestWatcherAutoPrimeConfig`  
Expected: FAIL with field not found / argument mismatch.

- [ ] **Step 3: Implement `AutoPrime` in `pkg/watcher/watcher.go` and `pkg/cmd/watch.go`**

In `pkg/watcher/watcher.go`:
- Add `AutoPrime bool` to `WatcherEngine`.
- On each watch cycle / tick:
  - If `w.AutoPrime`:
    - Call `quota.PrimeAllEligible(ctx, w.store, w.cache, false)`.
    - If any account is primed, log to daemon logger: `[auto-prime] Primed account %s`.
In `pkg/cmd/watch.go`:
- Add flag `--auto-prime` to `newWatchCmd()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `go test -v ./pkg/watcher/...`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pkg/watcher/watcher.go pkg/watcher/watcher_test.go pkg/cmd/watch.go
git commit -m "feat(watch): add --auto-prime background automation to watcher daemon"
```

---

### Task 5: Python Core Engine & CLI Parity

**Files:**
- Create: `src/promux/prime.py`
- Create: `src/promux/commands/prime.py`
- Modify: `src/promux/commands/quota.py`
- Modify: `src/promux/cli.py`
- Test: `tests/test_prime.py`

**Interfaces:**
- Produces: Python `promux.prime.is_eligible_for_prime`, `promux.prime.prime_account`, `promux prime` CLI command, and `promux quota --prime`.

- [ ] **Step 1: Write failing Python unit tests**

In `tests/test_prime.py`:
```python
from datetime import datetime, timezone
from promux.models import QuotaSummary
from promux.prime import is_eligible_for_prime

def test_is_eligible_for_prime():
    now = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    qs = QuotaSummary(gemini_weekly_remaining=1.0, gemini_weekly_reset="2026-10-19T10:00:00Z")
    eligible, reason = is_eligible_for_prime(qs, now, force=False)
    assert eligible is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_prime.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'promux.prime'`.

- [ ] **Step 3: Implement Python `prime.py`, `commands/prime.py`, and quota flag**

Implement:
- `src/promux/prime.py`: Eligibility checking, token backup/restore, running `agy -p "ping"`.
- `src/promux/commands/prime.py`: Command parser and handler.
- Update `src/promux/commands/quota.py`: add `--prime` flag.
- Register `prime` subcommand in `src/promux/cli.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_prime.py -v && ruff check src tests && mypy src`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/promux/prime.py src/promux/commands/prime.py src/promux/commands/quota.py src/promux/cli.py tests/test_prime.py
git commit -m "feat(python): implement python prime engine, cli command and quota flag"
```

---

### Task 6: Binary Build, Live Verification & Documentation

**Files:**
- Modify: `README.md`
- Build: `/home/opmc/.local/bin/promux`

- [ ] **Step 1: Build and install new Go binary**

Run:
```bash
make install
```
Verify `/home/opmc/.local/bin/promux` has the updated timestamp and `promux prime --help` displays new flags.

- [ ] **Step 2: Run dry-run verification**

Run:
```bash
promux prime --dry-run
promux quota --all --prime --dry-run || promux quota --all
```
Verify accounts like `mahan` (or any with reset reached) are correctly detected as eligible.

- [ ] **Step 3: Update documentation in `README.md`**

Add `promux prime`, `promux quota --all --prime`, and `promux watch --auto-prime` usage examples and explanation of weekly quota priming to `README.md`.

- [ ] **Step 4: Run full test suites**

Run:
```bash
go test -v ./...
pytest -v
ruff check src tests
mypy src
```
Expected: 100% tests passing, 0 lint errors, 0 type errors.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document promux prime and weekly quota activation strategy"
```
