# Antigravity 2.0 Hub SSH GUI Profile Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable `promux` to save, switch, rotate, inspect, and sync Antigravity 2.0 Hub SSH (`~/.gemini/jetski-standalone-oauth-token`) profiles using the shared `agy` vault and cleanly restart the Hub `language_server` when switching.

**Architecture:** Keep `~/.promux/tools/agy/accounts/<name>/antigravity-oauth-token` as the single source of truth, add `hub_active` to `models.State`, place Hub token translation/sync in `pkg/auth/hub_token.go` (preserving/generating `writer_id` and keeping `RefreshAccountToken` cycle-free), place daemon discovery and build-tagged graceful/detached `language_server` restart in `pkg/hub/`, and expose `promux hub {save,switch,next,whoami,status,restart}` plus `--hub`/`--no-restart` on `switch`/`next`.

**Tech Stack:** Go 1.24+, Cobra (`github.com/spf13/cobra`), standard library (`crypto/rand`, `encoding/base64`, `os/exec`, `syscall`, `net/http`), build tags (`linux`, `darwin`, `windows`, `unix`).

## Global Constraints

- **Go 1.24+** with zero new external runtime dependencies (`go vet ./...`, `go test -v -race ./...`, `make build`).
- **Never swallow errors silently.** Always log with error context or return wrapped errors.
- **Never write debug/info logs to `os.Stdout`.** `os.Stdout` is strictly for user data and JSON; warnings/logs go to `os.Stderr`.
- **Never perform non-atomic file writes on credentials or state.** Always write to a `.tmp` file (`0600`), sync/close, and atomically rename (`os.Rename`).
- **Never bypass advisory file locking.** Always use `storage.StorageEngine` or `lock.WithLock` when reading/mutating vault state or `state.json`.
- **Never hold `manager.lock` while signaling or waiting on `language_server` processes.**
- **Cross-platform compilation:** `GOOS=linux`, `GOOS=darwin`, and `GOOS=windows` must all compile cleanly (`go build ./...`).

---

## File Map

| File | Responsibility |
|------|----------------|
| `pkg/models/account.go` | Add `HubActive *string` (`json:"hub_active,omitempty"`) to `State` |
| `pkg/config/paths.go` | Add `HubGeminiDir()`, `HubLiveTokenFile()`, `HubDaemonDir()` with env overrides |
| `pkg/config/config_test.go` | Unit tests for Hub path resolution and env overrides |
| `pkg/storage/storage.go` | Update `RemoveProfile` to clear `HubActive`; add `SetHubActive`, `SaveHubProfileToken` |
| `pkg/storage/storage_test.go` | Unit tests for `HubActive` legacy compatibility, `RemoveProfile`, and Hub helpers |
| `pkg/auth/hub_token.go` | Vault ↔ Hub token translation (`writer_id` handling), divergence detection (`CheckHubSyncStatus`) |
| `pkg/auth/refresh.go` | Sync `HubActive` before/after refresh in `RefreshAccountToken` |
| `pkg/auth/auth_test.go` | Tests for Hub token translation, sync status, and `RefreshAccountToken` Hub sync |
| `pkg/hub/daemon.go` | Parse `ls_*.json` daemon descriptors and `DaemonInfo` types |
| `pkg/hub/process_unix.go` | Unix `SIGTERM`→`SIGKILL` shutdown, respawn polling, detached `Setsid` restart helper |
| `pkg/hub/process_linux.go` | Linux `/proc/<pid>/cmdline` verification & `/proc/<pid>/stat` ancestor detection |
| `pkg/hub/process_darwin.go` | macOS `ps` verification & ancestor detection |
| `pkg/hub/process_windows.go` | Windows no-op stubs so cross-builds pass |
| `pkg/hub/hub.go` | High-level `Switch`, `RotateNext`, `Save`, `Status`, `Restart` operations |
| `pkg/hub/hub_test.go` | Unit & `TestHelperProcess` subprocess tests for daemon discovery, signal handling, and orchestration |
| `pkg/cmd/hub.go` | `promux hub {switch,next,save,whoami,status,restart,_restart-pids}` commands |
| `pkg/cmd/profile.go` | Add `--hub` / `--no-restart` to `switch` & `next`, and `[hub]` indicator to `list` |
| `pkg/cmd/hub_test.go` | CLI & `--json` end-to-end tests for all `hub` commands and `--hub` flags |

---

### Task 1: Model & Config Hub Paths

**Files:**
- Modify: `pkg/models/account.go:38-48`
- Modify: `pkg/config/paths.go:1-72`
- Test: `pkg/config/config_test.go`
- Test: `pkg/models/models_test.go`

**Interfaces:**
- Produces `models.State.HubActive *string`
- Produces `config.HubGeminiDir() string`, `config.HubLiveTokenFile(hubGeminiDir string) string`, `config.HubDaemonDir(hubGeminiDir string) string`

- [ ] **Step 1: Write failing tests for `State.HubActive` and `config` Hub path helpers**

Add to `pkg/models/models_test.go`:
```go
func TestStateHubActiveJSONCompatibility(t *testing.T) {
	// Legacy state.json without hub_active unmarshals with HubActive == nil
	legacy := []byte(`{"active":"alpha","accounts":{}}`)
	var st State
	if err := json.Unmarshal(legacy, &st); err != nil {
		t.Fatalf("unmarshal legacy state failed: %v", err)
	}
	if st.Active == nil || *st.Active != "alpha" {
		t.Fatalf("expected active alpha, got %v", st.Active)
	}
	if st.HubActive != nil {
		t.Fatalf("expected nil HubActive for legacy state, got %v", *st.HubActive)
	}

	// State with hub_active round-trips cleanly
	hub := "beta"
	st.HubActive = &hub
	raw, err := json.Marshal(st)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}
	var decoded State
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatalf("re-unmarshal failed: %v", err)
	}
	if decoded.HubActive == nil || *decoded.HubActive != "beta" {
		t.Fatalf("expected HubActive beta, got %v", decoded.HubActive)
	}
}
```

Add to `pkg/config/config_test.go`:
```go
func TestHubPathsEnvOverrides(t *testing.T) {
	hubGemini := t.TempDir()
	t.Setenv("PROMUX_HUB_GEMINI_DIR", hubGemini)
	t.Setenv("PROMUX_HUB_TOKEN_FILE", "")
	t.Setenv("PROMUX_HUB_DAEMON_DIR", "")

	if HubGeminiDir() != hubGemini {
		t.Fatalf("expected %s, got %s", hubGemini, HubGeminiDir())
	}
	expectedTok := filepath.Join(hubGemini, "jetski-standalone-oauth-token")
	if HubLiveTokenFile("") != expectedTok {
		t.Fatalf("expected %s, got %s", expectedTok, HubLiveTokenFile(""))
	}
	expectedDaemon := filepath.Join(hubGemini, "antigravity", "daemon")
	if HubDaemonDir("") != expectedDaemon {
		t.Fatalf("expected %s, got %s", expectedDaemon, HubDaemonDir(""))
	}

	customTok := filepath.Join(t.TempDir(), "custom-hub-token")
	customDaemon := filepath.Join(t.TempDir(), "custom-daemon")
	t.Setenv("PROMUX_HUB_TOKEN_FILE", customTok)
	t.Setenv("PROMUX_HUB_DAEMON_DIR", customDaemon)
	if HubLiveTokenFile("") != customTok {
		t.Fatalf("expected %s, got %s", customTok, HubLiveTokenFile(""))
	}
	if HubDaemonDir("") != customDaemon {
		t.Fatalf("expected %s, got %s", customDaemon, HubDaemonDir(""))
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `go test -v ./pkg/models/... ./pkg/config/...`
Expected: FAIL (`HubActive` undefined, `HubGeminiDir` undefined).

- [ ] **Step 3: Implement `State.HubActive` and `config` Hub path helpers**

Update `pkg/models/account.go`:
```go
type State struct {
	Active    *string                 `json:"active"`
	HubActive *string                 `json:"hub_active,omitempty"`
	Accounts  map[string]*AccountMeta `json:"accounts"`
}
```

Add to `pkg/config/paths.go`:
```go
func HubGeminiDir() string {
	if h := os.Getenv("PROMUX_HUB_GEMINI_DIR"); h != "" {
		return h
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ".gemini"
	}
	return filepath.Join(home, ".gemini")
}

func HubLiveTokenFile(hubGeminiDir string) string {
	if p := os.Getenv("PROMUX_HUB_TOKEN_FILE"); p != "" {
		return p
	}
	if hubGeminiDir == "" {
		hubGeminiDir = HubGeminiDir()
	}
	return filepath.Join(hubGeminiDir, "jetski-standalone-oauth-token")
}

func HubDaemonDir(hubGeminiDir string) string {
	if d := os.Getenv("PROMUX_HUB_DAEMON_DIR"); d != "" {
		return d
	}
	if hubGeminiDir == "" {
		hubGeminiDir = HubGeminiDir()
	}
	return filepath.Join(hubGeminiDir, "antigravity", "daemon")
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `go test -v -race ./pkg/models/... ./pkg/config/...`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pkg/models/account.go pkg/models/models_test.go pkg/config/paths.go pkg/config/config_test.go
git commit -m "feat(config,models): add HubActive state field and Hub SSH path helpers"
```

---

### Task 2: StorageEngine `HubActive` Lifecycle & Transactional Helpers

**Files:**
- Modify: `pkg/storage/storage.go:244-282`
- Test: `pkg/storage/storage_test.go`

**Interfaces:**
- Consumes `models.State.HubActive`
- Produces:
  - `(s *StorageEngine) SetHubActiveWithContext(ctx context.Context, name string) error`
  - `(s *StorageEngine) SaveProfileFromTokenBytes(name string, tokenJSON []byte, email, projectID *string, planType string, setCLIActive, setHubActive bool) (*models.AccountMeta, error)`
  - `(s *StorageEngine) GetHubActiveProfile() (*models.AccountMeta, error)`

- [ ] **Step 1: Write failing tests in `pkg/storage/storage_test.go`**

```go
func TestStorageHubActiveAndRemoveClearsHubActive(t *testing.T) {
	storage, _, _ := setupTestStorage(t)

	email := "hub@example.com"
	acct, err := storage.SaveProfileFromTokenBytes(
		"hub-acct",
		[]byte(`{"token":{"access_token":"a","refresh_token":"r"}}`),
		&email,
		nil,
		"STANDARD",
		false,
		true,
	)
	if err != nil {
		t.Fatalf("SaveProfileFromTokenBytes failed: %v", err)
	}
	if acct.Name != "hub-acct" {
		t.Fatalf("unexpected name: %s", acct.Name)
	}

	state, _ := storage.LoadState()
	if state.Active != nil {
		t.Fatalf("expected CLI Active nil when setCLIActive=false, got %v", *state.Active)
	}
	if state.HubActive == nil || *state.HubActive != "hub-acct" {
		t.Fatalf("expected HubActive 'hub-acct', got %v", state.HubActive)
	}

	hubActive, err := storage.GetHubActiveProfile()
	if err != nil || hubActive == nil || hubActive.Name != "hub-acct" {
		t.Fatalf("GetHubActiveProfile failed: %v %v", hubActive, err)
	}

	// RemoveProfile must clear HubActive as well as Active
	if err := storage.RemoveProfile("hub-acct"); err != nil {
		t.Fatalf("RemoveProfile failed: %v", err)
	}
	state, _ = storage.LoadState()
	if state.HubActive != nil {
		t.Fatalf("expected HubActive nil after removal, got %v", *state.HubActive)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test -v ./pkg/storage/...`
Expected: FAIL (`SaveProfileFromTokenBytes` and `GetHubActiveProfile` undefined).

- [ ] **Step 3: Implement `StorageEngine` Hub methods and update `RemoveProfile`**

In `pkg/storage/storage.go`:
1. Add `atomicWriteBytes(targetPath string, data []byte) error` (`0600` temp file + `Sync()` + `Close()` + `os.Rename`).
2. In `RemoveProfile`:
```go
if state.HubActive != nil && *state.HubActive == name {
	state.HubActive = nil
}
```
3. Add methods:
```go
func (s *StorageEngine) SetHubActiveWithContext(ctx context.Context, name string) error {
	return s.Transaction(ctx, func(state *models.State) error {
		acct, ok := state.Accounts[name]
		if !ok {
			return fmt.Errorf("%w: %s", ErrAccountNotFound, name)
		}
		now := time.Now().UTC()
		acct.LastUsedAt = &now
		state.HubActive = &name
		return nil
	})
}

func (s *StorageEngine) SaveProfileFromTokenBytes(name string, tokenJSON []byte, email, projectID *string, planType string, setCLIActive, setHubActive bool) (*models.AccountMeta, error) {
	if planType == "" {
		planType = "STANDARD"
	}
	var savedAcct *models.AccountMeta
	err := s.Transaction(context.Background(), func(state *models.State) error {
		acctDir := filepath.Join(s.AccountsDir, name)
		destToken := filepath.Join(acctDir, "antigravity-oauth-token")
		if err := s.atomicWriteBytes(destToken, tokenJSON); err != nil {
			return fmt.Errorf("failed to write token to vault: %w", err)
		}

		now := time.Now().UTC()
		acct := &models.AccountMeta{
			Name:       name,
			Enabled:    true,
			SavedAt:    &now,
			LastUsedAt: &now,
			Email:      email,
			ProjectID:  projectID,
			PlanType:   planType,
		}
		if existing, ok := state.Accounts[name]; ok && existing.SavedAt != nil {
			acct.SavedAt = existing.SavedAt
			if acct.Email == nil {
				acct.Email = existing.Email
			}
			if acct.ProjectID == nil {
				acct.ProjectID = existing.ProjectID
			}
		}

		state.Accounts[name] = acct
		if setCLIActive && (state.Active == nil || *state.Active == "") {
			state.Active = &name
		}
		if setHubActive {
			state.HubActive = &name
		}
		savedAcct = acct
		return nil
	})
	return savedAcct, err
}

func (s *StorageEngine) GetHubActiveProfile() (*models.AccountMeta, error) {
	state, err := s.LoadState()
	if err != nil {
		return nil, err
	}
	if state.HubActive == nil {
		return nil, nil
	}
	return state.Accounts[*state.HubActive], nil
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `go test -v -race ./pkg/storage/...`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pkg/storage/storage.go pkg/storage/storage_test.go
git commit -m "feat(storage): add HubActive tracking and token byte persistence"
```

---

### Task 3: Hub Token Translation, Divergence Check & Refresh Sync (`pkg/auth`)

**Files:**
- Create: `pkg/auth/hub_token.go`
- Modify: `pkg/auth/refresh.go:151-191`
- Test: `pkg/auth/auth_test.go`

**Interfaces:**
- Produces:
  - `type HubSyncState string` (`HubSyncInSync`, `HubSyncOutOfSync`, `HubSyncMissing`, `HubSyncUntracked`)
  - `type HubSyncInfo struct { State HubSyncState; LiveEmail string; LiveExpiry string; WriterID string }`
  - `SyncVaultToHubToken(vaultTokenPath, hubTokenPath string) (writerID string, err error)`
  - `SyncHubToVaultIfSameAccount(hubTokenPath, vaultTokenPath string) error`
  - `ReadHubTokenForVault(hubTokenPath string) (clean RawTokenData, email string, encoded []byte, err error)`
  - `CheckHubSyncStatus(store *storage.StorageEngine, hubTokenPath string) (*HubSyncInfo, error)`

- [ ] **Step 1: Write failing tests in `pkg/auth/auth_test.go`**

```go
func TestHubTokenTranslationAndSyncStatus(t *testing.T) {
	tmpDir := t.TempDir()
	vaultPath := filepath.Join(tmpDir, "vault-token")
	hubPath := filepath.Join(tmpDir, "jetski-standalone-oauth-token")

	vaultJSON := `{"auth_method":"consumer","id_token_email":"u1@example.com","token":{"access_token":"acc-1","refresh_token":"ref-1","expiry":"2099-01-01T00:00:00Z"}}`
	if err := os.WriteFile(vaultPath, []byte(vaultJSON), 0600); err != nil {
		t.Fatal(err)
	}

	// Case 1: hubPath missing -> generates 43-char URL-safe writer_id
	wid1, err := SyncVaultToHubToken(vaultPath, hubPath)
	if err != nil {
		t.Fatalf("SyncVaultToHubToken failed: %v", err)
	}
	if len(wid1) != 43 {
		t.Fatalf("expected 43-char writer_id, got %d (%q)", len(wid1), wid1)
	}

	// Case 2: hubPath already has writer_id -> preserves existing writer_id
	_ = os.WriteFile(hubPath, []byte(`{"writer_id":"existing-writer-id","token":{"access_token":"old"}}`), 0600)
	wid2, err := SyncVaultToHubToken(vaultPath, hubPath)
	if err != nil {
		t.Fatalf("SyncVaultToHubToken preserve failed: %v", err)
	}
	if wid2 != "existing-writer-id" {
		t.Fatalf("expected preserved writer_id, got %q", wid2)
	}

	// Case 3: ReadHubTokenForVault strips writer_id and extracts email
	clean, email, _, err := ReadHubTokenForVault(hubPath)
	if err != nil {
		t.Fatalf("ReadHubTokenForVault failed: %v", err)
	}
	if _, has := clean["writer_id"]; has {
		t.Fatalf("expected writer_id stripped from vault token")
	}
	if email != "u1@example.com" {
		t.Fatalf("expected u1@example.com, got %q", email)
	}
}

func TestRefreshAccountTokenSyncsHubActive(t *testing.T) {
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "hub-refreshed-access",
			"expires_in":   3600,
		})
	}))
	defer mockServer.Close()

	t.Setenv("PROMUX_OAUTH_TOKEN_URL", mockServer.URL)
	promuxDir := t.TempDir()
	geminiDir := t.TempDir()
	hubGemini := t.TempDir()
	t.Setenv("PROMUX_HUB_GEMINI_DIR", hubGemini)

	// CLI active = alpha, Hub active = beta
	cliLive := filepath.Join(geminiDir, "antigravity-oauth-token")
	_ = os.WriteFile(cliLive, []byte(`{"token":{"access_token":"cli-acc","refresh_token":"cli-ref","expiry":"2099-01-01T00:00:00Z"}}`), 0600)
	store := storage.New(promuxDir, geminiDir)
	_, _ = store.SaveProfile("alpha", nil, nil, "STANDARD")

	_, err := store.SaveProfileFromTokenBytes(
		"beta",
		[]byte(`{"token":{"access_token":"beta-old","refresh_token":"beta-ref","expiry":"2020-01-01T00:00:00Z"}}`),
		nil, nil, "STANDARD", false, true,
	)
	if err != nil {
		t.Fatal(err)
	}

	hubLive := filepath.Join(hubGemini, "jetski-standalone-oauth-token")
	_ = os.WriteFile(hubLive, []byte(`{"writer_id":"keep-me","token":{"access_token":"beta-old","refresh_token":"beta-ref","expiry":"2020-01-01T00:00:00Z"}}`), 0600)

	refreshed, err := RefreshAccountToken(store, "beta", false)
	if err != nil || !refreshed {
		t.Fatalf("expected beta refreshed, got refreshed=%v err=%v", refreshed, err)
	}

	hubData, err := ReadTokenData(hubLive)
	if err != nil {
		t.Fatal(err)
	}
	if ExtractAccessToken(hubData) != "hub-refreshed-access" {
		t.Fatalf("expected hub access token updated, got %q", ExtractAccessToken(hubData))
	}
	if hubData["writer_id"] != "keep-me" {
		t.Fatalf("expected writer_id preserved, got %v", hubData["writer_id"])
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test -v ./pkg/auth/...`
Expected: FAIL (`SyncVaultToHubToken` undefined).

- [ ] **Step 3: Implement `pkg/auth/hub_token.go` and update `pkg/auth/refresh.go`**

Create `pkg/auth/hub_token.go`:
```go
package auth

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"

	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/storage"
)

type HubSyncState string

const (
	HubSyncInSync    HubSyncState = "in-sync"
	HubSyncOutOfSync HubSyncState = "out-of-sync"
	HubSyncMissing   HubSyncState = "missing"
	HubSyncUntracked HubSyncState = "untracked"
)

type HubSyncInfo struct {
	State      HubSyncState `json:"state"`
	LiveEmail  string       `json:"live_email,omitempty"`
	LiveExpiry string       `json:"live_expiry,omitempty"`
	WriterID   string       `json:"writer_id,omitempty"`
}

func generateWriterID() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func writeJSONAtomic(targetPath string, data RawTokenData) error {
	if err := os.MkdirAll(filepath.Dir(targetPath), 0755); err != nil {
		return err
	}
	tmpPath := targetPath + ".tmp"
	f, err := os.OpenFile(tmpPath, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	if err := enc.Encode(data); err != nil {
		_ = f.Close()
		_ = os.Remove(tmpPath)
		return err
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		_ = os.Remove(tmpPath)
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmpPath)
		return err
	}
	return os.Rename(tmpPath, targetPath)
}

func SyncVaultToHubToken(vaultTokenPath, hubTokenPath string) (string, error) {
	vaultData, err := ReadTokenData(vaultTokenPath)
	if err != nil {
		return "", fmt.Errorf("failed to read vault token: %w", err)
	}
	if ExtractAccessToken(vaultData) == "" && ExtractRefreshToken(vaultData) == "" {
		return "", errors.New("vault token contains neither access_token nor refresh_token")
	}

	writerID := ""
	if existing, err := ReadTokenData(hubTokenPath); err == nil {
		if w, ok := existing["writer_id"].(string); ok && w != "" {
			writerID = w
		}
	}
	if writerID == "" {
		writerID, err = generateWriterID()
		if err != nil {
			return "", fmt.Errorf("failed to generate writer_id: %w", err)
		}
	}

	out := maps.Clone(vaultData)
	out["writer_id"] = writerID
	if err := writeJSONAtomic(hubTokenPath, out); err != nil {
		return "", fmt.Errorf("failed to write hub token: %w", err)
	}
	return writerID, nil
}

func ReadHubTokenForVault(hubTokenPath string) (RawTokenData, string, []byte, error) {
	data, err := ReadTokenData(hubTokenPath)
	if err != nil {
		return nil, "", nil, fmt.Errorf("failed to read hub token at %s: %w", hubTokenPath, err)
	}
	if ExtractAccessToken(data) == "" && ExtractRefreshToken(data) == "" {
		return nil, "", nil, errors.New("hub token contains neither access_token nor refresh_token")
	}
	email, _ := data["id_token_email"].(string)
	clean := maps.Clone(data)
	delete(clean, "writer_id")
	encoded, err := json.MarshalIndent(clean, "", "  ")
	if err != nil {
		return nil, "", nil, err
	}
	encoded = append(encoded, '\n')
	return clean, email, encoded, nil
}

func SyncHubToVaultIfSameAccount(hubTokenPath, vaultTokenPath string) error {
	hubData, err := ReadTokenData(hubTokenPath)
	if err != nil {
		return nil
	}
	vaultData, err := ReadTokenData(vaultTokenPath)
	if err != nil {
		return nil
	}
	hubRef := ExtractRefreshToken(hubData)
	vaultRef := ExtractRefreshToken(vaultData)
	if hubRef == "" || vaultRef == "" || hubRef != vaultRef {
		return nil
	}
	clean := maps.Clone(hubData)
	delete(clean, "writer_id")
	return writeJSONAtomic(vaultTokenPath, clean)
}

func CheckHubSyncStatus(store *storage.StorageEngine, hubTokenPath string) (*HubSyncInfo, error) {
	info := &HubSyncInfo{State: HubSyncMissing}
	hubData, err := ReadTokenData(hubTokenPath)
	if err != nil {
		if os.IsNotExist(err) {
			return info, nil
		}
		return nil, err
	}
	info.LiveEmail, _ = hubData["id_token_email"].(string)
	info.LiveExpiry = ExtractExpiry(hubData)
	info.WriterID, _ = hubData["writer_id"].(string)

	state, err := store.LoadState()
	if err != nil {
		return nil, err
	}
	if state.HubActive == nil || *state.HubActive == "" {
		info.State = HubSyncUntracked
		return info, nil
	}
	vaultPath := filepath.Join(store.AccountsDir, *state.HubActive, "antigravity-oauth-token")
	vaultData, err := ReadTokenData(vaultPath)
	if err != nil {
		info.State = HubSyncOutOfSync
		return info, nil
	}
	hubRef := ExtractRefreshToken(hubData)
	vaultRef := ExtractRefreshToken(vaultData)
	if hubRef != "" && vaultRef != "" && hubRef == vaultRef {
		info.State = HubSyncInSync
		return info, nil
	}
	vaultEmail, _ := vaultData["id_token_email"].(string)
	if hubRef == "" && vaultEmail != "" && info.LiveEmail == vaultEmail {
		info.State = HubSyncInSync
		return info, nil
	}
	info.State = HubSyncOutOfSync
	return info, nil
}
```

Update `RefreshAccountToken` in `pkg/auth/refresh.go`:
```go
	isActive := state.Active != nil && *state.Active == accountName
	isHubActive := state.HubActive != nil && *state.HubActive == accountName
	vaultToken := filepath.Join(store.AccountsDir, accountName, "antigravity-oauth-token")

	if isActive {
		SafeSyncLiveToVault(store, accountName)
	}
	if isHubActive {
		_ = SyncHubToVaultIfSameAccount(config.HubLiveTokenFile(""), vaultToken)
	}
	// ... existing refresh logic ...
	if isActive {
		SafeSyncVaultToLive(store, accountName)
	}
	if isHubActive {
		if _, err := SyncVaultToHubToken(vaultToken, config.HubLiveTokenFile("")); err != nil {
			return true, fmt.Errorf("failed to sync refreshed token to hub: %w", err)
		}
	}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `go test -v -race ./pkg/auth/...`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pkg/auth/hub_token.go pkg/auth/refresh.go pkg/auth/auth_test.go
git commit -m "feat(auth): add Hub SSH token translation, sync status, and refresh sync"
```

---

### Task 4: Hub Daemon Discovery & Process Lifecycle (`pkg/hub`)

**Files:**
- Create: `pkg/hub/daemon.go`
- Create: `pkg/hub/process_unix.go`
- Create: `pkg/hub/process_linux.go`
- Create: `pkg/hub/process_darwin.go`
- Create: `pkg/hub/process_windows.go`
- Test: `pkg/hub/process_test.go`

**Interfaces:**
- Produces:
  - `type DaemonInfo struct { FilePath string; PID int; HTTPSPort int; HTTPPort int; LSVersion string }`
  - `type RestartOptions struct { NoRestart bool; ShutdownTimeout time.Duration; RespawnWaitTimeout time.Duration; SelfDetachDelay time.Duration }`
  - `type RestartResult struct { StoppedPIDs []int; RespawnedPIDs []int; Deferred bool; DeferredPIDs []int; NeedsReconnect bool }`
  - `DiscoverDaemons(daemonDir string) ([]DaemonInfo, error)`
  - `IsHubLanguageServerCmdline(args string) bool`
  - `RestartDaemons(ctx context.Context, daemonDir string, opts RestartOptions) (*RestartResult, error)`
  - `RestartPIDs(ctx context.Context, pids []int, daemonDir string, opts RestartOptions) (*RestartResult, error)`

- [ ] **Step 1: Write failing tests in `pkg/hub/process_test.go`**

Include:
1. `TestIsHubLanguageServerCmdline`: verifies positive (`language_server --standalone --subclient_type hub --workspace_id agy-hub-ssh`) and negative (`language_server_linux_x64`, `/usr/bin/bash`, empty) matches.
2. `TestDiscoverDaemonsFiltersStaleAndUnrelated`: writes valid JSON for dead PID `99999999`, valid JSON for current `os.Getpid()` (not a Hub `language_server`), and malformed JSON -> verifies `DiscoverDaemons` returns 0 daemons without error.
3. `TestRestartPIDsGracefulAndSIGKILLFallback`: uses Go's `TestHelperProcess` pattern (`GO_WANT_HELPER_PROCESS=1`):
   - Subtest `"graceful SIGTERM"`: helper sleeps until `SIGTERM`, exits `0` -> `RestartPIDs` stops it via `SIGTERM` within timeout.
   - Subtest `"SIGKILL fallback"`: helper ignores `SIGTERM` (`signal.Ignore(syscall.SIGTERM)`), `ShutdownTimeout: 300 * time.Millisecond` -> `RestartPIDs` falls back to `SIGKILL` and terminates it.
4. `TestIsDescendantOf`: spawns a parent helper -> child helper hierarchy (or checks current test pid against `os.Getppid()`) to verify ancestor walking returns `true` for an ancestor and `false` for unrelated PIDs.

- [ ] **Step 2: Run test to verify it fails**

Run: `go test -v ./pkg/hub/...`
Expected: FAIL (package/files do not exist yet).

- [ ] **Step 3: Implement `daemon.go` and platform process files**

1. `pkg/hub/daemon.go`:
   - `DiscoverDaemons(daemonDir string) ([]DaemonInfo, error)`: globs `filepath.Join(daemonDir, "ls_*.json")`, reads JSON, checks `isProcessAlive(pid)` and `isHubProcess(pid)` (using `readProcessCmdline(pid)` -> `IsHubLanguageServerCmdline`), skips dead/unrelated/corrupt entries.
   - `IsHubLanguageServerCmdline(cmdline string) bool`: checks contains `language_server` AND (`--subclient_type hub` OR `--subclient_type=hub` OR `agy-hub-ssh` OR `.antigravity-hub-server`).
2. `pkg/hub/process_linux.go` (`//go:build linux`):
   - `readProcessCmdline(pid int) (string, error)`: reads `/proc/<pid>/cmdline`, replaces `\x00` with space.
   - `readParentPID(pid int) (int, error)`: reads `/proc/<pid>/stat`, finds last `)` to safely skip comm with spaces, parses 4th field (`ppid`).
3. `pkg/hub/process_darwin.go` (`//go:build darwin`):
   - `readProcessCmdline(pid int) (string, error)`: runs `ps -p <pid> -o args=`.
   - `readParentPID(pid int) (int, error)`: runs `ps -p <pid> -o ppid=`.
4. `pkg/hub/process_unix.go` (`//go:build linux || darwin`):
   - `isProcessAlive(pid int) bool`: `syscall.Kill(pid, 0) == nil`.
   - `IsDescendantOf(targetPIDs []int) bool`: builds map of `targetPIDs`, walks `readParentPID` from `os.Getpid()` up to `pid <= 1` (with cycle/max-depth guard `64`).
   - `RestartDaemons`: calls `DiscoverDaemons`; if none, returns empty `RestartResult`. If `IsDescendantOf` is true, spawns detached `exec.Command(exe, "hub", "_restart-pids", "--delay", delay.String(), pidArgs...)` with `SysProcAttr: &syscall.SysProcAttr{Setsid: true}`, stdio `/dev/null`, `cmd.Start()`, returns `RestartResult{Deferred: true, DeferredPIDs: pids}`. Otherwise calls `RestartPIDs`.
   - `RestartPIDs`: sends `syscall.SIGTERM` to each alive PID, polls `100ms` up to `ShutdownTimeout` (default `5s`), sends `syscall.SIGKILL` to remaining alive PIDs and polls up to `2s`, then polls `DiscoverDaemons` every `200ms` up to `RespawnWaitTimeout` (default `3s`) to populate `RespawnedPIDs` vs `NeedsReconnect`.
5. `pkg/hub/process_windows.go` (`//go:build windows`):
   - Stubs for `readProcessCmdline`, `isProcessAlive`, `IsDescendantOf`, `RestartDaemons`, `RestartPIDs` returning `ErrUnsupported` / empty `RestartResult` so `GOOS=windows go build ./...` passes cleanly.

- [ ] **Step 4: Run tests and cross-compile check**

Run: `go test -v -race ./pkg/hub/... && GOOS=darwin go build ./... && GOOS=windows go build ./...`
Expected: PASS and 0 build errors on all 3 platforms.

- [ ] **Step 5: Commit**

```bash
git add pkg/hub/daemon.go pkg/hub/process_unix.go pkg/hub/process_linux.go pkg/hub/process_darwin.go pkg/hub/process_windows.go pkg/hub/process_test.go
git commit -m "feat(hub): implement Hub daemon discovery and graceful/detached LS restart"
```

---

### Task 5: High-Level Hub Orchestration (`pkg/hub/hub.go`)

**Files:**
- Create: `pkg/hub/hub.go`
- Create: `pkg/hub/hub_test.go`

**Interfaces:**
- Consumes `storage.StorageEngine`, `auth.*`, `config.*`, `hub.DiscoverDaemons`, `hub.RestartDaemons`
- Produces:
  - `type SwitchResult struct { Profile string; HubTokenPath string; WriterID string; Refreshed bool; Restart *RestartResult }`
  - `type RotateResult struct { Success bool; FromAccount *string; ToAccount *string; Reason string; CooldownUntil *time.Time; Refreshed bool; Restart *RestartResult }`
  - `type StatusResult struct { HubActive *string; Email string; PlanType string; Sync *auth.HubSyncInfo; Daemons []DaemonInfo; HubTokenPath string }`
  - `Switch(ctx context.Context, store *storage.StorageEngine, name string, opts RestartOptions) (*SwitchResult, error)`
  - `RotateNext(ctx context.Context, store *storage.StorageEngine, reason string, cooldownMinutes int, opts RestartOptions) (*RotateResult, error)`
  - `Save(store *storage.StorageEngine, name string, email, projectID *string, planType string) (*models.AccountMeta, error)`
  - `Status(store *storage.StorageEngine) (*StatusResult, error)`

- [ ] **Step 1: Write failing tests in `pkg/hub/hub_test.go`**

Test cases:
1. `TestHubSwitchRefreshesExpiredTokenAndPreservesWriterID`: sets up mock OAuth server, expired vaulted profile `alpha`, existing `jetski-standalone-oauth-token` with `writer_id: "w-123"`. Calls `Switch(..., "alpha", RestartOptions{NoRestart: true})`. Verifies vault token & Hub token both refreshed, `writer_id == "w-123"`, and `state.HubActive == "alpha"`.
2. `TestHubSwitchNonExistentProfileDoesNotTouchHubToken`: calls `Switch(..., "missing", ...)` -> returns `storage.ErrAccountNotFound`, leaves `jetski-standalone-oauth-token` untouched.
3. `TestHubRotateNextLRUAndCooldown`: saves `alpha` (HubActive, recent) and `beta` (oldest `LastUsedAt`), calls `RotateNext`. Verifies `alpha` put on cooldown, `HubActive` becomes `beta`, and `jetski-standalone-oauth-token` updated to `beta`'s token.
4. `TestHubSaveExtractsEmailAndStripsWriterID`: writes `jetski-standalone-oauth-token` with `id_token_email` and `writer_id`, calls `Save(store, "gamma", nil, nil, "")`, verifies `gamma` saved with `Email == "u@example.com"`, `writer_id` absent in vault token, and `HubActive == "gamma"`.

- [ ] **Step 2: Run tests to verify fail**

Run: `go test -v ./pkg/hub/...`
Expected: FAIL (`Switch`, `RotateNext`, `Save`, `Status` undefined).

- [ ] **Step 3: Implement `pkg/hub/hub.go`**

Key implementation details:
- `Switch`:
  1. Check `_, err := store.GetProfile(name)` (returns `storage.ErrAccountNotFound` early).
  2. Check expiry on `filepath.Join(store.AccountsDir, name, "antigravity-oauth-token")`; if expired or missing access token, call `auth.RefreshAccountToken(store, name, true)`.
  3. Inside `store.Transaction(ctx, ...)`: call `auth.SyncVaultToHubToken(vaultToken, hubTokenPath)` (atomic write), update `acct.LastUsedAt = &now`, set `state.HubActive = &name`.
  4. **Outside lock:** if `!opts.NoRestart`, call `RestartDaemons(ctx, config.HubDaemonDir(""), opts)`.
- `RotateNext`:
  1. Choose candidate inside `store.Transaction`: read `state.HubActive`, apply cooldown to `*state.HubActive` (if non-empty), select LRU `StateStandby` account excluding current `HubActive`. Return `RotateResult{Success: false, ...}` if none eligible.
  2. Release lock, ensure candidate token freshness (`auth.RefreshAccountToken`), re-acquire lock to re-verify candidate still exists, call `SyncVaultToHubToken`, update `LastUsedAt` & `state.HubActive = &target`.
  3. **Outside lock:** if `!opts.NoRestart`, call `RestartDaemons`.
- `Save`:
  1. Call `auth.ReadHubTokenForVault(hubTokenPath)` -> `(_, emailFromTok, cleanBytes, err)`.
  2. If `email == nil && emailFromTok != ""`, set `email = &emailFromTok`.
  3. Call `store.SaveProfileFromTokenBytes(name, cleanBytes, email, projectID, planType, false, true)` (which atomically saves the clean vault token and sets `state.HubActive = &name`).
- `Status`: loads `state`, gets `HubActive` metadata, calls `auth.CheckHubSyncStatus`, and calls `DiscoverDaemons`.

- [ ] **Step 4: Run tests to verify pass**

Run: `go test -v -race ./pkg/hub/...`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pkg/hub/hub.go pkg/hub/hub_test.go
git commit -m "feat(hub): add high-level Hub SSH switch, rotate, save, and status orchestration"
```

---

### Task 6: CLI Commands & Top-Level `--hub` Integration (`pkg/cmd`)

**Files:**
- Create: `pkg/cmd/hub.go`
- Create: `pkg/cmd/hub_test.go`
- Modify: `pkg/cmd/root.go:41-57`
- Modify: `pkg/cmd/profile.go:33-104,151-221`

**Interfaces:**
- Consumes `pkg/hub`, `pkg/storage`, `pkg/auth`, `pkg/config`
- Produces CLI commands:
  - `promux hub switch <name> [--no-restart]`
  - `promux hub next [--reason ...] [--cooldown ...] [--no-restart]`
  - `promux hub save <name> [--email ...] [--project ...] [--plan ...]`
  - `promux hub whoami`
  - `promux hub status`
  - `promux hub restart`
  - `promux hub _restart-pids [--delay 300ms] <pid...>` (hidden)
- Modifies `promux switch <name> [--hub] [--no-restart]`, `promux next [--hub] [--no-restart]`, `promux list` (`[hub]` badge).

- [ ] **Step 1: Write failing CLI tests in `pkg/cmd/hub_test.go`**

Test cases (using `executeCommand` and `setupTestEnv` patterns from `cmd_test.go` / `e2e_test.go`):
1. `TestCLIHubSaveSwitchNextStatusJSON`:
   - Writes `~/.gemini/jetski-standalone-oauth-token` (`PROMUX_HUB_GEMINI_DIR`), saves as `gui-a` via `promux hub save gui-a --json`.
   - Saves a second CLI profile `cli-b`, runs `promux hub switch cli-b --no-restart --json`, verifies `HubActive == "cli-b"`, `Active` unchanged, `jetski-standalone-oauth-token` updated.
   - Runs `promux hub next --no-restart --json`, verifies rotation back to `gui-a`.
   - Runs `promux hub whoami --json` and `promux hub status --json`, verifies `sync.state == "in-sync"`.
2. `TestCLISwitchAndNextWithHubFlag`:
   - Runs `promux switch cli-b --hub --no-restart --json` and verifies both `Active` and `HubActive` become `cli-b`.
   - Runs `promux next --hub --no-restart --json` and verifies both rotate together.
3. `TestCLIListShowsHubIndicator`:
   - With `Active == "alpha"` and `HubActive == "beta"`, runs `promux list` and asserts output contains `beta [hub]`.
4. `TestCLIHubRejectsUnsupportedToolFlag`:
   - Runs `promux -t claude hub status` -> returns error containing `only supported for tool 'agy'`.

- [ ] **Step 2: Run test to verify it fails**

Run: `go test -v ./pkg/cmd/... -run "TestCLIHub|TestCLISwitchAndNextWithHubFlag|TestCLIListShowsHubIndicator"`
Expected: FAIL (`unknown command "hub"`).

- [ ] **Step 3: Implement `pkg/cmd/hub.go` and update `pkg/cmd/root.go` and `pkg/cmd/profile.go`**

1. `pkg/cmd/hub.go`:
   - Add `requireAgyTool()` helper checking `ToolName == "agy"`.
   - Register `newHubCmd()` on `RootCmd` in `pkg/cmd/root.go`.
   - Implement `switch`, `next`, `save`, `whoami`, `status`, `restart`, and hidden `_restart-pids` subcommands.
   - In `hub switch` / `hub next` / `hub restart`:
     - Write structured JSON or human-readable output to `cmd.OutOrStdout()`.
     - When `res.Restart != nil && res.Restart.NeedsReconnect`: print informational note to `cmd.ErrOrStderr()` when `JSONOut` is true, or `cmd.OutOrStdout()` in human mode.
     - When `res.Restart != nil && res.Restart.Deferred`: output has already been written before detached helper's `300ms` delay triggers `SIGTERM`.
2. `pkg/cmd/profile.go`:
   - In `newListCmd()`: when `state.HubActive != nil && *state.HubActive == name`, append ` [hub]` to the displayed profile name.
   - In `newSwitchCmd()`: add `--hub` and `--no-restart` flags. When `--hub` is set: validate profile exists & refresh if expired before mutating CLI state, run `store.SwitchProfile(name)`, then run `hub.Switch(...)`.
   - In `newNextCmd()`: add `--hub` and `--no-restart` flags. When `--hub` is set: rotate CLI profile via `fo.RotateNext(...)`; if `res.Success && res.ToAccount != nil`, sync & restart Hub to `*res.ToAccount` via `hub.Switch(...)`.

- [ ] **Step 4: Run full test suite, vet, and cross-compile verification**

Run:
```bash
go test -v -race ./... && go vet ./... && make build && GOOS=darwin go build ./... && GOOS=windows go build ./...
```
Expected: All tests pass with 0 race conditions, `go vet` exits 0, `bin/promux` builds cleanly, and `darwin`/`windows` builds pass.

- [ ] **Step 5: Commit**

```bash
git add pkg/cmd/hub.go pkg/cmd/hub_test.go pkg/cmd/root.go pkg/cmd/profile.go
git commit -m "feat(cmd): add promux hub commands and --hub flags for switch/next"
```
