package cmd

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/alirezaopmc/promux/pkg/auth"
	"github.com/alirezaopmc/promux/pkg/config"
	"github.com/alirezaopmc/promux/pkg/models"
)

func setupHubCLITestEnv(t *testing.T) (promuxDir, geminiDir, hubGeminiDir, cliTokenPath, hubTokenPath, daemonDir string) {
	t.Helper()
	root := t.TempDir()
	promuxDir = filepath.Join(root, "promux")
	geminiDir = filepath.Join(root, "gemini-cli")
	hubGeminiDir = filepath.Join(root, "gemini-hub")

	if err := os.MkdirAll(promuxDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(geminiDir, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(hubGeminiDir, 0755); err != nil {
		t.Fatal(err)
	}

	t.Setenv("PROMUX_HOME", promuxDir)
	t.Setenv("PROMUX_GEMINI_HOME", geminiDir)
	t.Setenv("PROMUX_HUB_GEMINI_DIR", hubGeminiDir)
	t.Setenv("PROMUX_HUB_TOKEN_FILE", "")
	t.Setenv("PROMUX_HUB_DAEMON_DIR", "")

	ToolName = "agy"
	JSONOut = false
	DebugMode = false

	cliTokenPath = config.LiveTokenFile(geminiDir)
	hubTokenPath = config.HubLiveTokenFile("")
	daemonDir = config.HubDaemonDir("")
	return promuxDir, geminiDir, hubGeminiDir, cliTokenPath, hubTokenPath, daemonDir
}

func makeHubTestTokenJSON(t *testing.T, accessToken, refreshToken, email string, expiry time.Time, writerID string) []byte {
	t.Helper()
	payload := map[string]any{
		"auth_method": "consumer",
		"token": map[string]any{
			"access_token":  accessToken,
			"refresh_token": refreshToken,
			"expiry":        expiry.UTC().Format(time.RFC3339),
		},
	}
	if email != "" {
		payload["id_token_email"] = email
	}
	if writerID != "" {
		payload["writer_id"] = writerID
	}
	b, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		t.Fatalf("failed to marshal test token: %v", err)
	}
	return append(b, '\n')
}

func executeCommandStreams(args ...string) (stdout, stderr string, err error) {
	testCLIMu.Lock()
	defer func() {
		resetCmdFlags(RootCmd)
		ToolName = "agy"
		JSONOut = false
		DebugMode = false
		testCLIMu.Unlock()
	}()

	resetCmdFlags(RootCmd)
	ToolName = "agy"
	JSONOut = false
	DebugMode = false

	outBuf := new(bytes.Buffer)
	errBuf := new(bytes.Buffer)
	RootCmd.SetOut(outBuf)
	RootCmd.SetErr(errBuf)
	RootCmd.SetArgs(args)

	err = RootCmd.Execute()
	return outBuf.String(), errBuf.String(), err
}

func TestCLIHubSaveSwitchNextStatusJSON(t *testing.T) {
	_, _, _, cliTokenPath, hubTokenPath, _ := setupHubCLITestEnv(t)
	future := time.Now().UTC().Add(2 * time.Hour)

	// Write Hub live token and save as gui-a
	if err := os.WriteFile(hubTokenPath, makeHubTestTokenJSON(t, "gui-a-acc", "gui-a-ref", "guia@example.com", future, "writer-initial"), 0600); err != nil {
		t.Fatal(err)
	}

	out, err := executeCommand("hub", "save", "gui-a", "--json")
	if err != nil {
		t.Fatalf("hub save gui-a --json failed: %v (out: %s)", err, out)
	}
	var savedMeta models.AccountMeta
	if err := json.Unmarshal([]byte(out), &savedMeta); err != nil {
		t.Fatalf("unmarshal hub save failed: %v (raw: %q)", err, out)
	}
	if savedMeta.Name != "gui-a" || savedMeta.Email == nil || *savedMeta.Email != "guia@example.com" {
		t.Fatalf("unexpected saved metadata: %+v", savedMeta)
	}

	// Save a second CLI profile cli-b, then switch CLI back to gui-a so Active != cli-b
	if err := os.WriteFile(cliTokenPath, makeHubTestTokenJSON(t, "cli-b-acc", "cli-b-ref", "clib@example.com", future, ""), 0600); err != nil {
		t.Fatal(err)
	}
	if out, err := executeCommand("save", "cli-b", "--email", "clib@example.com"); err != nil {
		t.Fatalf("save cli-b failed: %v (out: %s)", err, out)
	}
	if out, err := executeCommand("switch", "gui-a"); err != nil {
		t.Fatalf("switch gui-a failed: %v (out: %s)", err, out)
	}

	// Switch Hub only to cli-b
	out, err = executeCommand("hub", "switch", "cli-b", "--no-restart", "--json")
	if err != nil {
		t.Fatalf("hub switch cli-b --no-restart --json failed: %v (out: %s)", err, out)
	}
	var swRes map[string]any
	if err := json.Unmarshal([]byte(out), &swRes); err != nil {
		t.Fatalf("unmarshal hub switch failed: %v (raw: %q)", err, out)
	}
	if swRes["profile"] != "cli-b" {
		t.Fatalf("expected profile=cli-b, got %v", swRes)
	}
	if swRes["writer_id"] != "writer-initial" {
		t.Fatalf("expected writer_id=writer-initial, got %v", swRes)
	}

	store := GetStorage()
	state, err := store.LoadState()
	if err != nil {
		t.Fatal(err)
	}
	if state.HubActive == nil || *state.HubActive != "cli-b" {
		t.Fatalf("expected HubActive=cli-b, got %v", state.HubActive)
	}
	if state.Active == nil || *state.Active != "gui-a" {
		t.Fatalf("expected CLI Active unchanged (gui-a), got %v", state.Active)
	}

	hubData, err := auth.ReadTokenData(hubTokenPath)
	if err != nil {
		t.Fatal(err)
	}
	if got := auth.ExtractAccessToken(hubData); got != "cli-b-acc" {
		t.Fatalf("expected hub live access_token cli-b-acc, got %q", got)
	}
	if got, _ := hubData["writer_id"].(string); got != "writer-initial" {
		t.Fatalf("expected preserved writer_id writer-initial, got %q", got)
	}

	// Rotate Hub via hub next back to gui-a
	out, err = executeCommand("hub", "next", "--no-restart", "--json")
	if err != nil {
		t.Fatalf("hub next --no-restart --json failed: %v (out: %s)", err, out)
	}
	var rotRes map[string]any
	if err := json.Unmarshal([]byte(out), &rotRes); err != nil {
		t.Fatalf("unmarshal hub next failed: %v (raw: %q)", err, out)
	}
	if rotRes["success"] != true || rotRes["from_account"] != "cli-b" || rotRes["to_account"] != "gui-a" {
		t.Fatalf("unexpected hub next result: %v", rotRes)
	}

	state, err = store.LoadState()
	if err != nil {
		t.Fatal(err)
	}
	if state.HubActive == nil || *state.HubActive != "gui-a" {
		t.Fatalf("expected HubActive rotated back to gui-a, got %v", state.HubActive)
	}
	if state.Active == nil || *state.Active != "gui-a" {
		t.Fatalf("expected CLI Active still gui-a, got %v", state.Active)
	}

	// Check hub whoami --json and hub status --json
	for _, sub := range []string{"whoami", "status"} {
		out, err = executeCommand("hub", sub, "--json")
		if err != nil {
			t.Fatalf("hub %s --json failed: %v (out: %s)", sub, err, out)
		}
		var stRes map[string]any
		if err := json.Unmarshal([]byte(out), &stRes); err != nil {
			t.Fatalf("unmarshal hub %s failed: %v (raw: %q)", sub, err, out)
		}
		if stRes["hub_active"] != "gui-a" {
			t.Fatalf("hub %s: expected hub_active=gui-a, got %v", sub, stRes["hub_active"])
		}
		syncObj, ok := stRes["sync"].(map[string]any)
		if !ok || syncObj["state"] != string(auth.HubSyncInSync) {
			t.Fatalf("hub %s: expected sync.state==%q, got %v", sub, auth.HubSyncInSync, stRes["sync"])
		}
	}
}

func TestCLISwitchAndNextWithHubFlag(t *testing.T) {
	_, _, _, cliTokenPath, hubTokenPath, _ := setupHubCLITestEnv(t)
	future := time.Now().UTC().Add(2 * time.Hour)

	if err := os.WriteFile(cliTokenPath, makeHubTestTokenJSON(t, "gui-a-acc", "gui-a-ref", "guia@example.com", future, ""), 0600); err != nil {
		t.Fatal(err)
	}
	if out, err := executeCommand("save", "gui-a", "--email", "guia@example.com"); err != nil {
		t.Fatalf("save gui-a failed: %v (out: %s)", err, out)
	}

	if err := os.WriteFile(cliTokenPath, makeHubTestTokenJSON(t, "cli-b-acc", "cli-b-ref", "clib@example.com", future, ""), 0600); err != nil {
		t.Fatal(err)
	}
	if out, err := executeCommand("save", "cli-b", "--email", "clib@example.com"); err != nil {
		t.Fatalf("save cli-b failed: %v (out: %s)", err, out)
	}

	// Switch both CLI and Hub to cli-b
	out, err := executeCommand("switch", "cli-b", "--hub", "--no-restart", "--json")
	if err != nil {
		t.Fatalf("switch cli-b --hub --no-restart --json failed: %v (out: %s)", err, out)
	}
	var swOut map[string]any
	if err := json.Unmarshal([]byte(out), &swOut); err != nil {
		t.Fatalf("unmarshal switch --hub failed: %v (raw: %q)", err, out)
	}
	if swOut["active"] != "cli-b" || swOut["hub_active"] != "cli-b" {
		t.Fatalf("expected active=cli-b and hub_active=cli-b in JSON, got %v", swOut)
	}

	store := GetStorage()
	state, err := store.LoadState()
	if err != nil {
		t.Fatal(err)
	}
	if state.Active == nil || *state.Active != "cli-b" || state.HubActive == nil || *state.HubActive != "cli-b" {
		t.Fatalf("expected both Active and HubActive to be cli-b, got active=%v hub=%v", state.Active, state.HubActive)
	}

	// Rotate both CLI and Hub together via next --hub
	out, err = executeCommand("next", "--hub", "--no-restart", "--json")
	if err != nil {
		t.Fatalf("next --hub --no-restart --json failed: %v (out: %s)", err, out)
	}
	var nextOut map[string]any
	if err := json.Unmarshal([]byte(out), &nextOut); err != nil {
		t.Fatalf("unmarshal next --hub failed: %v (raw: %q)", err, out)
	}
	if nextOut["success"] != true || nextOut["from_account"] != "cli-b" || nextOut["to_account"] != "gui-a" {
		t.Fatalf("unexpected next --hub JSON output: %v", nextOut)
	}

	state, err = store.LoadState()
	if err != nil {
		t.Fatal(err)
	}
	if state.Active == nil || *state.Active != "gui-a" || state.HubActive == nil || *state.HubActive != "gui-a" {
		t.Fatalf("expected both Active and HubActive rotated to gui-a, got active=%v hub=%v", state.Active, state.HubActive)
	}

	hubData, err := auth.ReadTokenData(hubTokenPath)
	if err != nil {
		t.Fatal(err)
	}
	if got := auth.ExtractAccessToken(hubData); got != "gui-a-acc" {
		t.Fatalf("expected hub live access_token gui-a-acc, got %q", got)
	}
	cliData, err := auth.ReadTokenData(cliTokenPath)
	if err != nil {
		t.Fatal(err)
	}
	if got := auth.ExtractAccessToken(cliData); got != "gui-a-acc" {
		t.Fatalf("expected cli live access_token gui-a-acc, got %q", got)
	}
}

func TestCLISwitchHubFlagFailedRefreshLeavesCLIAndHubUntouched(t *testing.T) {
	mockOAuth := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]any{"error": "invalid_grant"})
	}))
	defer mockOAuth.Close()
	t.Setenv("PROMUX_OAUTH_TOKEN_URL", mockOAuth.URL)

	_, _, _, cliTokenPath, hubTokenPath, _ := setupHubCLITestEnv(t)
	future := time.Now().UTC().Add(2 * time.Hour)
	expired := time.Now().UTC().Add(-1 * time.Hour)

	store := GetStorage()
	emailA := "alpha@example.com"
	if _, err := store.SaveProfileFromTokenBytes("alpha", makeHubTestTokenJSON(t, "alpha-acc", "alpha-ref", emailA, future, ""), &emailA, nil, "STANDARD", true, true); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cliTokenPath, makeHubTestTokenJSON(t, "alpha-acc", "alpha-ref", emailA, future, ""), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(hubTokenPath, makeHubTestTokenJSON(t, "alpha-acc", "alpha-ref", emailA, future, "w-alpha"), 0600); err != nil {
		t.Fatal(err)
	}

	emailB := "broken@example.com"
	if _, err := store.SaveProfileFromTokenBytes("broken", makeHubTestTokenJSON(t, "broken-old", "broken-ref", emailB, expired, ""), &emailB, nil, "STANDARD", false, false); err != nil {
		t.Fatal(err)
	}

	_, err := executeCommand("switch", "broken", "--hub", "--no-restart")
	if !errors.Is(err, auth.ErrTokenRevoked) {
		t.Fatalf("expected ErrTokenRevoked, got %v", err)
	}

	state, err := store.LoadState()
	if err != nil {
		t.Fatal(err)
	}
	if state.Active == nil || *state.Active != "alpha" {
		t.Fatalf("expected CLI Active to remain alpha, got %v", state.Active)
	}
	if state.HubActive == nil || *state.HubActive != "alpha" {
		t.Fatalf("expected HubActive to remain alpha, got %v", state.HubActive)
	}

	cliData, _ := auth.ReadTokenData(cliTokenPath)
	if got := auth.ExtractAccessToken(cliData); got != "alpha-acc" {
		t.Fatalf("CLI live token mutated on failed switch --hub: %q", got)
	}
	hubData, _ := auth.ReadTokenData(hubTokenPath)
	if got := auth.ExtractAccessToken(hubData); got != "alpha-acc" {
		t.Fatalf("Hub live token mutated on failed switch --hub: %q", got)
	}
}

func TestCLIListShowsHubIndicator(t *testing.T) {
	_, _, _, _, _, _ = setupHubCLITestEnv(t)
	future := time.Now().UTC().Add(2 * time.Hour)

	store := GetStorage()
	emailA := "alpha@example.com"
	emailB := "beta@example.com"
	if _, err := store.SaveProfileFromTokenBytes("alpha", makeHubTestTokenJSON(t, "a-acc", "a-ref", emailA, future, ""), &emailA, nil, "PRO", true, false); err != nil {
		t.Fatal(err)
	}
	if _, err := store.SaveProfileFromTokenBytes("beta", makeHubTestTokenJSON(t, "b-acc", "b-ref", emailB, future, ""), &emailB, nil, "STANDARD", false, true); err != nil {
		t.Fatal(err)
	}

	out, err := executeCommand("list")
	if err != nil {
		t.Fatalf("list failed: %v (out: %s)", err, out)
	}
	if !strings.Contains(out, "beta [hub]") {
		t.Fatalf("expected 'beta [hub]' in list output, got:\n%s", out)
	}
	if strings.Contains(out, "alpha [hub]") {
		t.Fatalf("did not expect 'alpha [hub]' in list output, got:\n%s", out)
	}
}

func TestCLIHubRejectsUnsupportedToolFlag(t *testing.T) {
	setupHubCLITestEnv(t)

	for _, args := range [][]string{
		{"-t", "claude", "hub", "status"},
		{"-t", "claude", "switch", "alpha", "--hub", "--no-restart"},
		{"-t", "codex", "next", "--hub", "--no-restart"},
	} {
		_, err := executeCommand(args...)
		if err == nil {
			t.Fatalf("expected error for args %v, got nil", args)
		}
		if !strings.Contains(err.Error(), "only supported for tool 'agy'") {
			t.Fatalf("expected error containing \"only supported for tool 'agy'\" for args %v, got: %v", args, err)
		}
	}
}

func TestCLIHubJSONWarningsGoToStderr(t *testing.T) {
	_, _, _, _, hubTokenPath, _ := setupHubCLITestEnv(t)
	future := time.Now().UTC().Add(2 * time.Hour)

	store := GetStorage()
	email := "tracked@example.com"
	if _, err := store.SaveProfileFromTokenBytes("tracked", makeHubTestTokenJSON(t, "tracked-acc", "tracked-ref", email, future, ""), &email, nil, "PRO", false, true); err != nil {
		t.Fatal(err)
	}
	// Put a different account in live hub token to induce out-of-sync
	if err := os.WriteFile(hubTokenPath, makeHubTestTokenJSON(t, "other-acc", "other-ref", "other@example.com", future, "w-other"), 0600); err != nil {
		t.Fatal(err)
	}

	stdout, stderr, err := executeCommandStreams("hub", "status", "--json")
	if err != nil {
		t.Fatalf("hub status --json failed: %v (stdout: %s, stderr: %s)", err, stdout, stderr)
	}
	var stRes map[string]any
	if err := json.Unmarshal([]byte(stdout), &stRes); err != nil {
		t.Fatalf("stdout must be pure valid JSON when out-of-sync, got err=%v raw=%q", err, stdout)
	}
	syncObj, _ := stRes["sync"].(map[string]any)
	if syncObj["state"] != string(auth.HubSyncOutOfSync) {
		t.Fatalf("expected sync.state=out-of-sync, got %v", syncObj)
	}
	if !strings.Contains(stderr, "out-of-sync") {
		t.Fatalf("expected out-of-sync warning on stderr, got %q", stderr)
	}
}

func TestCLIHubRestartPIDsHiddenCommand(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("unix signal lifecycle test")
	}

	// Verify _restart-pids is registered and marked Hidden
	hubCmd, _, err := RootCmd.Find([]string{"hub"})
	if err != nil {
		t.Fatalf("failed to find hub command: %v", err)
	}
	restartPIDsCmd, _, err := RootCmd.Find([]string{"hub", "_restart-pids"})
	if err != nil {
		t.Fatalf("failed to find _restart-pids command under hub: %v", err)
	}
	if !restartPIDsCmd.Hidden {
		t.Fatalf("expected _restart-pids command to be hidden (Hidden == true)")
	}
	_ = hubCmd

	setupHubCLITestEnv(t)

	// Invalid PID argument returns an error cleanly
	if _, err := executeCommand("hub", "_restart-pids", "--delay", "1ms", "not-a-pid"); err == nil {
		t.Fatal("expected error for invalid PID argument")
	}

	// Start dummy subprocess that handles SIGTERM gracefully
	child, waitReady := startCLIHelperProcess(t)
	defer func() {
		if child.Process != nil {
			_ = child.Process.Kill()
		}
		_ = child.Wait()
	}()
	waitReady()

	out, err := executeCommand("hub", "_restart-pids", "--delay", "50ms", strconv.Itoa(child.Process.Pid))
	if err != nil {
		t.Fatalf("hub _restart-pids failed: %v (out: %s)", err, out)
	}
	if waitErr := child.Wait(); waitErr != nil {
		t.Fatalf("expected dummy subprocess to exit 0 on SIGTERM, got: %v", waitErr)
	}
}

func TestCLIHubHumanOutputsAndRestartNeedsReconnect(t *testing.T) {
	_, _, _, cliTokenPath, hubTokenPath, daemonDir := setupHubCLITestEnv(t)
	future := time.Now().UTC().Add(2 * time.Hour)

	if err := os.WriteFile(hubTokenPath, makeHubTestTokenJSON(t, "gui-a-acc", "gui-a-ref", "guia@example.com", future, "w-human"), 0600); err != nil {
		t.Fatal(err)
	}
	if out, err := executeCommand("hub", "save", "gui-a", "--plan", "PRO"); err != nil {
		t.Fatalf("hub save failed: %v (out: %s)", err, out)
	}
	if err := os.WriteFile(cliTokenPath, makeHubTestTokenJSON(t, "cli-b-acc", "cli-b-ref", "clib@example.com", future, ""), 0600); err != nil {
		t.Fatal(err)
	}
	if out, err := executeCommand("save", "cli-b", "--email", "clib@example.com"); err != nil {
		t.Fatalf("save cli-b failed: %v (out: %s)", err, out)
	}

	// Human whoami / status / restart with no daemons
	out, err := executeCommand("hub", "whoami")
	if err != nil {
		t.Fatalf("hub whoami failed: %v", err)
	}
	if !strings.Contains(out, "gui-a") || !strings.Contains(out, "in-sync") {
		t.Fatalf("unexpected hub whoami output:\n%s", out)
	}

	out, err = executeCommand("hub", "status")
	if err != nil {
		t.Fatalf("hub status failed: %v", err)
	}
	if !strings.Contains(out, "Hub Token Path:") || !strings.Contains(out, hubTokenPath) {
		t.Fatalf("unexpected hub status output:\n%s", out)
	}

	out, err = executeCommand("hub", "restart")
	if err != nil {
		t.Fatalf("hub restart (no daemons) failed: %v", err)
	}
	if !strings.Contains(out, "No running Hub language_server daemons found") {
		t.Fatalf("unexpected hub restart empty output:\n%s", out)
	}

	// Human switch --hub --no-restart and next --hub --no-restart
	out, err = executeCommand("switch", "cli-b", "--hub", "--no-restart")
	if err != nil {
		t.Fatalf("switch --hub failed: %v (out: %s)", err, out)
	}
	if !strings.Contains(out, "Switched active profile to 'cli-b'") || !strings.Contains(out, "Switched Hub profile to 'cli-b'") {
		t.Fatalf("unexpected human switch --hub output:\n%s", out)
	}

	out, err = executeCommand("next", "--hub", "--no-restart")
	if err != nil {
		t.Fatalf("next --hub failed: %v (out: %s)", err, out)
	}
	if !strings.Contains(out, "Rotated profile: cli-b -> gui-a") || !strings.Contains(out, "Synced Hub profile to 'gui-a'") {
		t.Fatalf("unexpected human next --hub output:\n%s", out)
	}

	if runtime.GOOS == "windows" {
		return
	}

	// Verify NeedsReconnect goes to stderr in --json mode, and stdout in human mode
	if err := os.MkdirAll(daemonDir, 0755); err != nil {
		t.Fatal(err)
	}

	// 1) JSON mode
	d1, wait1 := startCLIHelperProcess(t, "language_server", "--standalone", "--subclient_type", "hub", "--workspace_id", "agy-hub-ssh")
	defer func() {
		if d1.Process != nil {
			_ = d1.Process.Kill()
		}
		_ = d1.Wait()
	}()
	wait1()

	desc1 := filepath.Join(daemonDir, "ls_1.json")
	if err := os.WriteFile(desc1, []byte(fmt.Sprintf(`{"pid":%d,"httpsPort":41001,"httpPort":41002,"lsVersion":"2.35.1"}`, d1.Process.Pid)), 0600); err != nil {
		t.Fatal(err)
	}
	// Also verify hub status human daemon table displays the live daemon
	statusOut, err := executeCommand("hub", "status")
	if err != nil {
		t.Fatalf("hub status with live daemon failed: %v", err)
	}
	if !strings.Contains(statusOut, strconv.Itoa(d1.Process.Pid)) || !strings.Contains(statusOut, "41001") {
		t.Fatalf("expected daemon PID and port in hub status output:\n%s", statusOut)
	}

	stdout, stderr, err := executeCommandStreams("hub", "restart", "--json")
	if err != nil {
		t.Fatalf("hub restart --json failed: %v (stdout: %s, stderr: %s)", err, stdout, stderr)
	}
	_ = d1.Wait()
	var restartRes map[string]any
	if err := json.Unmarshal([]byte(stdout), &restartRes); err != nil {
		t.Fatalf("stdout must be pure valid JSON on hub restart --json, err=%v raw=%q", err, stdout)
	}
	if restartRes["needs_reconnect"] != true {
		t.Fatalf("expected needs_reconnect=true, got %v", restartRes)
	}
	if !strings.Contains(stderr, hubReconnectNote) {
		t.Fatalf("expected reconnect note on stderr in JSON mode, got %q", stderr)
	}
	_ = os.Remove(desc1)

	// 2) Human mode
	d2, wait2 := startCLIHelperProcess(t, "language_server", "--standalone", "--subclient_type", "hub", "--workspace_id", "agy-hub-ssh")
	defer func() {
		if d2.Process != nil {
			_ = d2.Process.Kill()
		}
		_ = d2.Wait()
	}()
	wait2()

	desc2 := filepath.Join(daemonDir, "ls_2.json")
	if err := os.WriteFile(desc2, []byte(fmt.Sprintf(`{"pid":%d,"httpsPort":41003,"httpPort":41004,"lsVersion":"2.35.1"}`, d2.Process.Pid)), 0600); err != nil {
		t.Fatal(err)
	}
	stdout, stderr, err = executeCommandStreams("hub", "restart")
	if err != nil {
		t.Fatalf("hub restart failed: %v (stdout: %s, stderr: %s)", err, stdout, stderr)
	}
	_ = d2.Wait()
	if !strings.Contains(stdout, hubReconnectNote) {
		t.Fatalf("expected reconnect note on stdout in human mode, got %q", stdout)
	}
	if stderr != "" {
		t.Fatalf("expected empty stderr in human mode, got %q", stderr)
	}
}

func startCLIHelperProcess(t *testing.T, trailingArgs ...string) (*exec.Cmd, func()) {
	t.Helper()
	args := []string{"-test.run=^TestCLIHelperProcess$"}
	if len(trailingArgs) > 0 {
		args = append(args, "--")
		args = append(args, trailingArgs...)
	}
	child := exec.Command(os.Args[0], args...)
	child.Env = append(os.Environ(), "GO_WANT_CLI_HELPER_PROCESS=1")
	stdoutPipe, err := child.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := child.Start(); err != nil {
		t.Fatalf("failed to start dummy subprocess: %v", err)
	}

	waitReady := func() {
		t.Helper()
		readyCh := make(chan error, 1)
		go func() {
			line, readErr := bufio.NewReader(stdoutPipe).ReadString('\n')
			if readErr != nil {
				readyCh <- readErr
				return
			}
			if strings.TrimSpace(line) != "READY" {
				readyCh <- fmt.Errorf("unexpected ready line %q", line)
				return
			}
			readyCh <- nil
		}()

		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		select {
		case <-ctx.Done():
			_ = child.Process.Kill()
			t.Fatalf("timed out waiting for dummy subprocess ready")
		case readyErr := <-readyCh:
			if readyErr != nil {
				_ = child.Process.Kill()
				t.Fatalf("dummy subprocess failed to become ready: %v", readyErr)
			}
		}
	}

	return child, waitReady
}

func TestCLIHelperProcess(t *testing.T) {
	if os.Getenv("GO_WANT_CLI_HELPER_PROCESS") != "1" {
		return
	}
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM)
	fmt.Println("READY")
	<-sigCh
	os.Exit(0)
}
