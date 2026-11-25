# Antigravity 2.0 Hub SSH GUI Profile Switching Design

- **Status:** Draft (Pending User Review)
- **Author:** Antigravity / Promux Team
- **Date:** 2026-11-25
- **Scope:** `pkg/models`, `pkg/config`, `pkg/storage`, `pkg/auth`, `pkg/hub`, `pkg/cmd`

---

## 1. Context & Motivation

Promux currently manages Google Antigravity CLI (`agy`) OAuth profiles by swapping `~/.gemini/antigravity-cli/antigravity-oauth-token` and tracking the active profile in `~/.promux/tools/agy/state.json`.

When connecting to a Linux server from macOS via **Antigravity 2.0 Hub over SSH** (`--subclient_type hub --workspace_id agy-hub-ssh`), the remote `~/.antigravity-hub-server/bin/<version>/language_server` cannot use a desktop keyring and falls back to file-based token storage:

```text
composite_token_storage.go:176] Using file-based token storage because SSH session detected
```

- **Live Hub SSH token path:** `~/.gemini/jetski-standalone-oauth-token`
- **Daemon metadata directory:** `~/.gemini/antigravity/daemon/ls_*.json` (containing `pid`, `httpsPort`, `httpPort`, `lspPort`, `lsVersion`, `csrfToken`, alongside `.host`, `.keeper`, `.lease`)
- **Schema compatibility:** `jetski-standalone-oauth-token` uses the exact same `token` (`access_token`, `token_type`, `refresh_token`, `expiry`, `expires_in`), `auth_method`, and `id_token_email` fields as `antigravity-oauth-token`, adding only a top-level `"writer_id"` string. Both surfaces embed the same consumer OAuth Client ID (`1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com`).
- **In-memory caching:** Once authenticated, the Hub `language_server` holds an in-memory `oauth2.ReuseTokenSource` and cached `loadCodeAssistResponse`/`userInfo`/`peopleInfo`. Swapping `~/.gemini/jetski-standalone-oauth-token` on disk does not take effect until the Hub `language_server` restarts.

### Scope & Non-Goals
- **In scope:** Core Hub SSH profile switching, saving, rotation, status/whoami inspection, `--hub` combined CLI+Hub switch/next, `promux refresh` sync to Hub, and graceful Hub `language_server` restart (including when invoked from inside an active Hub `language_server` terminal/agent).
- **Out of scope (deferred):**
  - `promux watch --hub` log-tailing auto-failover (auto-killing a live GUI `language_server` mid-turn is intentionally excluded for now).
  - Non-SSH macOS desktop keychain switching (`composite_token_storage.go` keyring path).
  - VS Code-based Antigravity IDE (`~/.antigravity-ide-server`), which stores OAuth state on the macOS UI host via `antigravityUnifiedStateSync` (`uss-oauth`) rather than on the remote Linux host.

---

## 2. Architecture Overview

```
                     ┌────────────────────────────────────┐
                     │        ~/.promux/tools/agy/        │
                     │  accounts/<name>/antigravity-oauth-token
                     │  state.json  (active, hub_active)  │
                     └─────────┬─────────────────┬────────┘
                               │                 │
            (CLI target)       │                 │  (Hub SSH target via pkg/hub)
                               ▼                 ▼
   ~/.gemini/antigravity-cli/          ~/.gemini/jetski-standalone-oauth-token
     antigravity-oauth-token             (+ preserved/generated writer_id)
                                                 │
                                                 │  Outside manager.lock:
                                                 ▼  discover & SIGTERM/respawn
                                       ~/.gemini/antigravity/daemon/ls_*.json
                                       (verified --subclient_type hub PIDs)
```

### Design Principles
1. **Single shared account vault:** Reuse `~/.promux/tools/agy/accounts/<name>/antigravity-oauth-token` so existing `agy` profiles work immediately in the GUI without re-login.
2. **Independent active pointers:** `State.Active` tracks `agy` CLI; `State.HubActive` tracks Hub SSH GUI. Users can run CLI and GUI on different accounts or sync both via `--hub`.
3. **Lock-free process lifecycle:** Never hold `manager.lock` while signaling or waiting on `language_server` processes.
4. **Cross-platform builds:** Build-tagged process inspection/signaling (`process_linux.go`, `process_darwin.go`, `process_windows.go`) keeps `GOOS=linux`, `GOOS=darwin`, and `GOOS=windows` compiling cleanly.

---

## 3. Data Model, Config & Token Translation

### 3.1 `pkg/models/account.go`
Extend `State` with a nullable, backward-compatible field:

```go
type State struct {
	Active    *string                 `json:"active"`
	HubActive *string                 `json:"hub_active,omitempty"`
	Accounts  map[string]*AccountMeta `json:"accounts"`
}
```

- Existing `state.json` files without `hub_active` unmarshal with `HubActive == nil`.
- `StorageEngine.RemoveProfile(name)` clears `state.HubActive = nil` whenever `state.HubActive != nil && *state.HubActive == name` (matching `state.Active`).

### 3.2 `pkg/config/paths.go`
Add Hub path helpers with env-var overrides for hermetic tests and custom installations:

- `HubGeminiDir() string`: returns `PROMUX_HUB_GEMINI_DIR` if set, otherwise `filepath.Join(home, ".gemini")`.
- `HubLiveTokenFile(hubGeminiDir string) string`: returns `PROMUX_HUB_TOKEN_FILE` if set, otherwise `filepath.Join(hubGeminiDir, "jetski-standalone-oauth-token")`.
- `HubDaemonDir(hubGeminiDir string) string`: returns `PROMUX_HUB_DAEMON_DIR` if set, otherwise `filepath.Join(hubGeminiDir, "antigravity", "daemon")`.

### 3.3 `pkg/hub/token.go` — Schema Translation & Sync

#### Vault → Hub Write (`SyncVaultToHubToken(vaultTokenPath, hubTokenPath string) (writerID string, err error)`)
1. Read `vaultTokenPath` via `auth.ReadTokenData`. Return error if missing or missing both `access_token` and `refresh_token`.
2. Check `hubTokenPath`: if it exists and parses as valid JSON, extract its non-empty `"writer_id"` string.
3. If no `"writer_id"` exists (file missing, unreadable, corrupt JSON, or empty field), generate 32 cryptographically random bytes encoded with `base64.RawURLEncoding` (43-char URL-safe string matching Hub's `writer_id` format).
4. Clone the token map, set `"writer_id"` to the preserved/generated value, and write atomically (`hubTokenPath + ".tmp"`, mode `0600`, `Sync()`, `Close()`, `os.Rename`).

#### Hub → Vault Save (`ReadHubTokenForVault(hubTokenPath string) (clean auth.RawTokenData, email string, err error)`)
1. Read `hubTokenPath` via `auth.ReadTokenData`.
2. Validate that `auth.ExtractAccessToken(data) != ""` or `auth.ExtractRefreshToken(data) != ""`.
3. Extract `id_token_email` (string) if present so `promux hub save <name>` auto-populates `AccountMeta.Email` when `--email` is omitted.
4. Clone the map and remove `"writer_id"` before writing to `~/.promux/tools/agy/accounts/<name>/antigravity-oauth-token`.

#### Pre-Switch Freshness & `auth.RefreshAccountToken` Integration
- Before switching Hub to `<name>`, check `auth.IsTokenExpired(vaultData, config.DefaultTokenExpiryBufferSeconds)`. If expired or missing `access_token`, call `auth.RefreshAccountToken(store, name, true)` **before** updating `HubActive` or writing `jetski-standalone-oauth-token`.
- In `auth.RefreshAccountToken(store, accountName, force)`:
  - If `state.HubActive != nil && *state.HubActive == accountName`:
    1. If `hubTokenPath` shares the same `refresh_token` as the vault token (and was refreshed in-place by `language_server`), sync `hubTokenPath` → `vaultToken` (stripping `writer_id`) before evaluating expiry (`SyncHubToVaultIfSameAccount`).
    2. After refreshing `vaultToken`, sync `vaultToken` → `hubTokenPath` (preserving `writer_id`).
- Add `StorageEngine.SetHubActive(ctx context.Context, name string) error` and `StorageEngine.SaveHubProfile(...)` (or transactional helpers on `StorageEngine` under `manager.lock`) so `HubActive` and `LastUsedAt` are always updated atomically under advisory file lock.

#### Divergence Detection (`HubSyncStatus`)
`promux hub whoami` and `promux hub status` compare `jetski-standalone-oauth-token` against `state.HubActive`'s vaulted token by comparing `ExtractRefreshToken` (primary) and `id_token_email` (secondary):
- `in-sync`: Hub live file matches `HubActive`.
- `out-of-sync`: Hub live file has a different `refresh_token` (e.g., user signed in via GUI browser OAuth without running `promux hub save`). Reported clearly with live `id_token_email`.
- `missing`: `jetski-standalone-oauth-token` does not exist on disk.
- `untracked`: `jetski-standalone-oauth-token` exists on disk, `state.HubActive == nil`.

---

## 4. Hub Daemon Discovery & Restart Lifecycle (`pkg/hub/process*.go`)

### 4.1 Discovery & PID Verification
`DiscoverDaemons(daemonDir string) ([]DaemonInfo, error)` scans `filepath.Join(daemonDir, "ls_*.json")`:
1. Unmarshal JSON `{pid, httpsPort, httpPort, lspPort, lsVersion, csrfToken}`. Skip unreadable or malformed files with a warning rather than aborting.
2. Verify `pid > 0` is alive (`signal 0`).
3. Verify the process command line matches an Antigravity Hub `language_server` (contains `language_server` and `--subclient_type hub` or `agy-hub-ssh`):
   - **Linux (`process_linux.go`):** read `/proc/<pid>/cmdline` (NUL-separated arguments).
   - **macOS (`process_darwin.go`):** run `ps -p <pid> -o args=`.
   - **Windows (`process_windows.go`):** stub returning unsupported / no-op discovery so cross-compilation succeeds.
4. Ignore stale `ls_*.json` files whose PID is dead or belongs to an unrelated recycled PID.

### 4.2 Graceful Shutdown & Respawn Check
Hub `language_server` traps `SIGTERM` (`Shutting down cleanly on signal`) and closes SQLite WAL (`conversation_summaries.db`), sidecars, and remote-control connections cleanly.

`RestartDaemons(ctx context.Context, daemonDir string, opts RestartOptions) (*RestartResult, error)`:
1. Discover verified Hub LS PIDs. If none are running, return `RestartResult{StoppedPIDs: nil}` (success, non-error).
2. Check whether the current `promux` process is a descendant of any target Hub LS PID (see §4.3).
3. For external invocations:
   - Send `SIGTERM` to each target PID.
   - Poll every `100ms` up to `5s` (`ShutdownTimeout`) until all target PIDs exit.
   - Any PID still alive after `5s` receives `SIGKILL` and is polled up to `2s`.
   - Poll `DiscoverDaemons(daemonDir)` every `200ms` up to `3s` (`RespawnWaitTimeout`) to detect whether `agy-keeper` or the macOS client automatically respawned a new Hub LS PID:
     - If new PID(s) appear: populate `RestartResult.RespawnedPIDs` (`restarted`).
     - If none appear within `3s`: populate `RestartResult.NeedsReconnect = true` (`stopped`; GUI reconnects on its own or on window reload). Still returns `err == nil`.

### 4.3 Self-Kill Corner Case (Invoked Inside Antigravity GUI Terminal / Agent)
When `promux hub switch` is run from a terminal or agent spawned by the target `language_server`, sending `SIGTERM` synchronously kills `language_server`'s managed terminals (`Shutting down terminal ...`), terminating `promux` before it prints output or exits `0`.

- **Ancestor detection (`IsDescendantOf(targetPIDs []int) bool`):**
  - On Linux, walk `PPid` via `/proc/<pid>/stat` up to PID `1`; on macOS, use `ps -p <pid> -o ppid=`.
- **Detached delayed restart:**
  - If `IsDescendantOf` is `true` and `--no-restart` is **not** set:
    1. `promux` writes its final user/JSON response to `os.Stdout` and flushes.
    2. `promux` spawns a detached child (`os.Executable()` with `hub _restart-pids --delay 300ms <pid...>`, `SysProcAttr{Setsid: true}`, stdio redirected to `/dev/null`), returning `RestartResult{Deferred: true, DeferredPIDs: ...}`.
    3. `promux` exits `0` immediately; `300ms` later the detached helper executes the `SIGTERM` → `SIGKILL` shutdown sequence cleanly.

---

## 5. CLI Surface & Behavior (`pkg/cmd/hub.go`, `pkg/cmd/profile.go`)

### 5.1 New `promux hub` Subcommands
All `hub` subcommands honor global flags (`--json`, `--verbose`, `--debug`).

| Command | Flags | Behavior |
|---------|-------|----------|
| `promux hub switch <name>` | `--no-restart` | Validate `<name>`, refresh vault token if expired, sync to `jetski-standalone-oauth-token`, update `HubActive` & `LastUsedAt`, and restart Hub LS (unless `--no-restart`). |
| `promux hub next` | `--reason`, `--cooldown`, `--no-restart` | Rotate from `state.HubActive`: apply cooldown to previous `HubActive` (if set), select LRU standby account (reusing `failover` ordering), refresh if expired, sync to `jetski-standalone-oauth-token`, update `HubActive`, restart Hub LS. |
| `promux hub save <name>` | `--email`, `--project`, `--plan` | Read `~/.gemini/jetski-standalone-oauth-token`, strip `writer_id`, save to vault, default `email` from `id_token_email`, set `HubActive = &name` if unset (or if updating existing `HubActive`). |
| `promux hub whoami` | — | Display `HubActive`, email, plan, live-token sync status (`in-sync` / `out-of-sync` / `missing` / `untracked`), and detected Hub LS PIDs. |
| `promux hub status` | — | Alias/detailed view of `hub whoami` including daemon ports (`httpsPort`, `httpPort`), `lsVersion`, and `jetski-standalone-oauth-token` expiry. |
| `promux hub restart` | — | Restart verified running Hub `language_server` processes without touching token files (uses detached helper if invoked inside Hub LS). |
| `promux hub _restart-pids` | `--delay` | Hidden internal command for `Setsid` delayed restart when called from inside Hub LS. |

### 5.2 Top-Level Command Updates
- `promux switch <name> [--hub] [--no-restart]`:
  - Validates `<name>` exists and (when `--hub` is set) refreshes `<name>` if expired **before** mutating either target, preventing half-switched state on auth failure.
  - Performs CLI switch, then Hub sync + restart (unless `--no-restart`).
- `promux next [--hub] [--no-restart]`:
  - Rotates CLI profile, and when `--hub` is set, syncs the chosen `ToAccount` to Hub (preserving `HubActive` update + Hub LS restart).
- `promux list`:
  - Keeps existing columns; marks `HubActive` cleanly in the profile column (e.g., `* name [hub]` or `  name [hub]` when CLI and Hub differ) and preserves `hub_active` in `--json` output via `models.State`.
- `promux refresh [account]`:
  - Syncs refreshed credentials to `~/.gemini/jetski-standalone-oauth-token` whenever `account == state.HubActive`.

### 5.3 Error Handling & Output Contracts
- Errors are always returned wrapped with context (`fmt.Errorf("...: %w", err)`); no silent swallows.
- `os.Stdout` is strictly for user tables, status lines, and `--json` payloads. Operational warnings (e.g., `out-of-sync` live token or `NeedsReconnect` note) go to `os.Stderr` when `--json` is active so JSON parsing never breaks.
- Non-existent `promux hub switch <missing>` returns `storage.ErrAccountNotFound` before touching `jetski-standalone-oauth-token` or signaling `language_server`.
- If `state.HubActive == nil` when running `promux hub next`, it selects the LRU eligible standby account without applying cooldown to a nil active account (matching `FailoverEngine.RotateNext`).
- If `--tool` (`-t`) is explicitly set to a tool other than `agy`, `promux hub` returns an explicit error (`hub is only supported for tool 'agy'`) rather than touching another tool's vault.

---

## 6. Verification & Testing Plan

### 6.1 Unit & Integration Tests
1. **`pkg/hub/token_test.go`:**
   - `SyncVaultToHubToken`: preserves existing `writer_id`, generates 43-char URL-safe `writer_id` when missing or corrupt, enforces `0600` permissions, atomic write.
   - `ReadHubTokenForVault`: strips `writer_id`, extracts `id_token_email`, rejects empty/invalid tokens.
   - `CheckHubSyncStatus`: covers `in-sync`, `out-of-sync`, `missing`, and `untracked`.
2. **`pkg/hub/process_test.go`:**
   - `DiscoverDaemons`: filters dead PIDs, malformed JSON, and unrelated live PIDs (e.g. current test process PID).
   - Subprocess `TestHelperProcess` tests: verifies `SIGTERM` graceful shutdown, `SIGKILL` fallback when `SIGTERM` is trapped, and `IsDescendantOf` ancestor detection (`true` for child of helper PID, `false` for unrelated PID).
3. **`pkg/storage/storage_test.go` & `pkg/auth/refresh_test.go`:**
   - `State.HubActive` backward compatibility when loading legacy `state.json`.
   - `RemoveProfile` clears `HubActive` when removing the Hub-active profile.
   - `RefreshAccountToken` updates `~/.gemini/jetski-standalone-oauth-token` (preserving `writer_id`) when `account == HubActive`.
4. **`pkg/cmd/hub_test.go` / `pkg/cmd/e2e_test.go`:**
   - End-to-end CLI + `--json` tests for `promux hub save`, `promux hub switch --no-restart`, `promux hub next --no-restart`, `promux hub whoami`, `promux hub status`, `promux switch --hub --no-restart`, `promux next --hub --no-restart`, and `promux list` with distinct `Active` and `HubActive`.
   - Pre-switch expired token refresh test with mocked OAuth HTTP server.

### 6.2 Build & Definition of Done
1. `go test -v -race ./...` passes with 0 failures and 0 race conditions.
2. `go vet ./...` reports 0 errors.
3. `make build` compiles cleanly to `bin/promux`, and cross-compile check (`GOOS=darwin go build ./...` and `GOOS=windows go build ./...`) passes.
4. Manual live smoke test against an active Hub SSH session (`promux hub whoami`, `promux hub save`, `promux hub switch`) to verify whether `agy-keeper` / macOS Hub client auto-reconnects after `SIGTERM`.
