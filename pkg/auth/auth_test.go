package auth

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/alirezaopmc/promux/pkg/storage"
)

func TestTokenExtractionAndExpiry(t *testing.T) {
	// Nested style token
	nested := RawTokenData{
		"token": map[string]any{
			"access_token":  "nested-acc",
			"refresh_token": "nested-ref",
			"expiry":        "2026-10-16T12:00:00Z",
		},
	}
	if ExtractAccessToken(nested) != "nested-acc" {
		t.Fatalf("expected nested-acc")
	}
	if ExtractRefreshToken(nested) != "nested-ref" {
		t.Fatalf("expected nested-ref")
	}
	if ExtractExpiry(nested) != "2026-10-16T12:00:00Z" {
		t.Fatalf("expected expiry string")
	}

	// Flat style token
	flat := RawTokenData{
		"access_token":  "flat-acc",
		"refresh_token": "flat-ref",
		"expiry":        "2026-10-16T12:00:00Z",
	}
	if ExtractAccessToken(flat) != "flat-acc" {
		t.Fatalf("expected flat-acc")
	}
	if ExtractRefreshToken(flat) != "flat-ref" {
		t.Fatalf("expected flat-ref")
	}

	// Expiry check
	pastToken := RawTokenData{
		"access_token": "expired",
		"expiry":       time.Now().UTC().Add(-10 * time.Minute).Format(time.RFC3339),
	}
	if !IsTokenExpired(pastToken, 60) {
		t.Fatalf("expected token to be expired")
	}

	futureToken := RawTokenData{
		"access_token": "valid",
		"expiry":       time.Now().UTC().Add(10 * time.Minute).Format(time.RFC3339),
	}
	if IsTokenExpired(futureToken, 60) {
		t.Fatalf("expected token to NOT be expired")
	}
}

func TestSyncActiveTokens(t *testing.T) {
	tmpDir := t.TempDir()
	src := filepath.Join(tmpDir, "src-token")
	dst := filepath.Join(tmpDir, "dst-token")

	tokenData := `{"token": {"access_token": "new-acc", "refresh_token": "shared-ref"}}`
	if err := os.WriteFile(src, []byte(tokenData), 0600); err != nil {
		t.Fatalf("failed to write src: %v", err)
	}

	// Case 1: dst does not exist -> copy succeeds
	if err := SyncActiveTokens(src, dst); err != nil {
		t.Fatalf("sync failed: %v", err)
	}
	content, _ := os.ReadFile(dst)
	if string(content) != tokenData {
		t.Fatalf("dst content mismatch: %s", string(content))
	}

	// Case 2: dst exists with different refresh token -> should NOT overwrite
	diffToken := `{"token": {"access_token": "other-acc", "refresh_token": "diff-ref"}}`
	_ = os.WriteFile(dst, []byte(diffToken), 0600)
	if err := SyncActiveTokens(src, dst); err != nil {
		t.Fatalf("sync failed: %v", err)
	}
	content, _ = os.ReadFile(dst)
	if string(content) != diffToken {
		t.Fatalf("dst should not have been overwritten!")
	}
}

func TestRefreshTokenNativeSuccess(t *testing.T) {
	// Mock Google OAuth server
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		_ = r.ParseForm()
		if r.Form.Get("grant_type") != "refresh_token" {
			http.Error(w, "invalid grant_type", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "refreshed-access-token",
			"expires_in":   3600,
			"token_type":   "Bearer",
		})
	}))
	defer mockServer.Close()

	t.Setenv("PROMUX_OAUTH_TOKEN_URL", mockServer.URL)

	tmpDir := t.TempDir()
	tokenPath := filepath.Join(tmpDir, "token.json")
	initial := RawTokenData{
		"token": map[string]any{
			"access_token":  "old-acc",
			"refresh_token": "valid-ref",
			"expiry":        "2020-01-01T00:00:00Z",
		},
	}
	initialBytes, _ := json.Marshal(initial)
	_ = os.WriteFile(tokenPath, initialBytes, 0600)

	newAcc, revoked, err := RefreshTokenNative(tokenPath, initial)
	if err != nil {
		t.Fatalf("refresh failed: %v", err)
	}
	if revoked {
		t.Fatalf("expected revoked=false")
	}
	if newAcc != "refreshed-access-token" {
		t.Fatalf("expected refreshed-access-token, got %s", newAcc)
	}

	// Verify tokenPath on disk was updated
	diskData, err := ReadTokenData(tokenPath)
	if err != nil {
		t.Fatalf("failed to read disk token: %v", err)
	}
	if ExtractAccessToken(diskData) != "refreshed-access-token" {
		t.Fatalf("disk token not updated: %v", diskData)
	}
}

func TestRefreshTokenRevoked(t *testing.T) {
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"error":             "invalid_grant",
			"error_description": "Token has been expired or revoked.",
		})
	}))
	defer mockServer.Close()

	t.Setenv("PROMUX_OAUTH_TOKEN_URL", mockServer.URL)

	tmpDir := t.TempDir()
	tokenPath := filepath.Join(tmpDir, "token.json")
	initial := RawTokenData{
		"token": map[string]any{
			"access_token":  "old-acc",
			"refresh_token": "revoked-ref",
		},
	}

	_, revoked, err := RefreshTokenNative(tokenPath, initial)
	if !revoked {
		t.Fatalf("expected revoked=true")
	}
	if err != ErrTokenRevoked {
		t.Fatalf("expected ErrTokenRevoked, got %v", err)
	}
}

func TestRefreshAccountToken(t *testing.T) {
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "acc-from-account-refresh",
			"expires_in":   3600,
		})
	}))
	defer mockServer.Close()

	t.Setenv("PROMUX_OAUTH_TOKEN_URL", mockServer.URL)

	promuxDir := t.TempDir()
	geminiDir := t.TempDir()
	liveToken := filepath.Join(geminiDir, "antigravity-oauth-token")

	expiredTokenJSON := `{"token": {"access_token": "old", "refresh_token": "ref-1", "expiry": "2020-01-01T00:00:00Z"}}`
	_ = os.WriteFile(liveToken, []byte(expiredTokenJSON), 0600)

	store := storage.New(promuxDir, geminiDir)
	_, _ = store.SaveProfile("alpha", nil, nil, "STANDARD")

	refreshed, err := RefreshAccountToken(store, "alpha", false)
	if err != nil {
		t.Fatalf("RefreshAccountToken failed: %v", err)
	}
	if !refreshed {
		t.Fatalf("expected token to be refreshed")
	}

	// Verify live token updated
	liveData, _ := ReadTokenData(liveToken)
	if ExtractAccessToken(liveData) != "acc-from-account-refresh" {
		t.Fatalf("expected live token updated, got %s", ExtractAccessToken(liveData))
	}
}

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

func TestCheckHubSyncStatusAllStates(t *testing.T) {
	promuxDir := t.TempDir()
	geminiDir := t.TempDir()
	hubDir := t.TempDir()
	hubPath := filepath.Join(hubDir, "jetski-standalone-oauth-token")
	store := storage.New(promuxDir, geminiDir)

	// 1. HubSyncMissing: hub token file does not exist
	info, err := CheckHubSyncStatus(store, hubPath)
	if err != nil {
		t.Fatalf("CheckHubSyncStatus missing failed: %v", err)
	}
	if info.State != HubSyncMissing {
		t.Fatalf("expected %q, got %q", HubSyncMissing, info.State)
	}

	// 2. HubSyncUntracked: hub token exists, state.HubActive is nil/empty
	hubToken := `{"writer_id":"wid-1","id_token_email":"u1@example.com","token":{"access_token":"acc-1","refresh_token":"ref-1","expiry":"2099-01-01T00:00:00Z"}}`
	if err := os.WriteFile(hubPath, []byte(hubToken), 0600); err != nil {
		t.Fatal(err)
	}
	info, err = CheckHubSyncStatus(store, hubPath)
	if err != nil {
		t.Fatalf("CheckHubSyncStatus untracked failed: %v", err)
	}
	if info.State != HubSyncUntracked {
		t.Fatalf("expected %q, got %q", HubSyncUntracked, info.State)
	}
	if info.LiveEmail != "u1@example.com" || info.WriterID != "wid-1" || info.LiveExpiry != "2099-01-01T00:00:00Z" {
		t.Fatalf("unexpected HubSyncInfo metadata: %+v", info)
	}

	// 3. HubSyncInSync: state.HubActive matches refresh_token
	vaultToken := `{"id_token_email":"u1@example.com","token":{"access_token":"acc-old","refresh_token":"ref-1","expiry":"2099-01-01T00:00:00Z"}}`
	if _, err := store.SaveProfileFromTokenBytes("hubprof", []byte(vaultToken), nil, nil, "STANDARD", false, true); err != nil {
		t.Fatal(err)
	}
	info, err = CheckHubSyncStatus(store, hubPath)
	if err != nil {
		t.Fatalf("CheckHubSyncStatus in-sync failed: %v", err)
	}
	if info.State != HubSyncInSync {
		t.Fatalf("expected %q, got %q", HubSyncInSync, info.State)
	}

	// 3b. HubSyncInSync fallback: hub has no refresh_token, matches by email
	hubNoRef := `{"writer_id":"wid-1","id_token_email":"u1@example.com","token":{"access_token":"acc-1","expiry":"2099-01-01T00:00:00Z"}}`
	if err := os.WriteFile(hubPath, []byte(hubNoRef), 0600); err != nil {
		t.Fatal(err)
	}
	info, err = CheckHubSyncStatus(store, hubPath)
	if err != nil {
		t.Fatalf("CheckHubSyncStatus email fallback failed: %v", err)
	}
	if info.State != HubSyncInSync {
		t.Fatalf("expected %q on email fallback, got %q", HubSyncInSync, info.State)
	}

	// 4. HubSyncOutOfSync: hub refresh_token differs from vault
	hubDiff := `{"writer_id":"wid-2","id_token_email":"u2@example.com","token":{"access_token":"acc-2","refresh_token":"ref-other","expiry":"2099-01-01T00:00:00Z"}}`
	if err := os.WriteFile(hubPath, []byte(hubDiff), 0600); err != nil {
		t.Fatal(err)
	}
	info, err = CheckHubSyncStatus(store, hubPath)
	if err != nil {
		t.Fatalf("CheckHubSyncStatus out-of-sync failed: %v", err)
	}
	if info.State != HubSyncOutOfSync {
		t.Fatalf("expected %q, got %q", HubSyncOutOfSync, info.State)
	}

	// 4b. HubSyncOutOfSync: vault token missing for HubActive profile
	_ = os.Remove(filepath.Join(store.AccountsDir, "hubprof", "antigravity-oauth-token"))
	info, err = CheckHubSyncStatus(store, hubPath)
	if err != nil {
		t.Fatalf("CheckHubSyncStatus missing vault failed: %v", err)
	}
	if info.State != HubSyncOutOfSync {
		t.Fatalf("expected %q when vault token missing, got %q", HubSyncOutOfSync, info.State)
	}
}

func TestSyncHubToVaultIfSameAccount(t *testing.T) {
	tmpDir := t.TempDir()
	hubPath := filepath.Join(tmpDir, "hub-token")
	vaultPath := filepath.Join(tmpDir, "vault-token")

	// Same refresh_token -> copies hub to vault and strips writer_id
	_ = os.WriteFile(hubPath, []byte(`{"writer_id":"wid-1","token":{"access_token":"hub-new","refresh_token":"ref-same"}}`), 0600)
	_ = os.WriteFile(vaultPath, []byte(`{"token":{"access_token":"vault-old","refresh_token":"ref-same"}}`), 0600)
	if err := SyncHubToVaultIfSameAccount(hubPath, vaultPath); err != nil {
		t.Fatalf("SyncHubToVaultIfSameAccount failed: %v", err)
	}
	vaultData, err := ReadTokenData(vaultPath)
	if err != nil {
		t.Fatal(err)
	}
	if ExtractAccessToken(vaultData) != "hub-new" {
		t.Fatalf("expected vault access_token updated to hub-new, got %q", ExtractAccessToken(vaultData))
	}
	if _, has := vaultData["writer_id"]; has {
		t.Fatalf("expected writer_id stripped from vault token")
	}

	// Different refresh_token -> leaves vault untouched
	_ = os.WriteFile(hubPath, []byte(`{"writer_id":"wid-2","token":{"access_token":"hub-other","refresh_token":"ref-diff"}}`), 0600)
	if err := SyncHubToVaultIfSameAccount(hubPath, vaultPath); err != nil {
		t.Fatalf("SyncHubToVaultIfSameAccount diff failed: %v", err)
	}
	vaultData, _ = ReadTokenData(vaultPath)
	if ExtractAccessToken(vaultData) != "hub-new" {
		t.Fatalf("expected vault token unchanged on different refresh_token, got %q", ExtractAccessToken(vaultData))
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
