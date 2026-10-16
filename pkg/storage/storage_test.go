package storage

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"

	"github.com/alirezaopmc/promux/pkg/models"
)

func setupTestStorage(t *testing.T) (*StorageEngine, string, string) {
	promuxDir := t.TempDir()
	geminiDir := t.TempDir()

	liveToken := filepath.Join(geminiDir, "antigravity-oauth-token")
	if err := os.WriteFile(liveToken, []byte(`{"access_token": "token-initial"}`), 0600); err != nil {
		t.Fatalf("failed to write live token: %v", err)
	}

	storage := New(promuxDir, geminiDir)
	return storage, promuxDir, geminiDir
}

func TestStorageSaveAndSwitchProfile(t *testing.T) {
	storage, _, geminiDir := setupTestStorage(t)

	email1 := "user1@example.com"
	proj1 := "proj-1"
	acct1, err := storage.SaveProfile("alpha", &email1, &proj1, "STANDARD")
	if err != nil {
		t.Fatalf("failed to save profile alpha: %v", err)
	}
	if acct1.Name != "alpha" {
		t.Fatalf("expected name alpha, got %s", acct1.Name)
	}

	state, err := storage.LoadState()
	if err != nil {
		t.Fatalf("failed to load state: %v", err)
	}
	if state.Active == nil || *state.Active != "alpha" {
		t.Fatalf("expected active profile 'alpha', got %v", state.Active)
	}

	// Update live token to pretend another login occurred
	liveToken := filepath.Join(geminiDir, "antigravity-oauth-token")
	if err := os.WriteFile(liveToken, []byte(`{"access_token": "token-beta"}`), 0600); err != nil {
		t.Fatalf("failed to overwrite live token: %v", err)
	}

	email2 := "user2@example.com"
	_, err = storage.SaveProfile("beta", &email2, nil, "PRO")
	if err != nil {
		t.Fatalf("failed to save profile beta: %v", err)
	}

	// Switch back to alpha
	if err := storage.SwitchProfile("alpha"); err != nil {
		t.Fatalf("failed to switch to alpha: %v", err)
	}

	// Check that live token was restored to token-initial
	content, err := os.ReadFile(liveToken)
	if err != nil {
		t.Fatalf("failed to read live token: %v", err)
	}
	if string(content) != `{"access_token": "token-initial"}` {
		t.Fatalf("unexpected token content after switch: %s", string(content))
	}

	active, err := storage.GetActiveProfile()
	if err != nil || active == nil || active.Name != "alpha" {
		t.Fatalf("expected active profile alpha, got %v", active)
	}
}

func TestStorageRemoveProfile(t *testing.T) {
	storage, _, _ := setupTestStorage(t)

	_, err := storage.SaveProfile("gamma", nil, nil, "STANDARD")
	if err != nil {
		t.Fatalf("failed to save gamma: %v", err)
	}

	if err := storage.RemoveProfile("gamma"); err != nil {
		t.Fatalf("failed to remove gamma: %v", err)
	}

	state, err := storage.LoadState()
	if err != nil {
		t.Fatalf("failed to load state: %v", err)
	}
	if _, ok := state.Accounts["gamma"]; ok {
		t.Fatalf("gamma was not removed from state")
	}
	if state.Active != nil {
		t.Fatalf("expected nil active profile, got %v", state.Active)
	}
}

func TestStorageTransactionRollback(t *testing.T) {
	storage, _, _ := setupTestStorage(t)

	_, _ = storage.SaveProfile("delta", nil, nil, "STANDARD")

	boomErr := errors.New("something went wrong")
	err := storage.Transaction(context.Background(), func(state *models.State) error {
		delete(state.Accounts, "delta")
		return boomErr
	})

	if !errors.Is(err, boomErr) {
		t.Fatalf("expected boomErr, got %v", err)
	}

	// State on disk should still contain delta
	state, err := storage.LoadState()
	if err != nil {
		t.Fatalf("failed to load state: %v", err)
	}
	if _, ok := state.Accounts["delta"]; !ok {
		t.Fatalf("transaction was not rolled back!")
	}
}

func TestStorageConcurrentTransactions(t *testing.T) {
	storage, _, _ := setupTestStorage(t)

	var wg sync.WaitGroup
	workers := 8
	iterations := 10

	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			for j := 0; j < iterations; j++ {
				_ = storage.Transaction(context.Background(), func(state *models.State) error {
					name := "worker-account"
					acct, ok := state.Accounts[name]
					if !ok {
						acct = &models.AccountMeta{Name: name, Enabled: true}
						state.Accounts[name] = acct
					}
					acct.PlanType = "UPDATED"
					return nil
				})
			}
		}(i)
	}

	wg.Wait()
	state, err := storage.LoadState()
	if err != nil {
		t.Fatalf("failed to load state: %v", err)
	}
	if _, ok := state.Accounts["worker-account"]; !ok {
		t.Fatalf("worker-account missing from state")
	}
}
