package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/alirezaopmc/promux/pkg/models"
	"github.com/alirezaopmc/promux/pkg/quota"
	"github.com/alirezaopmc/promux/pkg/storage"
)

func TestPrimeCmdHelp(t *testing.T) {
	cmd := newPrimeCmd()
	buf := new(bytes.Buffer)
	cmd.SetOut(buf)
	cmd.SetArgs([]string{"--help"})
	err := cmd.Execute()
	if err != nil {
		t.Fatalf("unexpected error on help: %v", err)
	}
	out := buf.String()
	if !bytes.Contains(buf.Bytes(), []byte("Prime weekly quota")) {
		t.Errorf("expected help text to mention Prime weekly quota, got: %s", out)
	}
}

func TestPrimeCmdFlags(t *testing.T) {
	cmd := newPrimeCmd()

	forceFlag := cmd.Flags().Lookup("force")
	if forceFlag == nil || forceFlag.Shorthand != "f" {
		t.Fatalf("expected --force (-f) flag to be registered")
	}

	dryRunFlag := cmd.Flags().Lookup("dry-run")
	if dryRunFlag == nil || dryRunFlag.Shorthand != "n" {
		t.Fatalf("expected --dry-run (-n) flag to be registered")
	}

	jsonFlag := cmd.Flags().Lookup("json")
	if jsonFlag == nil {
		t.Fatalf("expected --json flag to be registered")
	}
}

func TestPrimeCmd_EmptyVault(t *testing.T) {
	setupTestEnv(t)

	// Plain text mode
	out, err := executeCommand("prime")
	if err != nil {
		t.Fatalf("unexpected error in empty vault: %v", err)
	}
	if !strings.Contains(out, "No profiles registered in vault.") {
		t.Fatalf("expected 'No profiles registered in vault.', got: %q", out)
	}

	// JSON mode
	out, err = executeCommand("prime", "--json")
	if err != nil {
		t.Fatalf("unexpected error in empty vault with --json: %v", err)
	}
	var results []quota.PrimeResult
	if err := json.Unmarshal([]byte(out), &results); err != nil {
		t.Fatalf("failed to parse JSON from empty vault: %v (out: %q)", err, out)
	}
	if len(results) != 0 {
		t.Fatalf("expected 0 results, got %d", len(results))
	}
}

func TestPrimeCmd_TargetNotFound(t *testing.T) {
	setupTestEnv(t)

	_, err := executeCommand("prime", "nonexistent")
	if err == nil {
		t.Fatalf("expected error for nonexistent profile, got nil")
	}
	if !strings.Contains(err.Error(), "profile 'nonexistent' not found in vault") {
		t.Fatalf("unexpected error message: %v", err)
	}
}

func TestPrimeCmd_DryRunAndJSON(t *testing.T) {
	setupTestEnv(t)

	// Register two profiles: alpha and beta
	_, err := executeCommand("save", "alpha", "--email", "alpha@example.com")
	if err != nil {
		t.Fatalf("failed to save alpha: %v", err)
	}
	_, err = executeCommand("save", "beta", "--email", "beta@example.com")
	if err != nil {
		t.Fatalf("failed to save beta: %v", err)
	}

	store := GetStorage()
	cache := quota.NewCache(store.Home)

	// Alpha: 100% weekly, nil reset -> eligible (floating)
	qsAlpha := &models.QuotaSummary{
		GeminiWeeklyRemaining: 1.0,
		GeminiWeeklyReset:     nil,
	}
	_ = cache.Set("alpha", qsAlpha)

	// Beta: active countdown 5 days away -> ineligible
	future := time.Now().UTC().Add(5 * 24 * time.Hour).Format(time.RFC3339)
	qsBeta := &models.QuotaSummary{
		GeminiWeeklyRemaining: 0.8,
		GeminiWeeklyReset:     &future,
	}
	_ = cache.Set("beta", qsBeta)

	// Ensure primeAccountFunc is never called in dry-run
	origPrimeAccount := primeAccountFunc
	primeCalled := false
	primeAccountFunc = func(ctx context.Context, s *storage.StorageEngine, c *quota.QuotaCache, acct string, f bool) (*quota.PrimeResult, error) {
		primeCalled = true
		return nil, errors.New("should not be called in dry run")
	}
	defer func() { primeAccountFunc = origPrimeAccount }()

	t.Run("dry-run text output", func(t *testing.T) {
		out, err := executeCommand("prime", "--dry-run")
		if err != nil {
			t.Fatalf("dry-run failed: %v", err)
		}
		if primeCalled {
			t.Fatalf("primeAccountFunc was called during dry-run")
		}

		if !strings.Contains(out, "Checking weekly quota status across 2 profiles...") {
			t.Errorf("missing header in output: %s", out)
		}
		if !strings.Contains(out, "alpha: weekly reset floating at 100% (would prime)") {
			t.Errorf("expected alpha to be detected as eligible, got: %s", out)
		}
		if !strings.Contains(out, "beta:") || !strings.Contains(out, "skipped") {
			t.Errorf("expected beta to be skipped, got: %s", out)
		}
		if !strings.Contains(out, "Summary: 1 account would be primed, 1 account skipped (dry-run).") {
			t.Errorf("expected summary line, got: %s", out)
		}
	})

	t.Run("dry-run json output", func(t *testing.T) {
		out, err := executeCommand("prime", "--dry-run", "--json")
		if err != nil {
			t.Fatalf("dry-run --json failed: %v", err)
		}

		var res []quota.PrimeResult
		if err := json.Unmarshal([]byte(out), &res); err != nil {
			t.Fatalf("failed to parse JSON: %v (out: %s)", err, out)
		}
		if len(res) != 2 {
			t.Fatalf("expected 2 results, got %d", len(res))
		}

		if res[0].Account != "alpha" || res[0].Primed || res[0].Reason != "weekly reset floating at 100%" {
			t.Errorf("unexpected alpha result: %+v", res[0])
		}
		if res[1].Account != "beta" || res[1].Primed || !strings.Contains(res[1].Reason, "active countdown") {
			t.Errorf("unexpected beta result: %+v", res[1])
		}
	})

	t.Run("dry-run single account", func(t *testing.T) {
		out, err := executeCommand("prime", "alpha", "--dry-run")
		if err != nil {
			t.Fatalf("single account dry-run failed: %v", err)
		}
		if !strings.Contains(out, "Checking weekly quota status for profile 'alpha'...") {
			t.Errorf("missing single profile header: %s", out)
		}
		if !strings.Contains(out, "alpha: weekly reset floating at 100% (would prime)") {
			t.Errorf("missing alpha status: %s", out)
		}
		if !strings.Contains(out, "Summary: 1 account would be primed, 0 accounts skipped (dry-run).") {
			t.Errorf("unexpected summary: %s", out)
		}
	})

	t.Run("dry-run with force overrides ineligible profile", func(t *testing.T) {
		out, err := executeCommand("prime", "beta", "--dry-run", "--force")
		if err != nil {
			t.Fatalf("dry-run with force failed: %v", err)
		}
		if !strings.Contains(out, "beta: forced (would prime)") {
			t.Errorf("expected beta to be forced eligible, got: %s", out)
		}
		if !strings.Contains(out, "Summary: 1 account would be primed, 0 accounts skipped (dry-run).") {
			t.Errorf("unexpected summary: %s", out)
		}
	})
}

func TestPrimeCmd_LiveExecution(t *testing.T) {
	setupTestEnv(t)

	_, err := executeCommand("save", "gamma", "--email", "gamma@example.com")
	if err != nil {
		t.Fatalf("failed to save gamma: %v", err)
	}

	store := GetStorage()
	cache := quota.NewCache(store.Home)

	qsGamma := &models.QuotaSummary{
		GeminiWeeklyRemaining: 1.0,
		GeminiWeeklyReset:     nil,
	}
	_ = cache.Set("gamma", qsGamma)

	newReset := time.Now().UTC().Add(7 * 24 * time.Hour).Format(time.RFC3339)

	origPrimeAccount := primeAccountFunc
	defer func() { primeAccountFunc = origPrimeAccount }()

	t.Run("successful priming", func(t *testing.T) {
		primeAccountFunc = func(ctx context.Context, s *storage.StorageEngine, c *quota.QuotaCache, acct string, f bool) (*quota.PrimeResult, error) {
			return &quota.PrimeResult{
				Account:  acct,
				Primed:   true,
				Reason:   "weekly reset floating at 100%",
				NewReset: &newReset,
			}, nil
		}

		out, err := executeCommand("prime", "gamma")
		if err != nil {
			t.Fatalf("live prime failed: %v", err)
		}

		if !strings.Contains(out, "gamma: weekly reset floating at 100% -> Priming via agy...") {
			t.Errorf("missing priming announcement: %s", out)
		}
		if !strings.Contains(out, "[gamma] Token primed! New weekly reset:") {
			t.Errorf("missing token primed message: %s", out)
		}
		if !strings.Contains(out, "Summary: 1 account primed, 0 accounts skipped.") {
			t.Errorf("unexpected summary: %s", out)
		}
	})

	t.Run("priming failure error isolation", func(t *testing.T) {
		primeAccountFunc = func(ctx context.Context, s *storage.StorageEngine, c *quota.QuotaCache, acct string, f bool) (*quota.PrimeResult, error) {
			return nil, errors.New("simulated agy timeout")
		}

		out, err := executeCommand("prime", "gamma")
		if err != nil {
			t.Fatalf("command itself returned error instead of isolating: %v", err)
		}

		if !strings.Contains(out, "[gamma] Priming failed: simulated agy timeout") {
			t.Errorf("missing priming failure message: %s", out)
		}
		if !strings.Contains(out, "Summary: 0 accounts primed, 0 accounts skipped, 1 error.") {
			t.Errorf("unexpected error summary: %s", out)
		}
	})

	t.Run("live json output", func(t *testing.T) {
		primeAccountFunc = func(ctx context.Context, s *storage.StorageEngine, c *quota.QuotaCache, acct string, f bool) (*quota.PrimeResult, error) {
			return &quota.PrimeResult{
				Account:  acct,
				Primed:   true,
				Reason:   "weekly reset floating at 100%",
				NewReset: &newReset,
			}, nil
		}

		out, err := executeCommand("prime", "gamma", "--json")
		if err != nil {
			t.Fatalf("live prime --json failed: %v", err)
		}

		var res []quota.PrimeResult
		if err := json.Unmarshal([]byte(out), &res); err != nil {
			t.Fatalf("failed to parse JSON: %v (raw: %s)", err, out)
		}
		if len(res) != 1 {
			t.Fatalf("expected 1 result, got %d", len(res))
		}
		if !res[0].Primed || res[0].NewReset == nil || *res[0].NewReset != newReset {
			t.Errorf("unexpected result: %+v", res[0])
		}
	})
}
