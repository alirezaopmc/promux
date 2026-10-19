package quota

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/alirezaopmc/promux/pkg/auth"
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
	eligible, reason := IsEligibleForPrime(qs1, now, false)
	if !eligible {
		t.Errorf("expected qs1 with past reset to be eligible, got reason: %s", reason)
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

func setupTestStorage(t *testing.T) (*storage.StorageEngine, *QuotaCache) {
	tmpDir := t.TempDir()
	promuxHome := filepath.Join(tmpDir, "promux")
	geminiHome := filepath.Join(tmpDir, "gemini")

	_ = os.MkdirAll(promuxHome, 0755)
	_ = os.MkdirAll(geminiHome, 0755)

	store := storage.New(promuxHome, geminiHome)
	cache := NewCache(promuxHome)
	return store, cache
}

func writeTestToken(t *testing.T, path, tokenVal string) {
	_ = os.MkdirAll(filepath.Dir(path), 0755)
	tokenData := map[string]any{
		"token": map[string]any{
			"access_token":  tokenVal,
			"refresh_token": "refresh-" + tokenVal,
			"expiry":        time.Now().UTC().Add(1 * time.Hour).Format(time.RFC3339),
		},
	}
	data, err := json.Marshal(tokenData)
	if err != nil {
		t.Fatalf("failed to marshal token: %v", err)
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatalf("failed to write token: %v", err)
	}
}

func TestPrimeAccount_NotEligible(t *testing.T) {
	store, cache := setupTestStorage(t)

	// Create profile
	writeTestToken(t, store.LiveToken, "live-tok")
	_, err := store.SaveProfile("alpha", nil, nil, "STANDARD")
	if err != nil {
		t.Fatalf("SaveProfile failed: %v", err)
	}

	// Cache with future reset
	future := time.Now().UTC().Add(24 * time.Hour).Format(time.RFC3339)
	qs := &models.QuotaSummary{
		GeminiWeeklyRemaining: 0.9,
		GeminiWeeklyReset:     &future,
	}
	_ = cache.Set("alpha", qs)

	agyCalled := false
	origRunAgy := runAgyPrime
	runAgyPrime = func(ctx context.Context) error {
		agyCalled = true
		return nil
	}
	defer func() { runAgyPrime = origRunAgy }()

	res, err := PrimeAccount(context.Background(), store, cache, "alpha", false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if res.Primed {
		t.Errorf("expected primed=false for ineligible account")
	}
	if agyCalled {
		t.Errorf("agy should not have been called for ineligible account")
	}
}

func TestPrimeAccount_ActiveProfile(t *testing.T) {
	store, cache := setupTestStorage(t)

	writeTestToken(t, store.LiveToken, "active-tok")
	_, err := store.SaveProfile("alpha", nil, nil, "STANDARD")
	if err != nil {
		t.Fatalf("SaveProfile failed: %v", err)
	}

	// Cache with expired reset
	past := time.Now().UTC().Add(-2 * time.Hour).Format(time.RFC3339)
	qs := &models.QuotaSummary{
		GeminiWeeklyRemaining: 1.0,
		GeminiWeeklyReset:     &past,
	}
	_ = cache.Set("alpha", qs)

	agyCalled := false
	origRunAgy := runAgyPrime
	runAgyPrime = func(ctx context.Context) error {
		agyCalled = true
		return nil
	}
	defer func() { runAgyPrime = origRunAgy }()

	newReset := time.Now().UTC().Add(168 * time.Hour).Format(time.RFC3339)
	origQueryQuota := queryQuota
	queryQuota = func(ctx context.Context, s *storage.StorageEngine, account string) (*models.QuotaSummary, error) {
		return &models.QuotaSummary{
			GeminiWeeklyRemaining: 0.999,
			GeminiWeeklyReset:     &newReset,
		}, nil
	}
	defer func() { queryQuota = origQueryQuota }()

	res, err := PrimeAccount(context.Background(), store, cache, "alpha", false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !res.Primed {
		t.Errorf("expected primed=true, got %v (reason: %s, error: %s)", res.Primed, res.Reason, res.Error)
	}
	if !agyCalled {
		t.Errorf("expected agy to be called")
	}
	if res.NewReset == nil || *res.NewReset != newReset {
		t.Errorf("expected NewReset=%s, got %v", newReset, res.NewReset)
	}

	// Cache should have been updated
	cached, ok := cache.Get("alpha")
	if !ok || cached == nil || cached.GeminiWeeklyReset == nil || *cached.GeminiWeeklyReset != newReset {
		t.Errorf("expected cache updated with new reset")
	}
}

func TestPrimeAccount_InactiveProfile_BackupAndRestore(t *testing.T) {
	store, cache := setupTestStorage(t)

	// Save active profile "activeAcct"
	writeTestToken(t, store.LiveToken, "live-active-token")
	_, err := store.SaveProfile("activeAcct", nil, nil, "STANDARD")
	if err != nil {
		t.Fatalf("SaveProfile activeAcct failed: %v", err)
	}

	// Save target profile "targetAcct"
	writeTestToken(t, store.LiveToken, "target-vault-token")
	_, err = store.SaveProfile("targetAcct", nil, nil, "STANDARD")
	if err != nil {
		t.Fatalf("SaveProfile targetAcct failed: %v", err)
	}

	// Ensure "activeAcct" is active
	if err := store.SwitchProfile("activeAcct"); err != nil {
		t.Fatalf("SwitchProfile failed: %v", err)
	}

	// Target has floating 100% quota
	qs := &models.QuotaSummary{
		GeminiWeeklyRemaining: 1.0,
		GeminiWeeklyReset:     nil,
	}
	_ = cache.Set("targetAcct", qs)

	var liveTokenDuringAgy string
	agyCalled := false
	origRunAgy := runAgyPrime
	runAgyPrime = func(ctx context.Context) error {
		agyCalled = true
		data, err := os.ReadFile(store.LiveToken)
		if err != nil {
			return err
		}
		var parsed map[string]any
		_ = json.Unmarshal(data, &parsed)
		tokMap, _ := parsed["token"].(map[string]any)
		liveTokenDuringAgy, _ = tokMap["access_token"].(string)
		return nil
	}
	defer func() { runAgyPrime = origRunAgy }()

	newReset := time.Now().UTC().Add(168 * time.Hour).Format(time.RFC3339)
	origQueryQuota := queryQuota
	queryQuota = func(ctx context.Context, s *storage.StorageEngine, account string) (*models.QuotaSummary, error) {
		return &models.QuotaSummary{
			GeminiWeeklyRemaining: 0.999,
			GeminiWeeklyReset:     &newReset,
		}, nil
	}
	defer func() { queryQuota = origQueryQuota }()

	res, err := PrimeAccount(context.Background(), store, cache, "targetAcct", false)
	if err != nil {
		t.Fatalf("PrimeAccount failed: %v", err)
	}
	if !res.Primed {
		t.Errorf("expected primed=true, got %v", res.Primed)
	}
	if !agyCalled {
		t.Errorf("expected agy to be called")
	}
	if liveTokenDuringAgy != "target-vault-token" {
		t.Errorf("expected live token during agy to be 'target-vault-token', got '%s'", liveTokenDuringAgy)
	}

	// Verify live token was restored to activeAcct's token
	liveData, _ := os.ReadFile(store.LiveToken)
	var parsedLive map[string]any
	_ = json.Unmarshal(liveData, &parsedLive)
	tokMap, _ := parsedLive["token"].(map[string]any)
	restoredTok, _ := tokMap["access_token"].(string)
	if restoredTok != "live-active-token" {
		t.Errorf("expected restored live token to be 'live-active-token', got '%s'", restoredTok)
	}

	// Verify no backup files remain
	files, _ := os.ReadDir(filepath.Dir(store.LiveToken))
	for _, f := range files {
		if filepath.Base(f.Name()) != filepath.Base(store.LiveToken) {
			t.Errorf("unexpected leftover file in live token dir: %s", f.Name())
		}
	}
}

func TestPrimeAccount_FailureRestoresBackup(t *testing.T) {
	store, cache := setupTestStorage(t)

	writeTestToken(t, store.LiveToken, "live-active-token")
	_, _ = store.SaveProfile("activeAcct", nil, nil, "STANDARD")

	writeTestToken(t, store.LiveToken, "target-vault-token")
	_, _ = store.SaveProfile("targetAcct", nil, nil, "STANDARD")

	_ = store.SwitchProfile("activeAcct")

	origRunAgy := runAgyPrime
	runAgyPrime = func(ctx context.Context) error {
		return errors.New("agy ping failed")
	}
	defer func() { runAgyPrime = origRunAgy }()

	_, err := PrimeAccount(context.Background(), store, cache, "targetAcct", true)
	if err == nil {
		t.Errorf("expected error from failed agy run")
	}

	// Verify live token was still restored
	liveData, _ := os.ReadFile(store.LiveToken)
	var parsedLive map[string]any
	_ = json.Unmarshal(liveData, &parsedLive)
	tokMap, _ := parsedLive["token"].(map[string]any)
	restoredTok, _ := tokMap["access_token"].(string)
	if restoredTok != "live-active-token" {
		t.Errorf("expected live token restored to 'live-active-token', got '%s'", restoredTok)
	}
}

func TestPrimeAllEligible(t *testing.T) {
	store, cache := setupTestStorage(t)

	writeTestToken(t, store.LiveToken, "token-a")
	_, _ = store.SaveProfile("acct-a", nil, nil, "STANDARD")

	writeTestToken(t, store.LiveToken, "token-b")
	_, _ = store.SaveProfile("acct-b", nil, nil, "STANDARD")

	_ = store.SwitchProfile("acct-a")

	// acct-a: eligible (past reset)
	past := time.Now().UTC().Add(-1 * time.Hour).Format(time.RFC3339)
	_ = cache.Set("acct-a", &models.QuotaSummary{
		GeminiWeeklyRemaining: 1.0,
		GeminiWeeklyReset:     &past,
	})

	// acct-b: ineligible (future reset)
	future := time.Now().UTC().Add(48 * time.Hour).Format(time.RFC3339)
	_ = cache.Set("acct-b", &models.QuotaSummary{
		GeminiWeeklyRemaining: 0.8,
		GeminiWeeklyReset:     &future,
	})

	primedAccounts := make(map[string]bool)
	origRunAgy := runAgyPrime
	runAgyPrime = func(ctx context.Context) error {
		return nil
	}
	defer func() { runAgyPrime = origRunAgy }()

	origQueryQuota := queryQuota
	queryQuota = func(ctx context.Context, s *storage.StorageEngine, account string) (*models.QuotaSummary, error) {
		primedAccounts[account] = true
		newReset := time.Now().UTC().Add(168 * time.Hour).Format(time.RFC3339)
		return &models.QuotaSummary{
			GeminiWeeklyRemaining: 0.999,
			GeminiWeeklyReset:     &newReset,
		}, nil
	}
	defer func() { queryQuota = origQueryQuota }()

	results, err := PrimeAllEligible(context.Background(), store, cache, false)
	if err != nil {
		t.Fatalf("PrimeAllEligible failed: %v", err)
	}

	if len(results) != 2 {
		t.Fatalf("expected 2 results, got %d", len(results))
	}

	// Verify acct-a was primed and acct-b was skipped
	var resA, resB *PrimeResult
	for i := range results {
		if results[i].Account == "acct-a" {
			resA = &results[i]
		}
		if results[i].Account == "acct-b" {
			resB = &results[i]
		}
	}

	if resA == nil || !resA.Primed {
		t.Errorf("expected acct-a to be primed, got %+v", resA)
	}
	if resB == nil || resB.Primed {
		t.Errorf("expected acct-b to not be primed, got %+v", resB)
	}
}

func TestPrimeAccount_ExpiredTokenRefreshedWithoutDeadlock(t *testing.T) {
	mockOAuth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "fresh-renewed-token",
			"expires_in":   3600,
		})
	}))
	defer mockOAuth.Close()

	t.Setenv("PROMUX_OAUTH_TOKEN_URL", mockOAuth.URL)

	store, cache := setupTestStorage(t)

	// Target profile with expired token
	expiredToken := filepath.Join(store.AccountsDir, "expiredAcct", "antigravity-oauth-token")
	_ = os.MkdirAll(filepath.Dir(expiredToken), 0755)
	expiredData := map[string]any{
		"token": map[string]any{
			"access_token":  "expired-token",
			"refresh_token": "valid-refresh-token",
			"expiry":        "2020-01-01T00:00:00Z",
		},
	}
	data, _ := json.Marshal(expiredData)
	_ = os.WriteFile(expiredToken, data, 0600)

	// Save profile to state
	_ = store.Transaction(context.Background(), func(state *models.State) error {
		now := time.Now().UTC()
		state.Accounts["expiredAcct"] = &models.AccountMeta{
			Name:       "expiredAcct",
			Enabled:    true,
			SavedAt:    &now,
			LastUsedAt: &now,
			PlanType:   "STANDARD",
		}
		return nil
	})

	// Set cache with eligible reset
	past := time.Now().UTC().Add(-1 * time.Hour).Format(time.RFC3339)
	_ = cache.Set("expiredAcct", &models.QuotaSummary{
		GeminiWeeklyRemaining: 1.0,
		GeminiWeeklyReset:     &past,
	})

	var liveTokenDuringAgy string
	origRunAgy := runAgyPrime
	runAgyPrime = func(ctx context.Context) error {
		liveData, err := auth.ReadTokenData(store.LiveToken)
		if err != nil {
			return err
		}
		liveTokenDuringAgy = auth.ExtractAccessToken(liveData)
		return nil
	}
	defer func() { runAgyPrime = origRunAgy }()

	origQueryQuota := queryQuota
	queryQuota = func(ctx context.Context, s *storage.StorageEngine, account string) (*models.QuotaSummary, error) {
		newReset := time.Now().UTC().Add(168 * time.Hour).Format(time.RFC3339)
		return &models.QuotaSummary{
			GeminiWeeklyRemaining: 0.999,
			GeminiWeeklyReset:     &newReset,
		}, nil
	}
	defer func() { queryQuota = origQueryQuota }()

	// Execute with short timeout: if there's a deadlock on store.LockFile, this will fail or time out
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	res, err := PrimeAccount(ctx, store, cache, "expiredAcct", false)
	if err != nil {
		t.Fatalf("PrimeAccount failed: %v", err)
	}
	if !res.Primed {
		t.Errorf("expected primed=true, got %v", res.Primed)
	}
	if liveTokenDuringAgy != "fresh-renewed-token" {
		t.Errorf("expected agy to run with 'fresh-renewed-token', got '%s'", liveTokenDuringAgy)
	}
}

func TestPrimeAllEligibleWithFilter(t *testing.T) {
	store, cache := setupTestStorage(t)

	writeTestToken(t, store.LiveToken, "token-alpha")
	_, _ = store.SaveProfile("alpha", nil, nil, "STANDARD")

	writeTestToken(t, store.LiveToken, "token-beta")
	_, _ = store.SaveProfile("beta", nil, nil, "STANDARD")

	writeTestToken(t, store.LiveToken, "token-gamma")
	_, _ = store.SaveProfile("gamma", nil, nil, "STANDARD")

	_ = store.SwitchProfile("alpha")

	origRunAgy := runAgyPrime
	primedAccounts := make(map[string]bool)
	runAgyPrime = func(ctx context.Context) error {
		return nil
	}
	defer func() { runAgyPrime = origRunAgy }()

	origQueryQuota := queryQuota
	queryQuota = func(ctx context.Context, s *storage.StorageEngine, account string) (*models.QuotaSummary, error) {
		primedAccounts[account] = true
		return &models.QuotaSummary{
			GeminiWeeklyRemaining: 1.0,
			GeminiWeeklyReset:     nil,
		}, nil
	}
	defer func() { queryQuota = origQueryQuota }()

	// Skip "beta"
	results, err := PrimeAllEligibleWithFilter(context.Background(), store, cache, true, func(account string) bool {
		return account == "beta"
	})
	if err != nil {
		t.Fatalf("PrimeAllEligibleWithFilter failed: %v", err)
	}

	if len(results) != 2 {
		t.Fatalf("expected 2 results, got %d", len(results))
	}
	for _, r := range results {
		if r.Account == "beta" {
			t.Errorf("expected beta to be skipped by filter")
		}
	}
	if primedAccounts["beta"] {
		t.Errorf("expected beta never to be touched")
	}
}

