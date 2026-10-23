# AGENTS.md

## Project Overview
Promux is a **zero-runtime-dependency** Go CLI tool and background daemon for managing multiple
Antigravity CLI (agy) OAuth profiles and automatic quota-aware failover.

- Go: 1.24+
- Build: `make build` or `go build ./cmd/promux`
- Dependencies: Go standard library + `github.com/creack/pty` (Unix PTY runner) + `github.com/spf13/cobra` (CLI parser)

## Development Commands
| Task | Command |
|------|---------|
| Build binary | `make build` (or `go build -o bin/promux ./cmd/promux`) |
| Install (local) | `make install` (installs to `~/.local/bin/promux`) |
| Run all tests | `make test` (or `go test -v -race ./...`) |
| Run single test package | `go test -v -race ./pkg/quota/...` |
| Run single test | `go test -v -race ./pkg/quota/... -run TestQuotaClientUserQuotaEnrichment` |
| Type check & vet | `go vet ./...` |

## Architecture — Module Responsibilities
- `cmd/promux/` — Main binary entrypoint (`main.go`).
- `pkg/cmd/` — Cobra CLI command tree (`root.go`, `profile.go`, `quota.go`, `watch.go`, `run.go`, `tools.go`, `version.go`, `prime.go`).
- `pkg/auth/` — Token read/write/refresh/sync (native HTTP OAuth + headless CLI fallback).
- `pkg/config/` — Configuration constants, paths, and environment variable resolution.
- `pkg/failover/` — Quota-aware profile rotation engine (`next` / `smart`). Pure business logic.
- `pkg/lock/` — Cross-platform advisory file lock (POSIX `flock` + Windows `LockFileEx`).
- `pkg/models/` — Data structures (`AccountMeta`, `State`, `QuotaSummary`, etc.).
- `pkg/quota/` — Cloud Code Assist REST API client, quota calculation, caching, and priming.
- `pkg/runner/` — Autonomous interactive PTY/pipe command execution with real-time log scanning.
- `pkg/storage/` — Profile vault I/O with transactional locking and atomic file operations.
- `pkg/terminal/` — ANSI-aware table formatting, width calculation, and coloring.
- `pkg/watcher/` — Background log-tailing daemon engine with auto-priming.

## Constraints — Never Do
- **Never swallow errors silently.** Always log with error context or return wrapped errors.
- **Never write debug/info logs to `os.Stdout`.** Stdout is strictly for user data and JSON; logs go to `os.Stderr`.
- **Never perform non-atomic file writes on credentials or state.** Always write to a `.tmp` file and atomically rename (`os.Rename`).
- **Never bypass advisory file locking.** Always use `storage.StorageEngine` or `lock.WithLock` when reading/mutating vault state.

## Definition of Done
Before marking any task complete:
1. `go test -v -race ./...` passes (0 race conditions, 0 test failures)
2. `go vet ./...` → 0 errors
3. `make build` compiles cleanly to `bin/promux`
4. New code has corresponding unit/integration tests in `pkg/`
5. No unhandled errors or missing error returns
