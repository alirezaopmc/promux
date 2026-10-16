package failover

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/alirezaopmc/promux/pkg/models"
	"github.com/alirezaopmc/promux/pkg/storage"
)

func setupTestFailover(t *testing.T) (*FailoverEngine, *storage.StorageEngine) {
	promuxDir := t.TempDir()
	geminiDir := t.TempDir()

	liveToken := filepath.Join(geminiDir, "antigravity-oauth-token")
	_ = os.WriteFile(liveToken, []byte(`{"access_token": "token-1"}`), 0600)

	store := storage.New(promuxDir, geminiDir)
	return New(store), store
}

func TestFailoverRotateNext(t *testing.T) {
	engine, store := setupTestFailover(t)

	// Save accounts
	_, _ = store.SaveProfile("acct-1", nil, nil, "STANDARD")

	liveToken := store.LiveToken
	_ = os.WriteFile(liveToken, []byte(`{"access_token": "token-2"}`), 0600)
	_, _ = store.SaveProfile("acct-2", nil, nil, "STANDARD")

	_ = os.WriteFile(liveToken, []byte(`{"access_token": "token-3"}`), 0600)
	_, _ = store.SaveProfile("acct-3", nil, nil, "STANDARD")

	// Set active to acct-1
	_ = store.SwitchProfile("acct-1")

	// RotateNext should rotate from acct-1 to acct-2 (LRU)
	res, err := engine.RotateNext("quota exhausted", 60)
	if err != nil {
		t.Fatalf("RotateNext failed: %v", err)
	}
	if !res.Success {
		t.Fatalf("expected success, got failure: %s", res.Reason)
	}
	if *res.FromAccount != "acct-1" || *res.ToAccount != "acct-2" {
		t.Fatalf("expected acct-1 -> acct-2, got %s -> %s", *res.FromAccount, *res.ToAccount)
	}

	// Verify acct-1 is in cooldown
	state, _ := store.LoadState()
	acct1 := state.Accounts["acct-1"]
	if acct1.State(time.Now().UTC()) != models.StateCooldown {
		t.Fatalf("expected acct-1 in cooldown, got %s", acct1.State(time.Now().UTC()))
	}

	// Active is now acct-2
	if *state.Active != "acct-2" {
		t.Fatalf("expected active acct-2, got %s", *state.Active)
	}
}

func TestFailoverNoEligibleStandby(t *testing.T) {
	engine, store := setupTestFailover(t)

	_, _ = store.SaveProfile("only-acct", nil, nil, "STANDARD")

	res, err := engine.RotateNext("test", 60)
	if err != nil {
		t.Fatalf("RotateNext returned error: %v", err)
	}
	if res.Success {
		t.Fatalf("expected Success=false when no standby accounts available")
	}
}
