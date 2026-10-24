package cmd

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"

	"github.com/alirezaopmc/promux/pkg/models"
)

var testCLIMu sync.Mutex

func resetCmdFlags(c *cobra.Command) {
	c.Flags().VisitAll(func(f *pflag.Flag) {
		_ = f.Value.Set(f.DefValue)
		f.Changed = false
	})
	for _, child := range c.Commands() {
		resetCmdFlags(child)
	}
}

func executeCommand(args ...string) (string, error) {
	testCLIMu.Lock()
	defer testCLIMu.Unlock()

	resetCmdFlags(RootCmd)
	ToolName = "agy"
	JSONOut = false
	DebugMode = false

	buf := new(bytes.Buffer)
	RootCmd.SetOut(buf)
	RootCmd.SetErr(buf)
	RootCmd.SetArgs(args)

	err := RootCmd.Execute()
	return buf.String(), err
}

func TestCLIVersion(t *testing.T) {
	out, err := executeCommand("version")
	if err != nil {
		t.Fatalf("version command failed: %v", err)
	}
	expected := "promux version " + Version + "\n"
	if out != expected {
		t.Fatalf("unexpected version output: %s, expected %s", out, expected)
	}
}

func TestCLIProfileWorkflow(t *testing.T) {
	tmpDir := t.TempDir()
	geminiDir := filepath.Join(tmpDir, "gemini")
	_ = os.MkdirAll(geminiDir, 0755)

	t.Setenv("PROMUX_HOME", tmpDir)
	t.Setenv("PROMUX_GEMINI_HOME", geminiDir)

	liveToken := filepath.Join(geminiDir, "antigravity-oauth-token")
	_ = os.WriteFile(liveToken, []byte(`{"access_token": "cli-test-token"}`), 0600)

	// Save profile
	out, err := executeCommand("save", "my-profile", "--email", "cli@example.com")
	if err != nil {
		t.Fatalf("save failed: %v (output: %s)", err, out)
	}

	// Whoami
	out, err = executeCommand("whoami")
	if err != nil {
		t.Fatalf("whoami failed: %v", err)
	}

	// List
	out, err = executeCommand("list")
	if err != nil {
		t.Fatalf("list failed: %v", err)
	}

	// Tools
	out, err = executeCommand("tools")
	if err != nil {
		t.Fatalf("tools failed: %v", err)
	}
}

func TestFormatOverviewCell(t *testing.T) {
	now := time.Date(2026, 10, 24, 12, 0, 0, 0, time.UTC)

	// Missing reset timestamp formats as "(-)"
	res := formatOverviewCell(1.0, nil, now)
	if !strings.Contains(res, "100.0%") || !strings.Contains(res, "(-)") {
		t.Fatalf("expected '100.0%% (-)', got %q", res)
	}

	// Active countdown formats with duration
	future := now.Add(2 * time.Hour).Format(time.RFC3339)
	resWithCountdown := formatOverviewCell(0.85, &future, now)
	if !strings.Contains(resWithCountdown, "85.0%") || !strings.Contains(resWithCountdown, "2h") {
		t.Fatalf("expected '85.0%% (2h)', got %q", resWithCountdown)
	}
}

func TestPrintQuotaGeminiWeeklyDashWhenNil(t *testing.T) {
	buf := new(bytes.Buffer)
	qs := models.NewQuotaSummary()
	qs.Gemini5hRemaining = 0.90
	qs.GeminiWeeklyReset = nil // no weekly reset for gemini

	printQuota(buf, "test-account", qs)
	out := buf.String()

	if !strings.Contains(out, "Gemini Models:") {
		t.Fatalf("expected 'Gemini Models:' in output: %s", out)
	}
	if !strings.Contains(out, "• Weekly Window: -") {
		t.Fatalf("expected '• Weekly Window: -' in output: %s", out)
	}
}
