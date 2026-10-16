package cmd

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func executeCommand(args ...string) (string, error) {
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
	if out != "promux version 1.0.0\n" {
		t.Fatalf("unexpected version output: %s", out)
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
