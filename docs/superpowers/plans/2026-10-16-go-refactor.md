# Promux Go Native Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-implement Promux as a standalone, zero-runtime-dependency Go binary with 1:1 functional parity to the Python version, plus an autonomous PTY runner that auto-injects `continue` on quota failovers.

**Architecture:** A modular Go CLI application (`pkg/models`, `pkg/config`, `pkg/lock`, `pkg/storage`, `pkg/auth`, `pkg/quota`, `pkg/failover`, `pkg/watcher`, `pkg/runner`, `cmd/promux`) using `spf13/cobra` for command routing, `creack/pty` for terminal execution, and direct OS syscalls for cross-platform advisory file locking.

**Tech Stack:** Go 1.22+, `github.com/spf13/cobra`, `github.com/creack/pty`, `golang.org/x/sys`, `github.com/fatih/color`.

## Global Constraints
- Target Go version: 1.22+
- Single static binary output (`promux`) with zero external runtime dependencies
- 100% data compatibility with existing `~/.promux` vault schema (`state.json`, `manager.lock`, `accounts/`)
- Strict atomic file writes (`0600` permissions on temp files, committed via atomic rename)
- Cross-platform file locking (`flock` on POSIX, `LockFileEx` on Windows)
- Every package must pass unit tests with race detection (`go test -race ./...`)

---

## File Structure Map

```
promux/
├── go.mod
├── go.sum
├── Makefile
├── cmd/
│   └── promux/
│       └── main.go
└── pkg/
    ├── config/
    │   ├── paths.go
    │   ├── constants.go
    │   └── config_test.go
    ├── models/
    │   ├── account.go
    │   ├── quota.go
    │   └── models_test.go
    ├── lock/
    │   ├── lock.go
    │   ├── lock_unix.go
    │   ├── lock_windows.go
    │   └── lock_test.go
    ├── storage/
    │   ├── storage.go
    │   ├── transaction.go
    │   └── storage_test.go
    ├── auth/
    │   ├── token.go
    │   ├── refresh.go
    │   └── auth_test.go
    ├── quota/
    │   ├── client.go
    │   ├── calculator.go
    │   ├── cache.go
    │   └── quota_test.go
    ├── failover/
    │   ├── engine.go
    │   └── failover_test.go
    ├── watcher/
    │   ├── watcher.go
    │   ├── parser.go
    │   └── watcher_test.go
    ├── runner/
    │   ├── runner.go
    │   ├── pty.go
    │   └── runner_test.go
    ├── adapter/
    │   ├── adapter.go
    │   ├── registry.go
    │   ├── agy.go
    │   └── adapter_test.go
    └── cmd/
        ├── root.go
        ├── profile.go
        ├── quota.go
        ├── watch.go
        ├── run.go
        └── tools.go
```

---

### Task 1: Project Scaffolding & Domain Models (`pkg/models`)

**Files:**
- Create: `go.mod`
- Create: `pkg/models/account.go`
- Create: `pkg/models/quota.go`
- Test: `pkg/models/models_test.go`

**Interfaces:**
- Produces:
  - `models.AccountState` (ACTIVE, STANDBY, COOLDOWN, DISABLED)
  - `models.AccountMeta` struct with JSON serialization
  - `models.State` struct with Active and Accounts map
  - `models.QuotaWindow` and `models.QuotaSummary` structs

- [ ] **Step 1: Initialize Go module**
```bash
go mod init github.com/alirezaopmc/promux
```

- [ ] **Step 2: Write failing tests for domain models**
Create `pkg/models/models_test.go` checking account state transitions, JSON unmarshaling of Python `state.json`, and quota structures.

- [ ] **Step 3: Run test to verify it fails**
```bash
go test -v ./pkg/models
```

- [ ] **Step 4: Implement models**
Implement `account.go` and `quota.go` matching Python `promux/models.py`.

- [ ] **Step 5: Run tests to verify pass**
```bash
go test -v ./pkg/models
```

- [ ] **Step 6: Commit**
```bash
git add go.mod pkg/models/
git commit -m "feat(models): initialize go module and domain models"
```

---

### Task 2: Cross-Platform Advisory File Locking (`pkg/lock`)

**Files:**
- Create: `pkg/lock/lock.go`
- Create: `pkg/lock/lock_unix.go`
- Create: `pkg/lock/lock_windows.go`
- Test: `pkg/lock/lock_test.go`

**Interfaces:**
- Produces:
  ```go
  type Locker interface {
      Lock() error
      TryLock() (bool, error)
      Unlock() error
  }
  func NewFileLock(path string) Locker
  func WithLock(path string, fn func() error) error
  ```

- [ ] **Step 1: Write failing tests for FileLock**
Test concurrent locking, non-blocking TryLock, reentrancy guards, and unlock cleanup.

- [ ] **Step 2: Run test to verify it fails**
```bash
go test -v ./pkg/lock
```

- [ ] **Step 3: Implement POSIX and Windows file lock**
Use `golang.org/x/sys/unix` for POSIX `fcntl.Flock` and Windows `LockFileEx` fallback.

- [ ] **Step 4: Run tests to verify pass**
```bash
go test -v -race ./pkg/lock
```

- [ ] **Step 5: Commit**
```bash
git add pkg/lock/ go.mod go.sum
git commit -m "feat(lock): cross-platform advisory file locking"
```

---

### Task 3: Config, Constants & Paths (`pkg/config`)

**Files:**
- Create: `pkg/config/constants.go`
- Create: `pkg/config/paths.go`
- Test: `pkg/config/config_test.go`

**Interfaces:**
- Produces:
  - `config.PromuxHome()` (respects `PROMUX_HOME` env var, defaults to `~/.promux`)
  - `config.GeminiHome()` (respects `PROMUX_GEMINI_HOME`, defaults to `~/.gemini/antigravity-cli`)
  - Regex constants: `IndividualQuotaRegex`, `ResourceExhaustedRegex`, `WeeklyQuotaRegex`

- [ ] **Step 1: Write failing test for config paths**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement config and regex constants**
- [ ] **Step 4: Verify test passes**
```bash
go test -v ./pkg/config
```
- [ ] **Step 5: Commit**
```bash
git add pkg/config/
git commit -m "feat(config): paths, environment overrides, and regex constants"
```

---

### Task 4: Storage Engine & Atomic Transactions (`pkg/storage`)

**Files:**
- Create: `pkg/storage/storage.go`
- Create: `pkg/storage/transaction.go`
- Test: `pkg/storage/storage_test.go`

**Interfaces:**
- Consumes: `pkg/models`, `pkg/lock`, `pkg/config`
- Produces:
  ```go
  type StorageEngine struct { ... }
  func NewStorageEngine(promuxHome, geminiHome string) *StorageEngine
  func (s *StorageEngine) LoadState() (*models.State, error)
  func (s *StorageEngine) SaveState(state *models.State) error
  func (s *StorageEngine) Transaction(ctx context.Context, fn func(state *models.State) error) error
  func (s *StorageEngine) SaveProfile(name, email, projectID, planType string) (*models.AccountMeta, error)
  func (s *StorageEngine) SwitchProfile(name string) error
  func (s *StorageEngine) RemoveProfile(name string) error
  ```

- [ ] **Step 1: Write failing test for storage transactions and atomic swaps**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement StorageEngine**
- [ ] **Step 4: Verify test passes with race detection**
```bash
go test -v -race ./pkg/storage
```
- [ ] **Step 5: Commit**
```bash
git add pkg/storage/
git commit -m "feat(storage): atomic vault transactions and profile management"
```

---

### Task 5: OAuth Token Decoder, Expiry & Refresh (`pkg/auth`)

**Files:**
- Create: `pkg/auth/token.go`
- Create: `pkg/auth/refresh.go`
- Test: `pkg/auth/auth_test.go`

**Interfaces:**
- Consumes: `pkg/storage`
- Produces:
  ```go
  func ReadTokenData(path string) (*models.TokenData, error)
  func IsTokenExpired(token *models.TokenData, bufferSeconds int) bool
  func RefreshToken(refreshToken string) (*models.TokenData, error)
  func RefreshAccountToken(storage *storage.StorageEngine, accountName string, force bool) (bool, error)
  func SyncActiveTokens(src, dst string) error
  ```

- [ ] **Step 1: Write failing tests for token reading, expiration calculation, and refresh mock**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement token reading and HTTP refresh client**
- [ ] **Step 4: Verify test passes**
```bash
go test -v ./pkg/auth
```
- [ ] **Step 5: Commit**
```bash
git add pkg/auth/
git commit -m "feat(auth): token parsing, expiry check, and oauth refresh"
```

---

### Task 6: Cloud Code Assist Quota Client, Calculator & Cache (`pkg/quota`)

**Files:**
- Create: `pkg/quota/client.go`
- Create: `pkg/quota/calculator.go`
- Create: `pkg/quota/cache.go`
- Test: `pkg/quota/quota_test.go`

**Interfaces:**
- Consumes: `pkg/models`, `pkg/storage`, `pkg/auth`
- Produces:
  ```go
  func FetchQuota(accessToken, projectID string) (*models.QuotaSummary, error)
  func CalculateRemainingWindows(resp *RawQuotaResponse) *models.QuotaSummary
  func FormatResetCountdown(duration time.Duration) string
  ```

- [ ] **Step 1: Write failing tests for quota JSON parsing, 5h & weekly window calculations**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement quota client, countdown formatters, and disk cache**
- [ ] **Step 4: Verify test passes**
```bash
go test -v ./pkg/quota
```
- [ ] **Step 5: Commit**
```bash
git add pkg/quota/
git commit -m "feat(quota): cloud code assist quota client and window calculator"
```

---

### Task 7: Failover Engine & Rotation Strategies (`pkg/failover`)

**Files:**
- Create: `pkg/failover/engine.go`
- Test: `pkg/failover/failover_test.go`

**Interfaces:**
- Consumes: `pkg/storage`, `pkg/models`, `pkg/quota`
- Produces:
  ```go
  type FailoverEngine struct { ... }
  func NewFailoverEngine(storage *storage.StorageEngine) *FailoverEngine
  func (f *FailoverEngine) RotateNext(reason string, cooldownMinutes int) (*models.RotationResult, error)
  func (f *FailoverEngine) RotateSmart(reason string, quotaClient *quota.Client) (*models.SmartRotationResult, error)
  func (f *FailoverEngine) ApplyCooldown(name string, minutes int) error
  ```

- [ ] **Step 1: Write failing tests for LRU next rotation, cooldown checks, and smart rotation**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement FailoverEngine**
- [ ] **Step 4: Verify test passes**
```bash
go test -v -race ./pkg/failover
```
- [ ] **Step 5: Commit**
```bash
git add pkg/failover/
git commit -m "feat(failover): rotation engine with cooldown and smart quota selection"
```

---

### Task 8: Passive Log Watcher Daemon (`pkg/watcher`)

**Files:**
- Create: `pkg/watcher/watcher.go`
- Create: `pkg/watcher/parser.go`
- Test: `pkg/watcher/watcher_test.go`

**Interfaces:**
- Consumes: `pkg/failover`, `pkg/config`
- Produces:
  ```go
  type LogWatcher struct { ... }
  func NewLogWatcher(storage *storage.StorageEngine, failover *failover.FailoverEngine) *LogWatcher
  func (w *LogWatcher) RunOnce() ([]LogMatch, error)
  func (w *LogWatcher) RunForever(ctx context.Context, onMatch func(LogMatch)) error
  func (w *LogWatcher) ParseResetMinutes(hint string) int
  ```

- [ ] **Step 1: Write failing tests for log tailing, inode rotation, truncation, and regex matchers**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement LogWatcher**
- [ ] **Step 4: Verify test passes**
```bash
go test -v ./pkg/watcher
```
- [ ] **Step 5: Commit**
```bash
git add pkg/watcher/
git commit -m "feat(watcher): log tailing daemon with reactive failover"
```

---

### Task 9: Autonomous PTY Runner with Auto-Continue Injection (`pkg/runner`)

**Files:**
- Create: `pkg/runner/runner.go`
- Create: `pkg/runner/pty.go`
- Test: `pkg/runner/runner_test.go`

**Interfaces:**
- Consumes: `pkg/failover`, `pkg/config`
- Produces:
  ```go
  type Runner struct { ... }
  func NewRunner(failover *failover.FailoverEngine) *Runner
  func (r *Runner) Exec(ctx context.Context, cmdName string, args []string) error
  ```

- [ ] **Step 1: Write failing test for PTY output scanning and mock keystroke injection**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement PTY runner using `creack/pty`**
- [ ] **Step 4: Verify test passes**
```bash
go test -v ./pkg/runner
```
- [ ] **Step 5: Commit**
```bash
git add pkg/runner/
git commit -m "feat(runner): pty wrapper with auto-continue keystroke injection"
```

---

### Task 10: Cobra CLI Command Hierarchy & Parity Test Suite

**Files:**
- Create: `cmd/promux/main.go`
- Create: `pkg/cmd/root.go`
- Create: `pkg/cmd/profile.go`
- Create: `pkg/cmd/quota.go`
- Create: `pkg/cmd/watch.go`
- Create: `pkg/cmd/run.go`
- Create: `pkg/cmd/tools.go`
- Test: `pkg/cmd/cmd_test.go`
- Create: `Makefile`

**Interfaces:**
- Produces: Compiled `bin/promux` executable supporting all CLI grammar flags

- [ ] **Step 1: Implement Cobra commands and wiring**
- [ ] **Step 2: Write comprehensive end-to-end CLI parity tests**
- [ ] **Step 3: Verify all tests pass across all packages**
```bash
go test -v -race ./...
```
- [ ] **Step 4: Build binary and smoke test**
```bash
make build
./bin/promux version
./bin/promux profile list
```
- [ ] **Step 5: Commit**
```bash
git add cmd/ pkg/cmd/ Makefile
git commit -m "feat(cli): cobra command hierarchy and full parity test suite"
```
