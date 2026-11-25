package hub

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

func TestIsHubLanguageServerCmdline(t *testing.T) {
	tests := []struct {
		name    string
		cmdline string
		want    bool
	}{
		{
			name:    "hub ssh standard cmdline",
			cmdline: "language_server --standalone --subclient_type hub --workspace_id agy-hub-ssh",
			want:    true,
		},
		{
			name:    "full antigravity hub server path",
			cmdline: "/home/opmc/.antigravity-hub-server/bin/2.35.1-x64/language_server --standalone --override_ide_name antigravity --subclient_type hub --workspace_id agy-hub-ssh",
			want:    true,
		},
		{
			name:    "equals flag syntax",
			cmdline: "/usr/local/bin/language_server --subclient_type=hub",
			want:    true,
		},
		{
			name:    "hub server path marker",
			cmdline: "/home/user/.antigravity-hub-server/bin/2.35.1/language_server --standalone",
			want:    true,
		},
		{
			name:    "ide language server without hub markers",
			cmdline: "language_server_linux_x64",
			want:    false,
		},
		{
			name:    "shell binary",
			cmdline: "/usr/bin/bash",
			want:    false,
		},
		{
			name:    "empty cmdline",
			cmdline: "",
			want:    false,
		},
		{
			name:    "hub flags without language_server binary",
			cmdline: "/usr/bin/python3 --subclient_type hub --workspace_id agy-hub-ssh",
			want:    false,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := IsHubLanguageServerCmdline(tc.cmdline); got != tc.want {
				t.Fatalf("IsHubLanguageServerCmdline(%q) = %v, want %v", tc.cmdline, got, tc.want)
			}
		})
	}
}

func TestDiscoverDaemonsFiltersStaleAndUnrelated(t *testing.T) {
	daemonDir := t.TempDir()

	// 1. Dead PID
	deadJSON := `{"pid":99999999,"httpsPort":40001,"httpPort":40002,"lspPort":0,"lsVersion":"2.35.1","csrfToken":"tok1"}`
	if err := os.WriteFile(filepath.Join(daemonDir, "ls_dead.json"), []byte(deadJSON), 0600); err != nil {
		t.Fatal(err)
	}

	// 2. Live PID belonging to an unrelated process (current test process)
	unrelatedJSON := fmt.Sprintf(`{"pid":%d,"httpsPort":40003,"httpPort":40004,"lspPort":0,"lsVersion":"2.35.1","csrfToken":"tok2"}`, os.Getpid())
	if err := os.WriteFile(filepath.Join(daemonDir, "ls_unrelated.json"), []byte(unrelatedJSON), 0600); err != nil {
		t.Fatal(err)
	}

	// 3. Malformed JSON
	if err := os.WriteFile(filepath.Join(daemonDir, "ls_corrupt.json"), []byte("{not-json"), 0600); err != nil {
		t.Fatal(err)
	}

	// 4. Non-positive PID
	if err := os.WriteFile(filepath.Join(daemonDir, "ls_zero.json"), []byte(`{"pid":0}`), 0600); err != nil {
		t.Fatal(err)
	}

	// 5. Companion non-json metadata files must be ignored
	if err := os.WriteFile(filepath.Join(daemonDir, "ls_dead.json.host"), []byte("127.0.0.1"), 0600); err != nil {
		t.Fatal(err)
	}

	daemons, err := DiscoverDaemons(daemonDir)
	if err != nil {
		t.Fatalf("DiscoverDaemons failed: %v", err)
	}
	if len(daemons) != 0 {
		t.Fatalf("expected 0 daemons, got %+v", daemons)
	}

	// Empty / missing directory should also return 0 daemons without error
	missingDaemons, err := DiscoverDaemons(filepath.Join(daemonDir, "does-not-exist"))
	if err != nil {
		t.Fatalf("DiscoverDaemons on missing dir failed: %v", err)
	}
	if len(missingDaemons) != 0 {
		t.Fatalf("expected 0 daemons for missing dir, got %+v", missingDaemons)
	}
}

func TestDiscoverDaemonsMatchesLiveHubProcess(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("unix process discovery test")
	}

	cmd, waitReady := startHelperProcess(t, "graceful-sigterm", nil,
		"language_server", "--standalone", "--subclient_type", "hub", "--workspace_id", "agy-hub-ssh",
	)
	defer func() {
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
		}
		_ = cmd.Wait()
	}()
	waitReady()

	daemonDir := t.TempDir()
	descPath := filepath.Join(daemonDir, "ls_live.json")
	liveJSON := fmt.Sprintf(`{"pid":%d,"httpsPort":42535,"httpPort":38797,"lspPort":0,"lsVersion":"2.35.1","csrfToken":"tok-live"}`, cmd.Process.Pid)
	if err := os.WriteFile(descPath, []byte(liveJSON), 0600); err != nil {
		t.Fatal(err)
	}

	daemons, err := DiscoverDaemons(daemonDir)
	if err != nil {
		t.Fatalf("DiscoverDaemons failed: %v", err)
	}
	if len(daemons) != 1 {
		t.Fatalf("expected 1 daemon, got %+v", daemons)
	}
	got := daemons[0]
	if got.FilePath != descPath || got.PID != cmd.Process.Pid || got.HTTPSPort != 42535 || got.HTTPPort != 38797 || got.LSVersion != "2.35.1" {
		t.Fatalf("unexpected DaemonInfo: %+v", got)
	}
}

func TestRestartPIDsGracefulAndSIGKILLFallback(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("unix signal lifecycle test")
	}

	t.Run("graceful SIGTERM", func(t *testing.T) {
		cmd, waitReady := startHelperProcess(t, "graceful-sigterm", nil)
		waitReady()
		pid := cmd.Process.Pid

		start := time.Now()
		res, err := RestartPIDs(context.Background(), []int{pid}, "", RestartOptions{
			ShutdownTimeout:    2 * time.Second,
			RespawnWaitTimeout: 10 * time.Millisecond,
		})
		elapsed := time.Since(start)
		if err != nil {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
			t.Fatalf("RestartPIDs failed: %v", err)
		}
		if res.Deferred {
			t.Fatalf("expected non-deferred restart, got %+v", res)
		}
		if !slices.Contains(res.StoppedPIDs, pid) {
			t.Fatalf("expected StoppedPIDs to contain %d, got %+v", pid, res.StoppedPIDs)
		}
		if elapsed >= 1500*time.Millisecond {
			t.Fatalf("expected graceful SIGTERM well within timeout, took %v", elapsed)
		}

		// Verify child exited cleanly with status 0 from its SIGTERM handler (not SIGKILL)
		if waitErr := cmd.Wait(); waitErr != nil {
			t.Fatalf("expected helper to exit 0 on SIGTERM, got %v", waitErr)
		}
	})

	t.Run("SIGKILL fallback", func(t *testing.T) {
		cmd, waitReady := startHelperProcess(t, "ignore-sigterm", nil)
		waitReady()
		pid := cmd.Process.Pid

		start := time.Now()
		res, err := RestartPIDs(context.Background(), []int{pid}, "", RestartOptions{
			ShutdownTimeout:    300 * time.Millisecond,
			RespawnWaitTimeout: 10 * time.Millisecond,
		})
		elapsed := time.Since(start)
		if err != nil {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
			t.Fatalf("RestartPIDs failed: %v", err)
		}
		if !slices.Contains(res.StoppedPIDs, pid) {
			t.Fatalf("expected StoppedPIDs to contain %d, got %+v", pid, res.StoppedPIDs)
		}
		if elapsed < 250*time.Millisecond {
			t.Fatalf("expected ShutdownTimeout before SIGKILL fallback, elapsed=%v", elapsed)
		}

		waitErr := cmd.Wait()
		if waitErr == nil {
			t.Fatalf("expected killed helper to return non-nil Wait error")
		}
		var exitErr *exec.ExitError
		if !errors.As(waitErr, &exitErr) {
			t.Fatalf("expected ExitError, got %T: %v", waitErr, waitErr)
		}
		status, ok := exitErr.Sys().(syscall.WaitStatus)
		if !ok || status.Signal() != syscall.SIGKILL {
			t.Fatalf("expected helper terminated by SIGKILL, got waitErr=%v status=%+v", waitErr, status)
		}
	})
}

func TestRestartDaemonsNoRestartAndRespawnDetection(t *testing.T) {
	t.Run("NoRestart returns empty result immediately", func(t *testing.T) {
		daemonDir := t.TempDir()
		res, err := RestartDaemons(context.Background(), daemonDir, RestartOptions{NoRestart: true})
		if err != nil {
			t.Fatalf("RestartDaemons NoRestart failed: %v", err)
		}
		if res == nil || len(res.StoppedPIDs) != 0 || len(res.RespawnedPIDs) != 0 || res.Deferred || res.NeedsReconnect {
			t.Fatalf("expected empty RestartResult, got %+v", res)
		}
	})

	if runtime.GOOS == "windows" {
		t.Skip("unix respawn detection test")
	}

	t.Run("detects respawned daemon within timeout", func(t *testing.T) {
		daemonDir := t.TempDir()

		oldCmd, waitOldReady := startHelperProcess(t, "graceful-sigterm", nil,
			"language_server", "--subclient_type", "hub", "--workspace_id", "agy-hub-ssh",
		)
		waitOldReady()
		defer func() {
			if oldCmd.Process != nil {
				_ = oldCmd.Process.Kill()
			}
			_ = oldCmd.Wait()
		}()

		oldJSON := fmt.Sprintf(`{"pid":%d,"httpsPort":41001,"httpPort":41002,"lsVersion":"2.35.1"}`, oldCmd.Process.Pid)
		if err := os.WriteFile(filepath.Join(daemonDir, "ls_old.json"), []byte(oldJSON), 0600); err != nil {
			t.Fatal(err)
		}

		newCmd, waitNewReady := startHelperProcess(t, "graceful-sigterm", nil,
			"language_server", "--subclient_type", "hub", "--workspace_id", "agy-hub-ssh",
		)
		waitNewReady()
		defer func() {
			if newCmd.Process != nil {
				_ = newCmd.Process.Kill()
			}
			_ = newCmd.Wait()
		}()

		// Simulate agy-keeper writing the respawned ls_*.json shortly after old LS exits
		var wg sync.WaitGroup
		defer wg.Wait()
		wg.Add(1)
		go func() {
			defer wg.Done()
			time.Sleep(150 * time.Millisecond)
			newJSON := fmt.Sprintf(`{"pid":%d,"httpsPort":41003,"httpPort":41004,"lsVersion":"2.35.1"}`, newCmd.Process.Pid)
			_ = os.WriteFile(filepath.Join(daemonDir, "ls_new.json"), []byte(newJSON), 0600)
		}()

		res, err := RestartDaemons(context.Background(), daemonDir, RestartOptions{
			ShutdownTimeout:    2 * time.Second,
			RespawnWaitTimeout: 1500 * time.Millisecond,
		})
		if err != nil {
			t.Fatalf("RestartDaemons failed: %v", err)
		}
		if !slices.Contains(res.StoppedPIDs, oldCmd.Process.Pid) {
			t.Fatalf("expected StoppedPIDs to contain %d, got %+v", oldCmd.Process.Pid, res.StoppedPIDs)
		}
		if !slices.Contains(res.RespawnedPIDs, newCmd.Process.Pid) {
			t.Fatalf("expected RespawnedPIDs to contain %d, got %+v", newCmd.Process.Pid, res.RespawnedPIDs)
		}
		if res.NeedsReconnect {
			t.Fatalf("expected NeedsReconnect=false when respawn detected, got true")
		}
	})

	t.Run("sets NeedsReconnect when no daemon respawns", func(t *testing.T) {
		daemonDir := t.TempDir()

		cmd, waitReady := startHelperProcess(t, "graceful-sigterm", nil,
			"language_server", "--subclient_type", "hub", "--workspace_id", "agy-hub-ssh",
		)
		waitReady()
		defer func() {
			if cmd.Process != nil {
				_ = cmd.Process.Kill()
			}
			_ = cmd.Wait()
		}()

		descJSON := fmt.Sprintf(`{"pid":%d,"httpsPort":41005,"httpPort":41006,"lsVersion":"2.35.1"}`, cmd.Process.Pid)
		if err := os.WriteFile(filepath.Join(daemonDir, "ls_single.json"), []byte(descJSON), 0600); err != nil {
			t.Fatal(err)
		}

		res, err := RestartDaemons(context.Background(), daemonDir, RestartOptions{
			ShutdownTimeout:    2 * time.Second,
			RespawnWaitTimeout: 250 * time.Millisecond,
		})
		if err != nil {
			t.Fatalf("RestartDaemons failed: %v", err)
		}
		if !slices.Contains(res.StoppedPIDs, cmd.Process.Pid) {
			t.Fatalf("expected StoppedPIDs to contain %d, got %+v", cmd.Process.Pid, res.StoppedPIDs)
		}
		if !res.NeedsReconnect || len(res.RespawnedPIDs) != 0 {
			t.Fatalf("expected NeedsReconnect=true and no RespawnedPIDs, got %+v", res)
		}
	})
}

func TestIsDescendantOf(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("unix process tree test")
	}

	if !IsDescendantOf([]int{os.Getppid()}) {
		t.Fatalf("expected current test process (pid=%d) to be descendant of ppid=%d", os.Getpid(), os.Getppid())
	}
	if IsDescendantOf([]int{99999999}) {
		t.Fatalf("expected current test process not to be descendant of nonexistent pid")
	}
	if IsDescendantOf(nil) {
		t.Fatalf("expected empty target PIDs to return false")
	}

	// Verify multi-hop ancestor walk: test -> parentHelper -> childHelper
	args := []string{"-test.run=^TestHelperProcess$"}
	cmd := exec.Command(os.Args[0], args...)
	cmd.Env = append(os.Environ(),
		"GO_WANT_HELPER_PROCESS=1",
		"HUB_HELPER_MODE=descendant-parent",
		"HUB_GRANDPARENT_PID="+strconv.Itoa(os.Getpid()),
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("multi-hop descendant helper failed: %v\noutput:\n%s", err, string(out))
	}
}

func TestRestartPIDsDeadAndEmptyPIDs(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("unix process lifecycle test")
	}

	res, err := RestartPIDs(context.Background(), []int{0, -1, 99999999}, "", RestartOptions{})
	if err != nil {
		t.Fatalf("RestartPIDs with dead/invalid pids failed: %v", err)
	}
	if res == nil || len(res.StoppedPIDs) != 0 || len(res.RespawnedPIDs) != 0 || res.Deferred || res.NeedsReconnect {
		t.Fatalf("expected empty RestartResult for dead/invalid pids, got %+v", res)
	}
}

func TestNormalizeRestartOptionsDefaults(t *testing.T) {
	opts := normalizeRestartOptions(RestartOptions{})
	if opts.ShutdownTimeout != 5*time.Second {
		t.Fatalf("expected default ShutdownTimeout 5s, got %v", opts.ShutdownTimeout)
	}
	if opts.RespawnWaitTimeout != 3*time.Second {
		t.Fatalf("expected default RespawnWaitTimeout 3s, got %v", opts.RespawnWaitTimeout)
	}
	if opts.SelfDetachDelay != 300*time.Millisecond {
		t.Fatalf("expected default SelfDetachDelay 300ms, got %v", opts.SelfDetachDelay)
	}
}

func startHelperProcess(t *testing.T, mode string, extraEnv []string, trailingArgs ...string) (*exec.Cmd, func()) {
	t.Helper()
	args := []string{"-test.run=^TestHelperProcess$"}
	if len(trailingArgs) > 0 {
		args = append(args, "--")
		args = append(args, trailingArgs...)
	}
	cmd := exec.Command(os.Args[0], args...)
	env := append(os.Environ(),
		"GO_WANT_HELPER_PROCESS=1",
		"HUB_HELPER_MODE="+mode,
	)
	env = append(env, extraEnv...)
	cmd.Env = env

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatalf("StdoutPipe failed: %v", err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatalf("failed to start helper process: %v", err)
	}

	waitReady := func() {
		t.Helper()
		done := make(chan string, 1)
		errCh := make(chan error, 1)
		go func() {
			line, readErr := bufio.NewReader(stdout).ReadString('\n')
			if readErr != nil {
				errCh <- readErr
				return
			}
			done <- strings.TrimSpace(line)
		}()

		select {
		case line := <-done:
			if line != "READY" {
				t.Fatalf("expected helper READY line, got %q", line)
			}
		case readErr := <-errCh:
			t.Fatalf("helper exited before READY: %v", readErr)
		case <-time.After(5 * time.Second):
			_ = cmd.Process.Kill()
			t.Fatalf("timed out waiting for helper READY")
		}
	}

	return cmd, waitReady
}

func TestHelperProcess(t *testing.T) {
	if os.Getenv("GO_WANT_HELPER_PROCESS") != "1" {
		return
	}

	switch os.Getenv("HUB_HELPER_MODE") {
	case "graceful-sigterm":
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGTERM)
		fmt.Println("READY")
		<-sigCh
		os.Exit(0)

	case "ignore-sigterm":
		signal.Ignore(syscall.SIGTERM)
		fmt.Println("READY")
		for {
			time.Sleep(time.Second)
		}

	case "descendant-parent":
		parentPID := os.Getpid()
		child := exec.Command(os.Args[0], "-test.run=^TestHelperProcess$")
		child.Env = append(os.Environ(),
			"GO_WANT_HELPER_PROCESS=1",
			"HUB_HELPER_MODE=descendant-child",
			"HUB_ANCESTOR_PID="+strconv.Itoa(parentPID),
			"HUB_UNRELATED_PID=99999999",
		)
		out, err := child.CombinedOutput()
		if err != nil {
			fmt.Fprintf(os.Stderr, "descendant-child failed: %v\n%s\n", err, string(out))
			os.Exit(1)
		}
		os.Exit(0)

	case "descendant-child":
		ancestorPID, err := strconv.Atoi(os.Getenv("HUB_ANCESTOR_PID"))
		if err != nil || ancestorPID <= 0 {
			fmt.Fprintf(os.Stderr, "invalid HUB_ANCESTOR_PID: %q\n", os.Getenv("HUB_ANCESTOR_PID"))
			os.Exit(2)
		}
		unrelatedPID, err := strconv.Atoi(os.Getenv("HUB_UNRELATED_PID"))
		if err != nil {
			fmt.Fprintf(os.Stderr, "invalid HUB_UNRELATED_PID: %q\n", os.Getenv("HUB_UNRELATED_PID"))
			os.Exit(2)
		}
		// Verify multi-hop ancestor: both immediate parent and grandparent (2 hops) must match.
		grandparentPID, err := strconv.Atoi(os.Getenv("HUB_GRANDPARENT_PID"))
		if err != nil || grandparentPID <= 0 {
			fmt.Fprintf(os.Stderr, "invalid HUB_GRANDPARENT_PID: %q\n", os.Getenv("HUB_GRANDPARENT_PID"))
			os.Exit(2)
		}
		if !IsDescendantOf([]int{ancestorPID}) {
			fmt.Fprintf(os.Stderr, "expected IsDescendantOf([%d]) == true for pid %d (ppid %d)\n", ancestorPID, os.Getpid(), os.Getppid())
			os.Exit(3)
		}
		if !IsDescendantOf([]int{grandparentPID}) {
			fmt.Fprintf(os.Stderr, "expected IsDescendantOf([%d]) == true (2-hop grandparent) for pid %d (ppid %d)\n", grandparentPID, os.Getpid(), os.Getppid())
			os.Exit(3)
		}
		if IsDescendantOf([]int{unrelatedPID}) {
			fmt.Fprintf(os.Stderr, "expected IsDescendantOf([%d]) == false for pid %d\n", unrelatedPID, os.Getpid())
			os.Exit(4)
		}
		os.Exit(0)

	default:
		fmt.Fprintf(os.Stderr, "unknown HUB_HELPER_MODE: %q\n", os.Getenv("HUB_HELPER_MODE"))
		os.Exit(1)
	}
}
