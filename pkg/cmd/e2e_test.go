package cmd

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alirezaopmc/promux/pkg/auth"
	"github.com/alirezaopmc/promux/pkg/models"
)

func setupTestEnv(t *testing.T) (promuxDir, geminiDir, liveToken string) {
	t.Helper()
	tmpDir := t.TempDir()
	promuxDir = filepath.Join(tmpDir, "promux")
	geminiDir = filepath.Join(tmpDir, "gemini")
	if err := os.MkdirAll(promuxDir, 0755); err != nil {
		t.Fatalf("failed to create promuxDir: %v", err)
	}
	if err := os.MkdirAll(geminiDir, 0755); err != nil {
		t.Fatalf("failed to create geminiDir: %v", err)
	}

	t.Setenv("PROMUX_HOME", promuxDir)
	t.Setenv("PROMUX_GEMINI_HOME", geminiDir)

	liveToken = filepath.Join(geminiDir, "antigravity-oauth-token")
	initialToken := map[string]any{
		"token": map[string]any{
			"access_token":  "initial-live-token",
			"refresh_token": "mock-refresh-token",
			"expiry":        time.Now().UTC().Add(24 * time.Hour).Format(time.RFC3339),
		},
	}
	data, err := json.Marshal(initialToken)
	if err != nil {
		t.Fatalf("failed to marshal initial token: %v", err)
	}
	if err := os.WriteFile(liveToken, data, 0600); err != nil {
		t.Fatalf("failed to write live token: %v", err)
	}

	return promuxDir, geminiDir, liveToken
}

// -----------------------------------------------------------------------------
// 1. Version Command
// -----------------------------------------------------------------------------

func TestE2E_CLIVersion(t *testing.T) {
	t.Run("standard version", func(t *testing.T) {
		out, err := executeCommand("version")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if !strings.Contains(out, "promux version 1.0.0") {
			t.Fatalf("expected version output to contain 'promux version 1.0.0', got: %q", out)
		}
	})

	t.Run("version --json", func(t *testing.T) {
		out, err := executeCommand("version", "--json")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		var res map[string]string
		if err := json.Unmarshal([]byte(out), &res); err != nil {
			t.Fatalf("failed to parse JSON output: %v (raw: %q)", err, out)
		}
		if res["version"] != "1.0.0" {
			t.Fatalf("expected version 1.0.0, got: %s", res["version"])
		}
	})

	t.Run("global --json version flag position", func(t *testing.T) {
		out, err := executeCommand("--json", "version")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		var res map[string]string
		if err := json.Unmarshal([]byte(out), &res); err != nil {
			t.Fatalf("failed to parse JSON output: %v (raw: %q)", err, out)
		}
		if res["version"] != "1.0.0" {
			t.Fatalf("expected version 1.0.0, got: %s", res["version"])
		}
	})
}

// -----------------------------------------------------------------------------
// 2. Tools Command
// -----------------------------------------------------------------------------

func TestE2E_CLITools(t *testing.T) {
	t.Run("tools standard output", func(t *testing.T) {
		out, err := executeCommand("tools")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		for _, exp := range []string{"TOOL", "NAME", "STATUS", "CAPABILITIES", "agy", "claude", "codex", "cursor"} {
			if !strings.Contains(out, exp) {
				t.Fatalf("expected output to contain %q, got: %q", exp, out)
			}
		}
	})

	t.Run("tools list argument", func(t *testing.T) {
		out, err := executeCommand("tools", "list")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if !strings.Contains(out, "Google Antigravity CLI") {
			t.Fatalf("expected output to contain 'Google Antigravity CLI', got: %q", out)
		}
	})

	t.Run("tools --json", func(t *testing.T) {
		out, err := executeCommand("tools", "--json")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		var tools []ToolInfo
		if err := json.Unmarshal([]byte(out), &tools); err != nil {
			t.Fatalf("failed to parse JSON output: %v (raw: %q)", err, out)
		}
		if len(tools) < 4 {
			t.Fatalf("expected at least 4 tools, got %d", len(tools))
		}
		if tools[0].Name != "agy" || tools[0].Status != "active" {
			t.Fatalf("expected first tool to be active agy, got: %+v", tools[0])
		}
		foundVault := false
		for _, f := range tools[0].Features {
			if f == "vault" {
				foundVault = true
				break
			}
		}
		if !foundVault {
			t.Fatalf("expected agy features to contain 'vault', got: %v", tools[0].Features)
		}
	})

	t.Run("tools list --json", func(t *testing.T) {
		out, err := executeCommand("tools", "list", "--json")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		var tools []ToolInfo
		if err := json.Unmarshal([]byte(out), &tools); err != nil {
			t.Fatalf("failed to parse JSON output: %v (raw: %q)", err, out)
		}
		if len(tools) < 4 {
			t.Fatalf("expected at least 4 tools, got %d", len(tools))
		}
	})
}

// -----------------------------------------------------------------------------
// 3. Profile Lifecycle Workflow (save, list, switch, whoami, next, remove)
// -----------------------------------------------------------------------------

func TestE2E_CLIProfileWorkflow(t *testing.T) {
	setupTestEnv(t)

	// Step 1: Initial empty state
	t.Run("empty state list and whoami", func(t *testing.T) {
		out, err := executeCommand("list")
		if err != nil {
			t.Fatalf("list failed: %v", err)
		}
		if !strings.Contains(out, "No profiles saved") {
			t.Fatalf("expected 'No profiles saved', got: %q", out)
		}

		out, err = executeCommand("list", "--json")
		if err != nil {
			t.Fatalf("list --json failed: %v", err)
		}
		var state models.State
		if err := json.Unmarshal([]byte(out), &state); err != nil {
			t.Fatalf("failed to unmarshal state: %v", err)
		}
		if len(state.Accounts) != 0 || state.Active != nil {
			t.Fatalf("expected empty accounts and nil active, got: %+v", state)
		}

		out, err = executeCommand("whoami")
		if err != nil {
			t.Fatalf("whoami failed: %v", err)
		}
		if !strings.Contains(out, "No active profile set") {
			t.Fatalf("expected 'No active profile set', got: %q", out)
		}

		out, err = executeCommand("whoami", "--json")
		if err != nil {
			t.Fatalf("whoami --json failed: %v", err)
		}
		var whoamiMap map[string]any
		if err := json.Unmarshal([]byte(out), &whoamiMap); err != nil {
			t.Fatalf("failed to unmarshal whoami: %v", err)
		}
		if whoamiMap["active"] != nil {
			t.Fatalf("expected active=nil, got: %v", whoamiMap["active"])
		}
	})

	// Step 2: Save initial profile 'alpha'
	t.Run("save alpha profile", func(t *testing.T) {
		out, err := executeCommand("profile", "save", "alpha",
			"--email", "alpha@company.com",
			"--project", "proj-alpha",
			"--plan", "PRO")
		if err != nil {
			t.Fatalf("save alpha failed: %v (out: %s)", err, out)
		}
		if !strings.Contains(out, "Saved profile 'alpha' successfully") {
			t.Fatalf("expected success message, got: %q", out)
		}

		// Verify whoami shows alpha
		out, err = executeCommand("whoami")
		if err != nil {
			t.Fatalf("whoami failed: %v", err)
		}
		if !strings.Contains(out, "alpha") || !strings.Contains(out, "alpha@company.com") || !strings.Contains(out, "PRO") {
			t.Fatalf("expected alpha details in whoami, got: %q", out)
		}

		// Verify whoami --json
		out, err = executeCommand("whoami", "--json")
		if err != nil {
			t.Fatalf("whoami --json failed: %v", err)
		}
		var acct models.AccountMeta
		if err := json.Unmarshal([]byte(out), &acct); err != nil {
			t.Fatalf("failed to unmarshal whoami JSON: %v", err)
		}
		if acct.Name != "alpha" || acct.Email == nil || *acct.Email != "alpha@company.com" || acct.PlanType != "PRO" {
			t.Fatalf("unexpected account metadata: %+v", acct)
		}
	})

	// Step 3: Save second profile 'beta'
	t.Run("save beta profile via alias", func(t *testing.T) {
		out, err := executeCommand("save", "beta",
			"--email", "beta@company.com",
			"--plan", "STANDARD")
		if err != nil {
			t.Fatalf("save beta failed: %v (out: %s)", err, out)
		}
		if !strings.Contains(out, "Saved profile 'beta' successfully") {
			t.Fatalf("expected success message for beta, got: %q", out)
		}
	})

	// Step 4: Save third profile 'gamma' with --json
	t.Run("save gamma profile with --json", func(t *testing.T) {
		out, err := executeCommand("profile", "save", "gamma",
			"--email", "gamma@company.com",
			"--json")
		if err != nil {
			t.Fatalf("save gamma --json failed: %v (out: %s)", err, out)
		}
		var saved models.AccountMeta
		if err := json.Unmarshal([]byte(out), &saved); err != nil {
			t.Fatalf("failed to unmarshal save JSON: %v (raw: %q)", err, out)
		}
		if saved.Name != "gamma" || saved.Email == nil || *saved.Email != "gamma@company.com" {
			t.Fatalf("unexpected saved account: %+v", saved)
		}
	})

	// Step 5: List profiles
	t.Run("list profiles standard and json", func(t *testing.T) {
		out, err := executeCommand("profile", "list")
		if err != nil {
			t.Fatalf("profile list failed: %v", err)
		}
		for _, exp := range []string{"alpha", "beta", "gamma", "alpha@company.com", "beta@company.com", "gamma@company.com"} {
			if !strings.Contains(out, exp) {
				t.Fatalf("expected profile list output to contain %q, got: %q", exp, out)
			}
		}

		out, err = executeCommand("list", "--json")
		if err != nil {
			t.Fatalf("list --json failed: %v", err)
		}
		var state models.State
		if err := json.Unmarshal([]byte(out), &state); err != nil {
			t.Fatalf("failed to unmarshal list JSON: %v", err)
		}
		if len(state.Accounts) != 3 {
			t.Fatalf("expected 3 accounts, got %d", len(state.Accounts))
		}
		if state.Active == nil || *state.Active != "alpha" {
			t.Fatalf("expected active profile to be alpha, got: %v", state.Active)
		}
	})

	// Step 6: Switch profile
	t.Run("switch profile to beta and gamma", func(t *testing.T) {
		out, err := executeCommand("switch", "beta")
		if err != nil {
			t.Fatalf("switch beta failed: %v", err)
		}
		if !strings.Contains(out, "Switched active profile to 'beta'") {
			t.Fatalf("expected switch confirmation, got: %q", out)
		}

		out, err = executeCommand("profile", "whoami")
		if err != nil {
			t.Fatalf("profile whoami failed: %v", err)
		}
		if !strings.Contains(out, "beta") {
			t.Fatalf("expected whoami to show beta, got: %q", out)
		}

		// Switch to gamma with --json
		out, err = executeCommand("profile", "switch", "gamma", "--json")
		if err != nil {
			t.Fatalf("profile switch gamma --json failed: %v", err)
		}
		var switchRes map[string]any
		if err := json.Unmarshal([]byte(out), &switchRes); err != nil {
			t.Fatalf("failed to unmarshal switch JSON: %v", err)
		}
		if switchRes["status"] != "switched" || switchRes["active"] != "gamma" {
			t.Fatalf("unexpected switch result: %v", switchRes)
		}
	})

	// Step 7: Rotate next profile
	t.Run("next rotation standard and json", func(t *testing.T) {
		out, err := executeCommand("next", "--reason", "quota_exhaustion", "--cooldown", "30")
		if err != nil {
			t.Fatalf("next failed: %v (out: %s)", err, out)
		}
		if !strings.Contains(out, "Rotated profile: gamma ->") {
			t.Fatalf("expected rotation from gamma, got: %q", out)
		}

		// Next rotation with --json
		out, err = executeCommand("profile", "next", "--json")
		if err != nil {
			t.Fatalf("profile next --json failed: %v (out: %s)", err, out)
		}
		var rotRes models.RotationResult
		if err := json.Unmarshal([]byte(out), &rotRes); err != nil {
			t.Fatalf("failed to unmarshal rotation result: %v (raw: %q)", err, out)
		}
		if !rotRes.Success {
			t.Fatalf("expected rotation success, got: %+v", rotRes)
		}
	})

	// Step 8: Remove profile
	t.Run("remove profile standard and json", func(t *testing.T) {
		out, err := executeCommand("remove", "gamma")
		if err != nil {
			t.Fatalf("remove gamma failed: %v", err)
		}
		if !strings.Contains(out, "Removed profile 'gamma'") {
			t.Fatalf("expected remove confirmation, got: %q", out)
		}

		// Verify gamma is gone from list
		out, err = executeCommand("list")
		if err != nil {
			t.Fatalf("list failed: %v", err)
		}
		if strings.Contains(out, "gamma") {
			t.Fatalf("expected gamma to be removed, but still in list: %q", out)
		}

		// Remove beta with --json
		out, err = executeCommand("profile", "remove", "beta", "--json")
		if err != nil {
			t.Fatalf("profile remove beta --json failed: %v", err)
		}
		var remRes map[string]any
		if err := json.Unmarshal([]byte(out), &remRes); err != nil {
			t.Fatalf("failed to unmarshal remove JSON: %v", err)
		}
		if remRes["status"] != "removed" || remRes["account"] != "beta" {
			t.Fatalf("unexpected remove JSON: %v", remRes)
		}
	})
}

// -----------------------------------------------------------------------------
// 4. Quota Command with Mock Server
// -----------------------------------------------------------------------------

func TestE2E_CLIQuota(t *testing.T) {
	setupTestEnv(t)

	// Setup mock Cloud Code Assist server
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/v1internal:loadCodeAssist" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"cloudaicompanionProject": map[string]any{"id": "mock-e2e-project"},
				"currentTier":             map[string]any{"id": "tier-pro", "name": "Antigravity Pro"},
			})
			return
		}
		if r.URL.Path == "/v1internal:retrieveUserQuotaSummary" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"groups": []any{
					map[string]any{
						"displayName": "Gemini Models",
						"buckets": []any{
							map[string]any{
								"window":            "5h",
								"remainingFraction": 0.85,
								"resetTime":         "2026-10-16T15:00:00Z",
							},
							map[string]any{
								"window":            "weekly",
								"remainingFraction": 0.95,
								"resetTime":         "2026-10-23T00:00:00Z",
							},
						},
					},
					map[string]any{
						"displayName": "Third-Party Models",
						"buckets": []any{
							map[string]any{
								"window":            "5h",
								"remainingFraction": 0.60,
							},
							map[string]any{
								"window":            "weekly",
								"remainingFraction": 0.75,
							},
						},
					},
				},
			})
			return
		}
		http.NotFound(w, r)
	}))
	defer mockServer.Close()

	t.Setenv("PROMUX_CODE_ASSIST_URL", mockServer.URL)

	// Save profile to inspect
	out, err := executeCommand("save", "main-user", "--email", "main@example.com")
	if err != nil {
		t.Fatalf("save main-user failed: %v (out: %s)", err, out)
	}

	t.Run("standard quota inspection", func(t *testing.T) {
		out, err := executeCommand("quota")
		if err != nil {
			t.Fatalf("quota command failed: %v (out: %s)", err, out)
		}
		for _, exp := range []string{
			"Quota Summary for Profile: main-user",
			"Gemini Models:",
			"5-Hour Window:",
			"85.0%",
			"Weekly Window:",
			"95.0%",
			"Third-Party Models:",
			"60.0%",
			"75.0%",
		} {
			if !strings.Contains(out, exp) {
				t.Fatalf("expected quota output to contain %q, got:\n%s", exp, out)
			}
		}
	})

	t.Run("cached quota inspection", func(t *testing.T) {
		// Subsequent quota call should read from cache
		out, err := executeCommand("quota")
		if err != nil {
			t.Fatalf("cached quota command failed: %v", err)
		}
		if !strings.Contains(out, "Quota Summary for Profile: main-user") {
			t.Fatalf("unexpected cached quota output: %s", out)
		}
	})

	t.Run("quota --refresh forces live inspection", func(t *testing.T) {
		out, err := executeCommand("quota", "--refresh")
		if err != nil {
			t.Fatalf("quota --refresh failed: %v", err)
		}
		if !strings.Contains(out, "Quota Summary for Profile: main-user") {
			t.Fatalf("unexpected quota --refresh output: %s", out)
		}

		out, err = executeCommand("quota", "-r")
		if err != nil {
			t.Fatalf("quota -r failed: %v", err)
		}
		if !strings.Contains(out, "85.0%") {
			t.Fatalf("unexpected quota -r output: %s", out)
		}
	})

	t.Run("quota --json output", func(t *testing.T) {
		out, err := executeCommand("quota", "--json")
		if err != nil {
			t.Fatalf("quota --json failed: %v", err)
		}
		var qs models.QuotaSummary
		if err := json.Unmarshal([]byte(out), &qs); err != nil {
			t.Fatalf("failed to unmarshal quota JSON: %v (raw: %q)", err, out)
		}
		if qs.Gemini5hRemaining != 0.85 || qs.GeminiWeeklyRemaining != 0.95 {
			t.Fatalf("unexpected gemini quota: %+v", qs)
		}
		if qs.ThirdParty5hRemaining != 0.60 || qs.ThirdPartyWeeklyRemaining != 0.75 {
			t.Fatalf("unexpected third-party quota: %+v", qs)
		}
	})

	t.Run("quota target account flag", func(t *testing.T) {
		// Save a secondary account
		_, err := executeCommand("save", "secondary-user", "--email", "sec@example.com", "--project", "mock-e2e-project")
		if err != nil {
			t.Fatalf("save secondary-user failed: %v", err)
		}

		out, err := executeCommand("quota", "--account", "secondary-user", "--json")
		if err != nil {
			t.Fatalf("quota --account secondary-user failed: %v", err)
		}
		var qs models.QuotaSummary
		if err := json.Unmarshal([]byte(out), &qs); err != nil {
			t.Fatalf("failed to unmarshal quota JSON: %v", err)
		}
		if qs.Gemini5hRemaining != 0.85 {
			t.Fatalf("expected Gemini5hRemaining=0.85, got: %f", qs.Gemini5hRemaining)
		}

		out, err = executeCommand("quota", "-a", "secondary-user")
		if err != nil {
			t.Fatalf("quota -a secondary-user failed: %v", err)
		}
		if !strings.Contains(out, "Quota Summary for Profile: secondary-user") {
			t.Fatalf("expected output for secondary-user, got: %q", out)
		}
	})

	t.Run("quota --all overview table and json", func(t *testing.T) {
		// Test standard table output with --all
		out, err := executeCommand("quota", "--all")
		if err != nil {
			t.Fatalf("quota --all failed: %v", err)
		}
		if !strings.Contains(out, "PROFILE") || !strings.Contains(out, "GEMINI (5H)") {
			t.Fatalf("unexpected quota --all header: %s", out)
		}
		if !strings.Contains(out, "main-user") || !strings.Contains(out, "secondary-user") {
			t.Fatalf("expected both accounts in quota --all output: %s", out)
		}

		// Test positional 'all' argument
		out, err = executeCommand("quota", "all")
		if err != nil {
			t.Fatalf("quota all failed: %v", err)
		}
		if !strings.Contains(out, "main-user") {
			t.Fatalf("expected main-user in 'quota all' output: %s", out)
		}

		// Test JSON array output with --all --json
		out, err = executeCommand("quota", "--all", "--json")
		if err != nil {
			t.Fatalf("quota --all --json failed: %v", err)
		}
		var records []AccountQuotaRecord
		if err := json.Unmarshal([]byte(out), &records); err != nil {
			t.Fatalf("failed to unmarshal quota --all JSON: %v (raw: %q)", err, out)
		}
		if len(records) < 2 {
			t.Fatalf("expected at least 2 account records, got %d", len(records))
		}
		foundMain := false
		foundSec := false
		for _, r := range records {
			if r.Account == "main-user" {
				foundMain = true
				if !r.Active {
					t.Fatalf("expected main-user to be active")
				}
				if r.Gemini == nil || r.Gemini.Remaining5h != 0.85 {
					t.Fatalf("unexpected gemini remaining: %+v", r.Gemini)
				}
			}
			if r.Account == "secondary-user" {
				foundSec = true
				if r.Active {
					t.Fatalf("expected secondary-user to not be active")
				}
			}
		}
		if !foundMain || !foundSec {
			t.Fatalf("missing accounts in records: %+v", records)
		}
	})
}

// -----------------------------------------------------------------------------
// 5. Refresh Command (OAuth Token Renewal)
// -----------------------------------------------------------------------------

func TestE2E_CLIRefresh(t *testing.T) {
	_, _, liveToken := setupTestEnv(t)

	mockOAuthServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
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
			"access_token": "refreshed-e2e-access-token",
			"expires_in":   3600,
			"token_type":   "Bearer",
		})
	}))
	defer mockOAuthServer.Close()

	t.Setenv("PROMUX_OAUTH_TOKEN_URL", mockOAuthServer.URL)

	// Save profile with future expiry
	out, err := executeCommand("save", "token-user", "--email", "token@example.com")
	if err != nil {
		t.Fatalf("save token-user failed: %v (out: %s)", err, out)
	}

	t.Run("refresh unexpired token without force", func(t *testing.T) {
		out, err := executeCommand("refresh")
		if err != nil {
			t.Fatalf("refresh failed: %v", err)
		}
		if !strings.Contains(out, "are still valid (use --force to renew now)") {
			t.Fatalf("expected still valid notice, got: %q", out)
		}
	})

	t.Run("refresh active account with --force", func(t *testing.T) {
		out, err := executeCommand("refresh", "--force")
		if err != nil {
			t.Fatalf("refresh --force failed: %v", err)
		}
		if !strings.Contains(out, "Successfully refreshed OAuth credentials for 'token-user'") {
			t.Fatalf("expected refresh success message, got: %q", out)
		}

		// Verify live token on disk was updated
		diskData, err := auth.ReadTokenData(liveToken)
		if err != nil {
			t.Fatalf("failed to read live token: %v", err)
		}
		if auth.ExtractAccessToken(diskData) != "refreshed-e2e-access-token" {
			t.Fatalf("expected disk access_token to be 'refreshed-e2e-access-token', got: %s",
				auth.ExtractAccessToken(diskData))
		}
	})

	t.Run("refresh named account with -f flag", func(t *testing.T) {
		out, err := executeCommand("refresh", "token-user", "-f")
		if err != nil {
			t.Fatalf("refresh token-user -f failed: %v", err)
		}
		if !strings.Contains(out, "Successfully refreshed OAuth credentials for 'token-user'") {
			t.Fatalf("expected refresh success message, got: %q", out)
		}
	})

	t.Run("refresh expired token automatically", func(t *testing.T) {
		// Overwrite live token with an expired expiry
		expiredToken := map[string]any{
			"token": map[string]any{
				"access_token":  "expired-token",
				"refresh_token": "mock-refresh-token",
				"expiry":        time.Now().UTC().Add(-1 * time.Hour).Format(time.RFC3339),
			},
		}
		data, _ := json.Marshal(expiredToken)
		_ = os.WriteFile(liveToken, data, 0600)

		// Sync to account vault as well
		store := GetStorage()
		vaultToken := filepath.Join(store.AccountsDir, "token-user", "antigravity-oauth-token")
		_ = os.WriteFile(vaultToken, data, 0600)

		// Refresh without --force should renew because it is expired
		out, err := executeCommand("refresh")
		if err != nil {
			t.Fatalf("refresh on expired token failed: %v", err)
		}
		if !strings.Contains(out, "Successfully refreshed OAuth credentials for 'token-user'") {
			t.Fatalf("expected auto-refresh success, got: %q", out)
		}
	})
}

// -----------------------------------------------------------------------------
// 6. Comprehensive Error Handling
// -----------------------------------------------------------------------------

func TestE2E_CLIErrorHandling(t *testing.T) {
	_, _, liveToken := setupTestEnv(t)

	t.Run("switch to non-existent account", func(t *testing.T) {
		out, err := executeCommand("switch", "non-existent-account")
		if err == nil {
			t.Fatalf("expected error switching to non-existent account, got success (out: %s)", out)
		}
		if !strings.Contains(err.Error(), "not found") {
			t.Fatalf("expected error to contain 'not found', got: %v", err)
		}

		// Also check subcommand profile switch
		_, err = executeCommand("profile", "switch", "non-existent-account")
		if err == nil || !strings.Contains(err.Error(), "not found") {
			t.Fatalf("expected error containing 'not found', got: %v", err)
		}
	})

	t.Run("remove non-existent account", func(t *testing.T) {
		out, err := executeCommand("remove", "non-existent-account")
		if err == nil {
			t.Fatalf("expected error removing non-existent account, got success (out: %s)", out)
		}
		if !strings.Contains(err.Error(), "not found") {
			t.Fatalf("expected error to contain 'not found', got: %v", err)
		}

		_, err = executeCommand("profile", "remove", "non-existent-account")
		if err == nil || !strings.Contains(err.Error(), "not found") {
			t.Fatalf("expected error containing 'not found', got: %v", err)
		}
	})

	t.Run("missing required arguments", func(t *testing.T) {
		// save requires 1 arg
		_, err := executeCommand("save")
		if err == nil {
			t.Fatalf("expected error for 'save' without args")
		}

		// switch requires 1 arg
		_, err = executeCommand("switch")
		if err == nil {
			t.Fatalf("expected error for 'switch' without args")
		}

		// remove requires 1 arg
		_, err = executeCommand("remove")
		if err == nil {
			t.Fatalf("expected error for 'remove' without args")
		}

		// run requires at least 1 arg
		_, err = executeCommand("run")
		if err == nil {
			t.Fatalf("expected error for 'run' without args")
		}
	})

	t.Run("save fails when live token is missing", func(t *testing.T) {
		// Remove live token file
		_ = os.Remove(liveToken)

		_, err := executeCommand("save", "should-fail")
		if err == nil {
			t.Fatalf("expected error when live token is missing, got nil")
		}
	})

	t.Run("quota and refresh fail when no active profile set", func(t *testing.T) {
		// In a fresh empty temp env
		tmpEmpty := t.TempDir()
		t.Setenv("PROMUX_HOME", filepath.Join(tmpEmpty, "promux"))
		t.Setenv("PROMUX_GEMINI_HOME", filepath.Join(tmpEmpty, "gemini"))

		_, err := executeCommand("quota")
		if err == nil {
			t.Fatalf("expected error running quota with no active profile")
		}
		if !strings.Contains(err.Error(), "no active profile set") {
			t.Fatalf("expected 'no active profile set' error, got: %v", err)
		}

		_, err = executeCommand("refresh")
		if err == nil {
			t.Fatalf("expected error running refresh with no active profile")
		}
		if !strings.Contains(err.Error(), "no active profile set") {
			t.Fatalf("expected 'no active profile set' error, got: %v", err)
		}

		// Target non-existent account for quota
		_, err = executeCommand("quota", "--account", "ghost")
		if err == nil {
			t.Fatalf("expected error for quota on non-existent account")
		}

		// Target non-existent account for refresh
		_, err = executeCommand("refresh", "ghost")
		if err == nil {
			t.Fatalf("expected error for refresh on non-existent account")
		}
	})

	t.Run("invalid flags rejected", func(t *testing.T) {
		_, err := executeCommand("version", "--invalid-nonexistent-flag")
		if err == nil {
			t.Fatalf("expected error for unknown flag on subcommand")
		}
		if !strings.Contains(err.Error(), "unknown flag") {
			t.Fatalf("expected 'unknown flag' error, got: %v", err)
		}

		_, err = executeCommand("--unknown-global-flag")
		if err == nil {
			t.Fatalf("expected error for unknown global flag")
		}
		if !strings.Contains(err.Error(), "unknown flag") {
			t.Fatalf("expected 'unknown flag' error, got: %v", err)
		}
	})

	t.Run("next rotation fails gracefully when no standby profiles available", func(t *testing.T) {
		// In isolated directory with only 1 active account
		tmpDir := t.TempDir()
		pDir := filepath.Join(tmpDir, "p")
		gDir := filepath.Join(tmpDir, "g")
		_ = os.MkdirAll(pDir, 0755)
		_ = os.MkdirAll(gDir, 0755)
		t.Setenv("PROMUX_HOME", pDir)
		t.Setenv("PROMUX_GEMINI_HOME", gDir)

		lt := filepath.Join(gDir, "antigravity-oauth-token")
		_ = os.WriteFile(lt, []byte(`{"token":{"access_token":"tok","refresh_token":"ref"}}`), 0600)

		_, _ = executeCommand("save", "lonely-user")

		out, err := executeCommand("next")
		if err != nil {
			t.Fatalf("unexpected command error: %v", err)
		}
		if !strings.Contains(out, "Rotation failed") {
			t.Fatalf("expected 'Rotation failed', got: %q", out)
		}

		out, err = executeCommand("next", "--json")
		if err != nil {
			t.Fatalf("unexpected JSON command error: %v", err)
		}
		var res models.RotationResult
		if err := json.Unmarshal([]byte(out), &res); err != nil {
			t.Fatalf("failed to unmarshal rotation result: %v", err)
		}
		if res.Success {
			t.Fatalf("expected rotation success to be false, got: %+v", res)
		}
	})
}
