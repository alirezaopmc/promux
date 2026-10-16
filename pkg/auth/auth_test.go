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
