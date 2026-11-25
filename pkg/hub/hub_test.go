package hub

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"testing"
	"time"

	"github.com/alirezaopmc/promux/pkg/auth"
	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/models"
	"github.com/alirezaopmc/promux/pkg/storage"
)

func setupHubTestEnv(t *testing.T) (store *storage.StorageEngine, hubGeminiDir, hubTokenPath, daemonDir string) {
	t.Helper()
	root := t.TempDir()
	promuxDir := filepath.Join(root, "promux")
	geminiDir := filepath.Join(root, "gemini-cli")
	hubGeminiDir = filepath.Join(root, "gemini-hub")

	t.Setenv("PROMUX_HOME", promuxDir)
	t.Setenv("PROMUX_GEMINI_HOME", geminiDir)
	t.Setenv("PROMUX_HUB_GEMINI_DIR", hubGeminiDir)
	t.Setenv("PROMUX_HUB_TOKEN_FILE", "")
	t.Setenv("PROMUX_HUB_DAEMON_DIR", "")

	store = storage.New(promuxDir, geminiDir)
	hubTokenPath = config.HubLiveTokenFile("")
	daemonDir = config.HubDaemonDir("")
	return store, hubGeminiDir, hubTokenPath, daemonDir
}

func makeTestTokenJSON(t *testing.T, accessToken, refreshToken, email string, expiry time.Time, writerID string) []byte {
	t.Helper()
	payload := map[string]any{
		"auth_method": "consumer",
		"token": map[string]any{
			"access_token":  accessToken,
			"refresh_token": refreshToken,
			"expiry":        expiry.UTC().Format(time.RFC3339),
		},
	}
	if email != "" {
		payload["id_token_email"] = email
	}
	if writerID != "" {
		payload["writer_id"] = writerID
	}
	b, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		t.Fatalf("failed to marshal test token: %v", err)
	}
	return append(b, '\n')
}

func TestHubSwitchRefreshesExpiredTokenAndPreservesWriterID(t *testing.T) {
	mockOAuth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		_ = r.ParseForm()
		if r.Form.Get("refresh_token") != "ref-alpha" {
			http.Error(w, "unexpected refresh token", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "alpha-refreshed-acc",
			"expires_in":   3600,
			"token_type":   "Bearer",
		})
	}))
	defer mockOAuth.Close()
	t.Setenv("PROMUX_OAUTH_TOKEN_URL", mockOAuth.URL)

	store, _, hubTokenPath, _ := setupHubTestEnv(t)

	email := "alpha@example.com"
	expired := time.Now().UTC().Add(-1 * time.Hour)
	_, err := store.SaveProfileFromTokenBytes(
		"alpha",
		makeTestTokenJSON(t, "alpha-expired-acc", "ref-alpha", email, expired, ""),
		&email, nil, "PRO", false, false,
	)
	if err != nil {
		t.Fatalf("SaveProfileFromTokenBytes failed: %v", err)
	}

	if err := os.MkdirAll(filepath.Dir(hubTokenPath), 0755); err != nil {
		t.Fatal(err)
	}
	existingHub := makeTestTokenJSON(t, "other-acc", "ref-other", "other@example.com", time.Now().UTC().Add(time.Hour), "w-123")
	if err := os.WriteFile(hubTokenPath, existingHub, 0600); err != nil {
		t.Fatal(err)
	}

	res, err := Switch(context.Background(), store, "alpha", RestartOptions{NoRestart: true})
	if err != nil {
		t.Fatalf("Switch failed: %v", err)
	}
	if res.Profile != "alpha" || res.HubTokenPath != hubTokenPath || res.WriterID != "w-123" || !res.Refreshed {
		t.Fatalf("unexpected SwitchResult: %+v", res)
	}
	if res.Restart != nil {
		t.Fatalf("expected nil Restart when NoRestart=true, got %+v", res.Restart)
	}

	// Verify vault token was refreshed
	vaultData, err := auth.ReadTokenData(filepath.Join(store.AccountsDir, "alpha", "antigravity-oauth-token"))
	if err != nil {
		t.Fatalf("failed to read vault token: %v", err)
	}
	if got := auth.ExtractAccessToken(vaultData); got != "alpha-refreshed-acc" {
		t.Fatalf("expected refreshed vault access_token, got %q", got)
	}

	// Verify Hub live token was refreshed and preserved writer_id
	hubData, err := auth.ReadTokenData(hubTokenPath)
	if err != nil {
		t.Fatalf("failed to read hub token: %v", err)
	}
	if got := auth.ExtractAccessToken(hubData); got != "alpha-refreshed-acc" {
		t.Fatalf("expected refreshed hub access_token, got %q", got)
	}
	if got, _ := hubData["writer_id"].(string); got != "w-123" {
		t.Fatalf("expected writer_id w-123 preserved, got %q", got)
	}

	// Verify state.HubActive updated without touching state.Active
	state, err := store.LoadState()
	if err != nil {
		t.Fatal(err)
	}
	if state.HubActive == nil || *state.HubActive != "alpha" {
		t.Fatalf("expected HubActive alpha, got %v", state.HubActive)
	}
	if state.Active != nil {
		t.Fatalf("expected CLI Active nil, got %v", *state.Active)
	}
}

func TestHubSwitchNonExistentProfileDoesNotTouchHubToken(t *testing.T) {
	store, _, hubTokenPath, _ := setupHubTestEnv(t)

	if err := os.MkdirAll(filepath.Dir(hubTokenPath), 0755); err != nil {
		t.Fatal(err)
	}
	sentinel := makeTestTokenJSON(t, "keep-acc", "keep-ref", "keep@example.com", time.Now().UTC().Add(time.Hour), "w-keep")
	if err := os.WriteFile(hubTokenPath, sentinel, 0600); err != nil {
		t.Fatal(err)
	}

	_, err := Switch(context.Background(), store, "missing", RestartOptions{NoRestart: true})
	if !errors.Is(err, storage.ErrAccountNotFound) {
		t.Fatalf("expected storage.ErrAccountNotFound, got %v", err)
	}

	gotBytes, err := os.ReadFile(hubTokenPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(gotBytes) != string(sentinel) {
		t.Fatalf("hub token modified on failed switch:\ngot:  %s\nwant: %s", string(gotBytes), string(sentinel))
	}

	state, err := store.LoadState()
	if err != nil {
		t.Fatal(err)
	}
	if state.HubActive != nil {
		t.Fatalf("expected HubActive to remain nil, got %v", *state.HubActive)
	}
}

func TestHubSwitchFailedRefreshDoesNotMutateStateOrHubToken(t *testing.T) {
	mockOAuth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"error": "invalid_grant",
		})
	}))
	defer mockOAuth.Close()
	t.Setenv("PROMUX_OAUTH_TOKEN_URL", mockOAuth.URL)

	store, _, hubTokenPath, _ := setupHubTestEnv(t)

	expired := time.Now().UTC().Add(-1 * time.Hour)
	_, err := store.SaveProfileFromTokenBytes(
		"revoked",
		makeTestTokenJSON(t, "old-acc", "revoked-ref", "revoked@example.com", expired, ""),
		nil, nil, "STANDARD", false, false,
	)
	if err != nil {
		t.Fatal(err)
	}

	if err := os.MkdirAll(filepath.Dir(hubTokenPath), 0755); err != nil {
		t.Fatal(err)
	}
	sentinel := makeTestTokenJSON(t, "keep-acc", "keep-ref", "keep@example.com", time.Now().UTC().Add(time.Hour), "w-keep")
	if err := os.WriteFile(hubTokenPath, sentinel, 0600); err != nil {
		t.Fatal(err)
	}

	_, err = Switch(context.Background(), store, "revoked", RestartOptions{NoRestart: true})
	if !errors.Is(err, auth.ErrTokenRevoked) {
		t.Fatalf("expected ErrTokenRevoked, got %v", err)
	}

	gotBytes, _ := os.ReadFile(hubTokenPath)
	if string(gotBytes) != string(sentinel) {
		t.Fatalf("hub token modified despite failed refresh")
	}
	state, _ := store.LoadState()
	if state.HubActive != nil {
		t.Fatalf("expected HubActive nil after failed refresh, got %v", *state.HubActive)
	}
}

func TestHubRotateNextLRUAndCooldown(t *testing.T) {
	store, _, hubTokenPath, _ := setupHubTestEnv(t)
	future := time.Now().UTC().Add(2 * time.Hour)

	for _, name := range []string{"alpha", "beta", "gamma"} {
		email := name + "@example.com"
		_, err := store.SaveProfileFromTokenBytes(
			name,
			makeTestTokenJSON(t, name+"-acc", name+"-ref", email, future, ""),
			&email, nil, "STANDARD", false, false,
		)
		if err != nil {
			t.Fatalf("save %s failed: %v", name, err)
		}
	}

	// Set deterministic LastUsedAt timestamps:
	// beta is oldest, gamma is middle, alpha is newest and HubActive.
	now := time.Now().UTC()
	oldest := now.Add(-3 * time.Hour)
	middle := now.Add(-1 * time.Hour)
	recent := now.Add(-5 * time.Minute)
	err := store.Transaction(context.Background(), func(state *models.State) error {
		state.Accounts["beta"].LastUsedAt = &oldest
		state.Accounts["gamma"].LastUsedAt = &middle
		state.Accounts["alpha"].LastUsedAt = &recent
		active := "alpha"
		state.HubActive = &active
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	res, err := RotateNext(context.Background(), store, "quota exhausted", 30, RestartOptions{NoRestart: true})
	if err != nil {
		t.Fatalf("RotateNext failed: %v", err)
	}
	if !res.Success {
		t.Fatalf("expected RotateNext success, got %+v", res)
	}
	if res.FromAccount == nil || *res.FromAccount != "alpha" {
		t.Fatalf("expected FromAccount alpha, got %v", res.FromAccount)
	}
	if res.ToAccount == nil || *res.ToAccount != "beta" {
		t.Fatalf("expected ToAccount beta (LRU), got %v", res.ToAccount)
	}
	if res.CooldownUntil == nil || res.CooldownUntil.Before(now.Add(25*time.Minute)) {
		t.Fatalf("expected CooldownUntil ~30m in future, got %v", res.CooldownUntil)
	}

	state, err := store.LoadState()
	if err != nil {
		t.Fatal(err)
	}
	if state.HubActive == nil || *state.HubActive != "beta" {
		t.Fatalf("expected HubActive beta, got %v", state.HubActive)
	}
	if st := state.Accounts["alpha"].State(time.Now().UTC()); st != models.StateCooldown {
		t.Fatalf("expected alpha in cooldown, got %s", st)
	}

	hubData, err := auth.ReadTokenData(hubTokenPath)
	if err != nil {
		t.Fatalf("failed to read hub token: %v", err)
	}
	if got := auth.ExtractAccessToken(hubData); got != "beta-acc" {
		t.Fatalf("expected hub token updated to beta-acc, got %q", got)
	}
}

func TestHubRotateNextNoEligibleStandbyAndTieBreak(t *testing.T) {
	store, _, hubTokenPath, _ := setupHubTestEnv(t)
	future := time.Now().UTC().Add(2 * time.Hour)

	// Single profile as HubActive -> no eligible standby
	_, err := store.SaveProfileFromTokenBytes(
		"solo",
		makeTestTokenJSON(t, "solo-acc", "solo-ref", "solo@example.com", future, ""),
		nil, nil, "STANDARD", false, true,
	)
	if err != nil {
		t.Fatal(err)
	}
	res, err := RotateNext(context.Background(), store, "manual", 0, RestartOptions{NoRestart: true})
	if err != nil {
		t.Fatalf("RotateNext returned error: %v", err)
	}
	if res.Success || res.ToAccount != nil {
		t.Fatalf("expected unsuccessful rotation when no standby exists, got %+v", res)
	}
	if res.FromAccount == nil || *res.FromAccount != "solo" {
		t.Fatalf("expected FromAccount solo, got %v", res.FromAccount)
	}

	// Add two nil-LastUsedAt profiles: "zeta" and "aardvark"; tie-break must pick "aardvark"
	for _, name := range []string{"zeta", "aardvark"} {
		_, err := store.SaveProfileFromTokenBytes(
			name,
			makeTestTokenJSON(t, name+"-acc", name+"-ref", name+"@example.com", future, ""),
			nil, nil, "STANDARD", false, false,
		)
		if err != nil {
			t.Fatal(err)
		}
	}
	if err := store.Transaction(context.Background(), func(state *models.State) error {
		state.Accounts["zeta"].LastUsedAt = nil
		state.Accounts["aardvark"].LastUsedAt = nil
		return nil
	}); err != nil {
		t.Fatal(err)
	}

	// Default cooldown (<= 0 -> DefaultCooldownMinutes = 60m)
	before := time.Now().UTC()
	res, err = RotateNext(context.Background(), store, "manual", 0, RestartOptions{NoRestart: true})
	if err != nil {
		t.Fatalf("RotateNext failed: %v", err)
	}
	if !res.Success || res.ToAccount == nil || *res.ToAccount != "aardvark" {
		t.Fatalf("expected rotation to aardvark on nil LastUsedAt tie-break, got %+v", res)
	}
	if res.CooldownUntil == nil || res.CooldownUntil.Before(before.Add(55*time.Minute)) {
		t.Fatalf("expected default 60m cooldown, got %v", res.CooldownUntil)
	}
	hubData, err := auth.ReadTokenData(hubTokenPath)
	if err != nil {
		t.Fatal(err)
	}
	if got := auth.ExtractAccessToken(hubData); got != "aardvark-acc" {
		t.Fatalf("expected hub token updated to aardvark-acc, got %q", got)
	}
}

func TestHubRotateNextRefreshesExpiredCandidateAndLeavesCooldownUntouchedOnFailure(t *testing.T) {
	failRefresh := true
	mockOAuth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if failRefresh {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]any{"error": "invalid_grant"})
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "beta-refreshed-acc",
			"expires_in":   3600,
		})
	}))
	defer mockOAuth.Close()
	t.Setenv("PROMUX_OAUTH_TOKEN_URL", mockOAuth.URL)

	store, _, hubTokenPath, _ := setupHubTestEnv(t)
	future := time.Now().UTC().Add(2 * time.Hour)
	expired := time.Now().UTC().Add(-1 * time.Hour)

	if _, err := store.SaveProfileFromTokenBytes(
		"alpha",
		makeTestTokenJSON(t, "alpha-acc", "alpha-ref", "alpha@example.com", future, ""),
		nil, nil, "STANDARD", false, true,
	); err != nil {
		t.Fatal(err)
	}
	if _, err := store.SaveProfileFromTokenBytes(
		"beta",
		makeTestTokenJSON(t, "beta-old-acc", "beta-ref", "beta@example.com", expired, ""),
		nil, nil, "STANDARD", false, false,
	); err != nil {
		t.Fatal(err)
	}

	// Failed refresh must not put alpha on cooldown or alter HubActive
	_, err := RotateNext(context.Background(), store, "quota", 30, RestartOptions{NoRestart: true})
	if !errors.Is(err, auth.ErrTokenRevoked) {
		t.Fatalf("expected ErrTokenRevoked, got %v", err)
	}
	state, _ := store.LoadState()
	if state.HubActive == nil || *state.HubActive != "alpha" {
		t.Fatalf("expected HubActive alpha unchanged, got %v", state.HubActive)
	}
	if state.Accounts["alpha"].State(time.Now().UTC()) != models.StateStandby {
		t.Fatalf("expected alpha not on cooldown after failed rotation")
	}

	// Once refresh succeeds, rotation succeeds with Refreshed=true
	failRefresh = false
	res, err := RotateNext(context.Background(), store, "quota", 30, RestartOptions{NoRestart: true})
	if err != nil {
		t.Fatalf("RotateNext failed: %v", err)
	}
	if !res.Success || !res.Refreshed || res.ToAccount == nil || *res.ToAccount != "beta" {
		t.Fatalf("unexpected RotateResult: %+v", res)
	}
	hubData, err := auth.ReadTokenData(hubTokenPath)
	if err != nil {
		t.Fatal(err)
	}
	if got := auth.ExtractAccessToken(hubData); got != "beta-refreshed-acc" {
		t.Fatalf("expected refreshed candidate token in hub live file, got %q", got)
	}
}

func TestHubSaveExtractsEmailAndStripsWriterID(t *testing.T) {
	store, _, hubTokenPath, _ := setupHubTestEnv(t)

	if err := os.MkdirAll(filepath.Dir(hubTokenPath), 0755); err != nil {
		t.Fatal(err)
	}
	liveBytes := makeTestTokenJSON(t, "gamma-acc", "gamma-ref", "u@example.com", time.Now().UTC().Add(time.Hour), "w-secret")
	if err := os.WriteFile(hubTokenPath, liveBytes, 0600); err != nil {
		t.Fatal(err)
	}

	acct, err := Save(store, "gamma", nil, nil, "")
	if err != nil {
		t.Fatalf("Save failed: %v", err)
	}
	if acct.Name != "gamma" {
		t.Fatalf("expected Name gamma, got %q", acct.Name)
	}
	if acct.Email == nil || *acct.Email != "u@example.com" {
		t.Fatalf("expected Email u@example.com, got %v", acct.Email)
	}
	if acct.PlanType != "STANDARD" {
		t.Fatalf("expected default PlanType STANDARD, got %q", acct.PlanType)
	}

	vaultPath := filepath.Join(store.AccountsDir, "gamma", "antigravity-oauth-token")
	vaultData, err := auth.ReadTokenData(vaultPath)
	if err != nil {
		t.Fatalf("failed to read vault token: %v", err)
	}
	if _, hasWriterID := vaultData["writer_id"]; hasWriterID {
		t.Fatalf("expected writer_id stripped from vault token, got %v", vaultData["writer_id"])
	}
	if got := auth.ExtractAccessToken(vaultData); got != "gamma-acc" {
		t.Fatalf("expected vault access_token gamma-acc, got %q", got)
	}

	state, err := store.LoadState()
	if err != nil {
		t.Fatal(err)
	}
	if state.HubActive == nil || *state.HubActive != "gamma" {
		t.Fatalf("expected HubActive gamma, got %v", state.HubActive)
	}
	if state.Active != nil {
		t.Fatalf("expected CLI Active untouched (nil), got %v", *state.Active)
	}
}

func TestHubStatusAndSwitchWithDaemonRestart(t *testing.T) {
	store, _, hubTokenPath, daemonDir := setupHubTestEnv(t)

	// 1. Empty status (missing hub token, no daemons)
	st, err := Status(store)
	if err != nil {
		t.Fatalf("Status on empty env failed: %v", err)
	}
	if st.HubActive != nil || st.HubTokenPath != hubTokenPath {
		t.Fatalf("unexpected initial status: %+v", st)
	}
	if st.Sync == nil || st.Sync.State != auth.HubSyncMissing {
		t.Fatalf("expected HubSyncMissing, got %+v", st.Sync)
	}
	if len(st.Daemons) != 0 {
		t.Fatalf("expected 0 daemons, got %+v", st.Daemons)
	}

	// 2. Save a profile from Hub live token and check in-sync status
	if err := os.MkdirAll(filepath.Dir(hubTokenPath), 0755); err != nil {
		t.Fatal(err)
	}
	future := time.Now().UTC().Add(time.Hour)
	if err := os.WriteFile(hubTokenPath, makeTestTokenJSON(t, "acc-1", "ref-1", "one@example.com", future, "w-1"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := Save(store, "one", nil, nil, "PRO"); err != nil {
		t.Fatal(err)
	}

	// Add second profile to switch to
	email2 := "two@example.com"
	if _, err := store.SaveProfileFromTokenBytes("two", makeTestTokenJSON(t, "acc-2", "ref-2", email2, future, ""), &email2, nil, "PRO", false, false); err != nil {
		t.Fatal(err)
	}

	if runtime.GOOS == "windows" {
		st, err = Status(store)
		if err != nil {
			t.Fatalf("Status failed: %v", err)
		}
		if st.HubActive == nil || *st.HubActive != "one" || st.Email != "one@example.com" || st.PlanType != "PRO" {
			t.Fatalf("unexpected status: %+v", st)
		}
		if st.Sync == nil || st.Sync.State != auth.HubSyncInSync {
			t.Fatalf("expected in-sync, got %+v", st.Sync)
		}
		return
	}

	// 3. Start a mock Hub language_server helper process and verify Status + Switch restart orchestration
	if err := os.MkdirAll(daemonDir, 0755); err != nil {
		t.Fatal(err)
	}
	cmd, waitReady := startHelperProcess(t, "graceful-sigterm", nil,
		"language_server", "--standalone", "--subclient_type", "hub", "--workspace_id", "agy-hub-ssh",
	)
	defer func() {
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
		}
		_ = cmd.Wait()
	}()
	waitReady()

	descJSON := fmt.Sprintf(`{"pid":%d,"httpsPort":43001,"httpPort":43002,"lsVersion":"2.35.1","csrfToken":"tok"}`, cmd.Process.Pid)
	if err := os.WriteFile(filepath.Join(daemonDir, "ls_hub.json"), []byte(descJSON), 0600); err != nil {
		t.Fatal(err)
	}

	st, err = Status(store)
	if err != nil {
		t.Fatalf("Status with live daemon failed: %v", err)
	}
	if st.HubActive == nil || *st.HubActive != "one" || st.Email != "one@example.com" || st.PlanType != "PRO" {
		t.Fatalf("unexpected status: %+v", st)
	}
	if st.Sync == nil || st.Sync.State != auth.HubSyncInSync {
		t.Fatalf("expected HubSyncInSync, got %+v", st.Sync)
	}
	if len(st.Daemons) != 1 || st.Daemons[0].PID != cmd.Process.Pid {
		t.Fatalf("expected 1 daemon with pid %d, got %+v", cmd.Process.Pid, st.Daemons)
	}

	swRes, err := Switch(context.Background(), store, "two", RestartOptions{
		ShutdownTimeout:    2 * time.Second,
		RespawnWaitTimeout: 100 * time.Millisecond,
	})
	if err != nil {
		t.Fatalf("Switch with daemon restart failed: %v", err)
	}
	if swRes.Restart == nil || !slices.Contains(swRes.Restart.StoppedPIDs, cmd.Process.Pid) {
		t.Fatalf("expected Restart.StoppedPIDs to contain %d, got %+v", cmd.Process.Pid, swRes.Restart)
	}
	if !swRes.Restart.NeedsReconnect {
		t.Fatalf("expected NeedsReconnect=true when helper did not respawn, got %+v", swRes.Restart)
	}
}
