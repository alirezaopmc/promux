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

	t.Run("priming failure clean output vs verbose", func(t *testing.T) {
		primeAccountFunc = func(ctx context.Context, s *storage.StorageEngine, c *quota.QuotaCache, acct string, f bool) (*quota.PrimeResult, error) {
			return nil, &quota.PrimeExecError{
				Err:    errors.New("exit status 1"),
				Output: "error: Eligibility check failed: Your current account is not eligible for Antigravity, because it is not currently available in your location.",
			}
		}

		// Clean mode (default)
		out, err := executeCommand("prime", "gamma")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		expectedClean := "[gamma] Priming failed: Eligibility check failed: Your current account is not eligible for Antigravity, because it is not currently available in your location."
		if !strings.Contains(out, expectedClean) {
			t.Fatalf("expected clean priming failure %q, got: %s", expectedClean, out)
		}
		if strings.Contains(out, "exit status 1") || strings.Contains(out, "agy command failed") {
			t.Fatalf("clean output should not contain verbose exit status: %s", out)
		}

		// Verbose mode (-v)
		outV, err := executeCommand("prime", "gamma", "-v")
		if err != nil {
			t.Fatalf("unexpected error with -v: %v", err)
		}
		if !strings.Contains(outV, "agy command failed: exit status 1 (output: error: Eligibility check failed:") {
			t.Fatalf("expected verbose priming failure with -v, got: %s", outV)
		}
	})

	t.Run("quota query failure clean output vs verbose", func(t *testing.T) {
		// Evict cache to force queryQuotaFunc
		_ = cache.Invalidate("gamma")
		defer func() { _ = cache.Set("gamma", qsGamma) }()
		origQuery := queryQuotaFunc
		defer func() { queryQuotaFunc = origQuery }()

		rawJSON := `{
  "error": {
    "code": 403,
    "message": "You do not have a valid license of this product. Please contact your administrator to request a license. (#3501)",
    "status": "PERMISSION_DENIED"
  }
}`
		queryQuotaFunc = func(ctx context.Context, store *storage.StorageEngine, account string) (*models.QuotaSummary, error) {
			return nil, quota.ParseAPIError(403, "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary", []byte(rawJSON))
		}

		// Clean mode (default)
		out, err := executeCommand("prime", "gamma")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		expectedClean := "gamma: failed to query quota: HTTP 403 (PERMISSION_DENIED): You do not have a valid license of this product (#3501)"
		if !strings.Contains(out, expectedClean) {
			t.Fatalf("expected clean quota error %q, got: %s", expectedClean, out)
		}
		if strings.Contains(out, "{\n") || strings.Contains(out, "cloudcode-pa.googleapis.com") {
			t.Fatalf("clean output must not contain JSON or full URL: %s", out)
		}

		// Verbose mode (--verbose)
		outV, err := executeCommand("prime", "gamma", "--verbose")
		if err != nil {
			t.Fatalf("unexpected error with --verbose: %v", err)
		}
		if !strings.Contains(outV, "HTTP 403 from https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary:") {
			t.Fatalf("expected verbose quota error with --verbose, got: %s", outV)
		}
	})

	t.Run("dry-run with query error reports errors in summary", func(t *testing.T) {
		_ = cache.Invalidate("gamma")
		defer func() { _ = cache.Set("gamma", qsGamma) }()
		origQuery := queryQuotaFunc
		defer func() { queryQuotaFunc = origQuery }()

		queryQuotaFunc = func(ctx context.Context, store *storage.StorageEngine, account string) (*models.QuotaSummary, error) {
			return nil, errors.New("simulated network failure")
		}

		out, err := executeCommand("prime", "gamma", "--dry-run")
		if err != nil {
			t.Fatalf("unexpected error in dry-run with error: %v", err)
		}
		if !strings.Contains(out, "gamma: failed to query quota: simulated network failure") {
			t.Fatalf("missing failure message: %s", out)
		}
		if !strings.Contains(out, "Summary: 0 accounts would be primed, 0 accounts skipped, 1 error (dry-run).") {
			t.Fatalf("expected summary to include 1 error, got: %s", out)
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

func TestQuotaCmdPrimeFlag(t *testing.T) {
	cmd := newQuotaCmd()
	flag := cmd.Flags().Lookup("prime")
	if flag == nil {
		t.Fatal("expected --prime flag to be registered on quota command")
	}
	if flag.Shorthand != "p" {
		t.Errorf("expected --prime flag shorthand to be 'p', got %q", flag.Shorthand)
	}
}

func TestQuotaCmd_PrimeAllAccounts(t *testing.T) {
	setupTestEnv(t)

	_, err := executeCommand("save", "alpha", "--email", "alpha@example.com")
	if err != nil {
		t.Fatalf("failed to save alpha: %v", err)
	}
	_, err = executeCommand("save", "beta", "--email", "beta@example.com")
	if err != nil {
		t.Fatalf("failed to save beta: %v", err)
	}

	newReset := time.Now().UTC().Add(7 * 24 * time.Hour).Format(time.RFC3339)
	store := GetStorage()
	cache := quota.NewCache(store.Home)

	qsBeta := &models.QuotaSummary{
		Gemini5hRemaining:         1.0,
		GeminiWeeklyRemaining:     0.8,
		ThirdParty5hRemaining:     1.0,
		ThirdPartyWeeklyRemaining: 0.8,
	}
	_ = cache.Set("beta", qsBeta)

	origPrimeAll := primeAllEligibleFunc
	defer func() { primeAllEligibleFunc = origPrimeAll }()

	primeAllCalled := false
	primeAllEligibleFunc = func(ctx context.Context, s *storage.StorageEngine, c *quota.QuotaCache, force bool) ([]quota.PrimeResult, error) {
		primeAllCalled = true
		qsAlpha := &models.QuotaSummary{
			Gemini5hRemaining:         1.0,
			GeminiWeeklyRemaining:     1.0,
			GeminiWeeklyReset:         &newReset,
			ThirdParty5hRemaining:     1.0,
			ThirdPartyWeeklyRemaining: 1.0,
		}
		_ = c.Set("alpha", qsAlpha)
		return []quota.PrimeResult{
			{Account: "alpha", Primed: true, NewReset: &newReset},
			{Account: "beta", Primed: false, Reason: "active countdown"},
		}, nil
	}

	out, err := executeCommand("quota", "--all", "--prime")
	if err != nil {
		t.Fatalf("quota --all --prime failed: %v (out: %s)", err, out)
	}
	if !primeAllCalled {
		t.Fatalf("expected primeAllEligibleFunc to be called")
	}
	if !strings.Contains(out, "⚡ Primed weekly quota for profile 'alpha'") {
		t.Errorf("expected announcement for primed alpha, got: %s", out)
	}
	if !strings.Contains(out, "alpha") || !strings.Contains(out, "beta") {
		t.Errorf("expected table to contain profiles alpha and beta, got: %s", out)
	}

	// Also test -p shorthand
	primeAllCalled = false
	out, err = executeCommand("quota", "--all", "-p")
	if err != nil {
		t.Fatalf("quota --all -p failed: %v", err)
	}
	if !primeAllCalled {
		t.Fatalf("expected primeAllEligibleFunc to be called for -p")
	}
}

func TestQuotaCmd_PrimeAllAccounts_JSON(t *testing.T) {
	setupTestEnv(t)

	_, err := executeCommand("save", "alpha", "--email", "alpha@example.com")
	if err != nil {
		t.Fatalf("failed to save alpha: %v", err)
	}

	newReset := time.Now().UTC().Add(7 * 24 * time.Hour).Format(time.RFC3339)

	origPrimeAll := primeAllEligibleFunc
	defer func() { primeAllEligibleFunc = origPrimeAll }()

	primeAllEligibleFunc = func(ctx context.Context, s *storage.StorageEngine, c *quota.QuotaCache, force bool) ([]quota.PrimeResult, error) {
		qsAlpha := &models.QuotaSummary{
			Gemini5hRemaining:         1.0,
			GeminiWeeklyRemaining:     1.0,
			GeminiWeeklyReset:         &newReset,
			ThirdParty5hRemaining:     1.0,
			ThirdPartyWeeklyRemaining: 1.0,
		}
		_ = c.Set("alpha", qsAlpha)
		return []quota.PrimeResult{
			{Account: "alpha", Primed: true, NewReset: &newReset},
		}, nil
	}

	out, err := executeCommand("quota", "--all", "--prime", "--json")
	if err != nil {
		t.Fatalf("quota --all --prime --json failed: %v", err)
	}

	// Must be valid JSON without prefixed text
	var records []AccountQuotaRecord
	if err := json.Unmarshal([]byte(out), &records); err != nil {
		t.Fatalf("failed to unmarshal JSON output: %v (raw: %q)", err, out)
	}
	if len(records) != 1 {
		t.Fatalf("expected 1 record, got %d", len(records))
	}
	if records[0].Account != "alpha" {
		t.Errorf("expected account 'alpha', got %s", records[0].Account)
	}
	if records[0].Gemini == nil || records[0].Gemini.ResetWeekly == nil || *records[0].Gemini.ResetWeekly != newReset {
		t.Errorf("expected updated Gemini reset timestamp, got: %+v", records[0].Gemini)
	}
}

func TestQuotaCmd_PrimeSingleAccount(t *testing.T) {
	setupTestEnv(t)

	_, err := executeCommand("save", "gamma", "--email", "gamma@example.com")
	if err != nil {
		t.Fatalf("failed to save gamma: %v", err)
	}

	newReset := time.Now().UTC().Add(7 * 24 * time.Hour).Format(time.RFC3339)

	origPrimeAccount := primeAccountFunc
	defer func() { primeAccountFunc = origPrimeAccount }()

	t.Run("successful prime and display", func(t *testing.T) {
		primeCalled := false
		primeAccountFunc = func(ctx context.Context, s *storage.StorageEngine, c *quota.QuotaCache, acct string, f bool) (*quota.PrimeResult, error) {
			primeCalled = true
			if acct != "gamma" {
				t.Errorf("expected target 'gamma', got %s", acct)
			}
			qsGamma := &models.QuotaSummary{
				Gemini5hRemaining:         1.0,
				GeminiWeeklyRemaining:     1.0,
				GeminiWeeklyReset:         &newReset,
				ThirdParty5hRemaining:     1.0,
				ThirdPartyWeeklyRemaining: 1.0,
			}
			_ = c.Set("gamma", qsGamma)
			return &quota.PrimeResult{
				Account:  acct,
				Primed:   true,
				NewReset: &newReset,
			}, nil
		}

		out, err := executeCommand("quota", "gamma", "--prime")
		if err != nil {
			t.Fatalf("quota gamma --prime failed: %v (out: %s)", err, out)
		}
		if !primeCalled {
			t.Fatalf("expected primeAccountFunc to be called")
		}
		if !strings.Contains(out, "⚡ Primed weekly quota for profile 'gamma'") {
			t.Errorf("missing primed notice in output: %s", out)
		}
		if !strings.Contains(out, "Quota Summary for Profile: gamma") {
			t.Errorf("missing quota summary header: %s", out)
		}
	})

	t.Run("single account json output", func(t *testing.T) {
		primeAccountFunc = func(ctx context.Context, s *storage.StorageEngine, c *quota.QuotaCache, acct string, f bool) (*quota.PrimeResult, error) {
			qsGamma := &models.QuotaSummary{
				Gemini5hRemaining:         1.0,
				GeminiWeeklyRemaining:     1.0,
				GeminiWeeklyReset:         &newReset,
				ThirdParty5hRemaining:     1.0,
				ThirdPartyWeeklyRemaining: 1.0,
			}
			_ = c.Set("gamma", qsGamma)
			return &quota.PrimeResult{
				Account:  acct,
				Primed:   true,
				NewReset: &newReset,
			}, nil
		}

		out, err := executeCommand("quota", "gamma", "--prime", "--json")
		if err != nil {
			t.Fatalf("quota gamma --prime --json failed: %v", err)
		}
		var qs models.QuotaSummary
		if err := json.Unmarshal([]byte(out), &qs); err != nil {
			t.Fatalf("failed to unmarshal JSON: %v (raw: %q)", err, out)
		}
		if qs.GeminiWeeklyReset == nil || *qs.GeminiWeeklyReset != newReset {
			t.Errorf("unexpected weekly reset in json: %+v", qs)
		}
	})

	t.Run("ineligible account skips announcement", func(t *testing.T) {
		primeAccountFunc = func(ctx context.Context, s *storage.StorageEngine, c *quota.QuotaCache, acct string, f bool) (*quota.PrimeResult, error) {
			qsGamma := &models.QuotaSummary{
				Gemini5hRemaining:         0.9,
				GeminiWeeklyRemaining:     0.9,
				ThirdParty5hRemaining:     0.9,
				ThirdPartyWeeklyRemaining: 0.9,
			}
			_ = c.Set("gamma", qsGamma)
			return &quota.PrimeResult{
				Account: acct,
				Primed:  false,
				Reason:  "active countdown",
			}, nil
		}

		out, err := executeCommand("quota", "gamma", "--prime")
		if err != nil {
			t.Fatalf("quota gamma --prime failed: %v", err)
		}
		if strings.Contains(out, "⚡ Primed weekly quota") {
			t.Errorf("should not announce priming for ineligible account: %s", out)
		}
		if !strings.Contains(out, "Quota Summary for Profile: gamma") {
			t.Errorf("expected quota summary: %s", out)
		}
	})
}

func TestQuotaCmd_PrimeAllAccounts_WithError(t *testing.T) {
	setupTestEnv(t)

	_, err := executeCommand("save", "alpha", "--email", "alpha@example.com")
	if err != nil {
		t.Fatalf("failed to save alpha: %v", err)
	}

	origPrimeAll := primeAllEligibleFunc
	defer func() { primeAllEligibleFunc = origPrimeAll }()

	primeAllEligibleFunc = func(ctx context.Context, s *storage.StorageEngine, c *quota.QuotaCache, force bool) ([]quota.PrimeResult, error) {
		qsAlpha := &models.QuotaSummary{
			Gemini5hRemaining:     0.5,
			GeminiWeeklyRemaining: 0.5,
		}
		_ = c.Set("alpha", qsAlpha)
		return []quota.PrimeResult{
			{Account: "alpha", Primed: false, Error: "simulated lock failure"},
		}, nil
	}

	out, err := executeCommand("quota", "--all", "--prime")
	if err != nil {
		t.Fatalf("unexpected command failure: %v", err)
	}
	if !strings.Contains(out, "✗ Failed to prime profile 'alpha': simulated lock failure") {
		t.Errorf("expected error warning in output, got: %s", out)
	}
	if !strings.Contains(out, "alpha") {
		t.Errorf("expected table to still render alpha, got: %s", out)
	}
}


