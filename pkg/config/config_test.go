package config

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestConfigPathsEnvOverrides(t *testing.T) {
	tmpDir := t.TempDir()
	geminiTmp := t.TempDir()

	t.Setenv("PROMUX_HOME", tmpDir)
	t.Setenv("PROMUX_GEMINI_HOME", geminiTmp)
	t.Setenv("PROMUX_HTTP_TIMEOUT", "12.5")

	if PromuxHome() != tmpDir {
		t.Fatalf("expected %s, got %s", tmpDir, PromuxHome())
	}
	if GeminiHome() != geminiTmp {
		t.Fatalf("expected %s, got %s", geminiTmp, GeminiHome())
	}
	if GetHTTPTimeout() != 12500*time.Millisecond {
		t.Fatalf("expected 12.5s, got %v", GetHTTPTimeout())
	}

	expectedState := filepath.Join(tmpDir, "tools", "agy", "state.json")
	if StateFile("agy") != expectedState {
		t.Fatalf("expected %s, got %s", expectedState, StateFile("agy"))
	}
}

func TestRegexSignatures(t *testing.T) {
	line1 := "Error: Individual quota reached. Resets in ~2h 15m."
	if !IndividualQuotaRegex.MatchString(line1) {
		t.Fatalf("failed to match individual quota regex")
	}

	sub := ResetHintRegex.FindStringSubmatch(line1)
	if len(sub) < 2 || sub[1] != "~2h 15m" {
		t.Fatalf("expected reset hint '~2h 15m', got %v", sub)
	}

	line2 := "RESOURCE_EXHAUSTED (code 429): Quota exceeded"
	if !ResourceExhaustedRegex.MatchString(line2) {
		t.Fatalf("failed to match resource exhausted regex")
	}

	line3 := "FAILED: weekly quota reached."
	if !WeeklyQuotaRegex.MatchString(line3) {
		t.Fatalf("failed to match weekly quota regex")
	}
}

func TestDetectCodeAssistURL(t *testing.T) {
	tmpGemini := t.TempDir()
	logPath := filepath.Join(tmpGemini, "cli.log")

	content := "I1016 00:02:27 http.go:349 URL: https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist\n"
	if err := os.WriteFile(logPath, []byte(content), 0644); err != nil {
		t.Fatalf("failed to write test log: %v", err)
	}

	url := DetectCodeAssistURL(tmpGemini)
	expected := "https://daily-cloudcode-pa.googleapis.com"
	if url != expected {
		t.Fatalf("expected %s, got %s", expected, url)
	}
}

func TestHubPathsEnvOverrides(t *testing.T) {
	hubGemini := t.TempDir()
	t.Setenv("PROMUX_HUB_GEMINI_DIR", hubGemini)
	t.Setenv("PROMUX_HUB_TOKEN_FILE", "")
	t.Setenv("PROMUX_HUB_DAEMON_DIR", "")

	if HubGeminiDir() != hubGemini {
		t.Fatalf("expected %s, got %s", hubGemini, HubGeminiDir())
	}
	expectedTok := filepath.Join(hubGemini, "jetski-standalone-oauth-token")
	if HubLiveTokenFile("") != expectedTok {
		t.Fatalf("expected %s, got %s", expectedTok, HubLiveTokenFile(""))
	}
	expectedDaemon := filepath.Join(hubGemini, "antigravity", "daemon")
	if HubDaemonDir("") != expectedDaemon {
		t.Fatalf("expected %s, got %s", expectedDaemon, HubDaemonDir(""))
	}

	customTok := filepath.Join(t.TempDir(), "custom-hub-token")
	customDaemon := filepath.Join(t.TempDir(), "custom-daemon")
	t.Setenv("PROMUX_HUB_TOKEN_FILE", customTok)
	t.Setenv("PROMUX_HUB_DAEMON_DIR", customDaemon)
	if HubLiveTokenFile("") != customTok {
		t.Fatalf("expected %s, got %s", customTok, HubLiveTokenFile(""))
	}
	if HubDaemonDir("") != customDaemon {
		t.Fatalf("expected %s, got %s", customDaemon, HubDaemonDir(""))
	}
}
