package watcher

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/alirezaopmc/promux/pkg/failover"
	"github.com/alirezaopmc/promux/pkg/quota"
	"github.com/alirezaopmc/promux/pkg/storage"
)

func TestParseResetMinutes(t *testing.T) {
	tests := []struct {
		input string
		want  int
	}{
		{"", 60},
		{"45m", 45},
		{"45 mins", 45},
		{"2h", 120},
		{"2 hours", 120},
		{"~1h 30m", 90},
		{"~ 3 hours 10 mins", 190},
		{"1d 2h", 1560},
	}

	for _, tt := range tests {
		got := ParseResetMinutes(tt.input)
		if got != tt.want {
			t.Errorf("ParseResetMinutes(%q) = %d, want %d", tt.input, got, tt.want)
		}
	}
}

func TestLogWatcherDetectsErrors(t *testing.T) {
	tmpDir := t.TempDir()
	logPath := filepath.Join(tmpDir, "cli.log")

	_ = os.WriteFile(logPath, []byte("Starting session\n"), 0644)

	watcher := NewLogWatcher(nil, nil, tmpDir)
	watcher.CustomLogs = []string{logPath}
	watcher.InitOffsets()

	// Append individual quota error
	f, err := os.OpenFile(logPath, os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		t.Fatalf("failed to open log for append: %v", err)
	}
	_, _ = f.WriteString("Error: Individual quota reached. Resets in 45m.\n")
	_ = f.Close()

	matches, err := watcher.RunOnce()
	if err != nil {
		t.Fatalf("RunOnce failed: %v", err)
	}
	if len(matches) != 1 {
		t.Fatalf("expected 1 match, got %d", len(matches))
	}
	if matches[0].Pattern != "INDIVIDUAL_QUOTA" || matches[0].ResetHint != "45m" {
		t.Fatalf("unexpected match: %+v", matches[0])
	}

	// Truncate file (log rotation) and write RESOURCE_EXHAUSTED
	_ = os.WriteFile(logPath, []byte("RESOURCE_EXHAUSTED (code 429)\n"), 0644)
	matches2, err := watcher.RunOnce()
	if err != nil {
		t.Fatalf("RunOnce failed after truncation: %v", err)
	}
	if len(matches2) != 1 || matches2[0].Pattern != "RESOURCE_EXHAUSTED" {
		t.Fatalf("expected RESOURCE_EXHAUSTED after rotation, got: %+v", matches2)
	}
}

func TestLogWatcherFailoverIntegration(t *testing.T) {
	tmpDir := t.TempDir()
	promuxDir := filepath.Join(tmpDir, "promux")
	geminiDir := filepath.Join(tmpDir, "gemini")
	_ = os.MkdirAll(geminiDir, 0755)

	liveToken := filepath.Join(geminiDir, "antigravity-oauth-token")
	_ = os.WriteFile(liveToken, []byte(`{"access_token": "token-1"}`), 0600)

	store := storage.New(promuxDir, geminiDir)
	_, _ = store.SaveProfile("alpha", nil, nil, "STANDARD")

	_ = os.WriteFile(liveToken, []byte(`{"access_token": "token-2"}`), 0600)
	_, _ = store.SaveProfile("beta", nil, nil, "STANDARD")

	_ = store.SwitchProfile("alpha")

	fo := failover.New(store)
	logFile := filepath.Join(geminiDir, "cli.log")
	_ = os.WriteFile(logFile, []byte("initial log\n"), 0644)

	watcher := NewLogWatcher(store, fo, geminiDir)
	watcher.PollInterval = 20 * time.Millisecond

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	matchedChan := make(chan LogMatch, 1)
	go func() {
		_ = watcher.RunForever(ctx, func(m LogMatch) {
			matchedChan <- m
		})
	}()

	time.Sleep(50 * time.Millisecond)

	// Append quota error
	f, _ := os.OpenFile(logFile, os.O_APPEND|os.O_WRONLY, 0644)
	_, _ = f.WriteString("RESOURCE_EXHAUSTED\n")
	_ = f.Close()

	select {
	case m := <-matchedChan:
		if m.Pattern != "RESOURCE_EXHAUSTED" {
			t.Fatalf("unexpected pattern: %s", m.Pattern)
		}
	case <-time.After(2 * time.Second):
		t.Fatalf("timed out waiting for watcher match")
	}

	// Verify failover switched to beta
	time.Sleep(100 * time.Millisecond)
	state, _ := store.LoadState()
	if state.Active == nil || *state.Active != "beta" {
		t.Fatalf("expected failover to beta, got %v", state.Active)
	}
}

func TestWatcherAutoPrimeConfig(t *testing.T) {
	w := NewWatcher("/tmp/test", 10*time.Second, true)
	if !w.AutoPrime {
		t.Errorf("expected AutoPrime to be true")
	}
}

func TestWatcherAutoPrimeLoop(t *testing.T) {
	origPrime := primeAllEligible
	origLog := logPrintf
	defer func() {
		primeAllEligible = origPrime
		logPrintf = origLog
	}()

	var primedCalls int
	var loggedMessages []string

	primeAllEligible = func(ctx context.Context, store *storage.StorageEngine, cache *quota.QuotaCache, force bool, shouldSkip func(account string) bool) ([]quota.PrimeResult, error) {
		primedCalls++
		return []quota.PrimeResult{
			{Account: "acc-1", Primed: true},
			{Account: "acc-2", Primed: false, Reason: "active countdown"},
		}, nil
	}

	logPrintf = func(format string, v ...any) {
		loggedMessages = append(loggedMessages, fmt.Sprintf(format, v...))
	}

	tmpDir := t.TempDir()
	promuxDir := filepath.Join(tmpDir, "promux")
	geminiDir := filepath.Join(tmpDir, "gemini")
	_ = os.MkdirAll(geminiDir, 0755)

	store := storage.New(promuxDir, geminiDir)
	_, _ = store.SaveProfile("acc-1", nil, nil, "STANDARD")

	w := NewLogWatcher(store, nil, geminiDir)
	w.AutoPrime = true
	w.PollInterval = 20 * time.Millisecond
	w.PrimeInterval = 30 * time.Millisecond

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	done := make(chan struct{})
	go func() {
		_ = w.RunForever(ctx, nil)
		close(done)
	}()

	// Wait for tick to execute
	time.Sleep(80 * time.Millisecond)
	cancel()
	<-done

	if primedCalls == 0 {
		t.Fatal("expected primeAllEligible to be called during auto-prime tick")
	}

	foundLog := false
	for _, msg := range loggedMessages {
		if strings.Contains(msg, "[auto-prime] Primed account acc-1") {
			foundLog = true
			break
		}
	}
	if !foundLog {
		t.Fatalf("expected log '[auto-prime] Primed account acc-1', got: %v", loggedMessages)
	}
}

func TestWatcherAutoPrimeDisabled(t *testing.T) {
	origPrime := primeAllEligible
	defer func() { primeAllEligible = origPrime }()

	var primedCalls int
	primeAllEligible = func(ctx context.Context, store *storage.StorageEngine, cache *quota.QuotaCache, force bool, shouldSkip func(account string) bool) ([]quota.PrimeResult, error) {
		primedCalls++
		return nil, nil
	}

	tmpDir := t.TempDir()
	store := storage.New(filepath.Join(tmpDir, "promux"), filepath.Join(tmpDir, "gemini"))
	w := NewLogWatcher(store, nil, tmpDir)
	w.AutoPrime = false
	w.PollInterval = 20 * time.Millisecond
	w.PrimeInterval = 20 * time.Millisecond

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	done := make(chan struct{})
	go func() {
		_ = w.RunForever(ctx, nil)
		close(done)
	}()

	time.Sleep(50 * time.Millisecond)
	cancel()
	<-done

	if primedCalls != 0 {
		t.Fatalf("expected primeAllEligible NOT to be called when AutoPrime=false, got %d calls", primedCalls)
	}
}

func TestWatcherAutoPrimeBackoff(t *testing.T) {
	origPrime := primeAllEligible
	origLog := logPrintf
	defer func() {
		primeAllEligible = origPrime
		logPrintf = origLog
	}()

	var loggedMessages []string
	logPrintf = func(format string, v ...any) {
		loggedMessages = append(loggedMessages, fmt.Sprintf(format, v...))
	}

	callCount := 0
	var lastSkipFilter func(account string) bool

	primeAllEligible = func(ctx context.Context, store *storage.StorageEngine, cache *quota.QuotaCache, force bool, shouldSkip func(account string) bool) ([]quota.PrimeResult, error) {
		callCount++
		lastSkipFilter = shouldSkip
		if callCount == 1 {
			// First run: fail "exhausted-acct" with error
			return []quota.PrimeResult{
				{Account: "exhausted-acct", Primed: false, Error: "429 RESOURCE_EXHAUSTED"},
			}, nil
		}
		return []quota.PrimeResult{}, nil
	}

	tmpDir := t.TempDir()
	store := storage.New(filepath.Join(tmpDir, "promux"), filepath.Join(tmpDir, "gemini"))
	w := NewLogWatcher(store, nil, tmpDir)
	w.AutoPrime = true
	w.PrimeInterval = 20 * time.Millisecond
	w.PollInterval = 20 * time.Millisecond

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	done := make(chan struct{})
	go func() {
		_ = w.RunForever(ctx, nil)
		close(done)
	}()

	// Wait for first call to register failure
	for i := 0; i < 50; i++ {
		w.mu.Lock()
		_, ok := w.failedPrimes["exhausted-acct"]
		w.mu.Unlock()
		if ok {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	cancel()
	<-done

	w.mu.Lock()
	failedAt, ok := w.failedPrimes["exhausted-acct"]
	w.mu.Unlock()

	if !ok {
		t.Fatal("expected exhausted-acct to be in failedPrimes")
	}
	if time.Since(failedAt) > 5*time.Second {
		t.Errorf("expected failedAt to be recent, got %v", failedAt)
	}

	// Verify log message
	foundBackoffLog := false
	for _, msg := range loggedMessages {
		if strings.Contains(msg, "[auto-prime] Failed to prime account exhausted-acct: 429 RESOURCE_EXHAUSTED (backing off 15m)") {
			foundBackoffLog = true
			break
		}
	}
	if !foundBackoffLog {
		t.Errorf("expected backoff log message, got: %v", loggedMessages)
	}

	// Verify shouldSkip skips during cooldown
	if lastSkipFilter == nil || !lastSkipFilter("exhausted-acct") {
		t.Errorf("expected skipFilter to return true for exhausted-acct during cooldown")
	}
	if lastSkipFilter != nil && lastSkipFilter("other-acct") {
		t.Errorf("expected skipFilter to return false for other-acct")
	}
}


