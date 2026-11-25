//go:build linux || darwin

package hub

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"syscall"
	"time"
)

const (
	maxAncestorDepth   = 64
	sigkillWaitTimeout = 2 * time.Second
	shutdownPollEvery  = 100 * time.Millisecond
	respawnPollEvery   = 200 * time.Millisecond
)

func isProcessAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	if err := syscall.Kill(pid, 0); err != nil {
		return false
	}
	return !isProcessDeadOrZombie(pid)
}

// IsDescendantOf reports whether the current process is a descendant of (or
// matches) any PID in targetPIDs by walking parent PIDs.
func IsDescendantOf(targetPIDs []int) bool {
	targets := make(map[int]struct{}, len(targetPIDs))
	for _, pid := range targetPIDs {
		if pid > 0 {
			targets[pid] = struct{}{}
		}
	}
	if len(targets) == 0 {
		return false
	}

	visited := make(map[int]struct{}, 16)
	curr := os.Getpid()
	for depth := 0; depth < maxAncestorDepth && curr > 0; depth++ {
		if _, ok := targets[curr]; ok {
			return true
		}
		if curr <= 1 {
			break
		}
		if _, seen := visited[curr]; seen {
			break
		}
		visited[curr] = struct{}{}

		ppid, err := readParentPID(curr)
		if err != nil || ppid <= 0 || ppid == curr {
			break
		}
		curr = ppid
	}
	return false
}

// RestartDaemons discovers running Antigravity Hub language_server processes
// and restarts them cleanly. If the current process runs inside a target
// language_server tree, it spawns a detached helper and returns Deferred=true.
func RestartDaemons(ctx context.Context, daemonDir string, opts RestartOptions) (*RestartResult, error) {
	if opts.NoRestart {
		return &RestartResult{}, nil
	}
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	opts = normalizeRestartOptions(opts)

	daemons, err := DiscoverDaemons(daemonDir)
	if err != nil {
		return nil, err
	}
	if len(daemons) == 0 {
		return &RestartResult{}, nil
	}

	pids := uniquePositivePIDs(daemons)
	if IsDescendantOf(pids) {
		if err := startDetachedRestart(pids, opts.SelfDetachDelay); err != nil {
			return nil, err
		}
		return &RestartResult{
			Deferred:     true,
			DeferredPIDs: pids,
		}, nil
	}

	return RestartPIDs(ctx, pids, daemonDir, opts)
}

// RestartPIDs sends SIGTERM to each live target PID, falls back to SIGKILL if
// still running after ShutdownTimeout, and polls daemonDir for respawned Hub
// instances up to RespawnWaitTimeout.
func RestartPIDs(ctx context.Context, pids []int, daemonDir string, opts RestartOptions) (*RestartResult, error) {
	if opts.NoRestart {
		return &RestartResult{}, nil
	}
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	opts = normalizeRestartOptions(opts)

	targets := filterAlivePIDs(uniqueInts(pids))
	if len(targets) == 0 {
		return &RestartResult{}, nil
	}

	for _, pid := range targets {
		if err := syscall.Kill(pid, syscall.SIGTERM); err != nil && !errors.Is(err, syscall.ESRCH) {
			return nil, fmt.Errorf("failed to send SIGTERM to pid %d: %w", pid, err)
		}
	}

	stillAlive, err := waitForPIDsExit(ctx, targets, opts.ShutdownTimeout, shutdownPollEvery)
	if err != nil {
		return nil, err
	}

	if len(stillAlive) > 0 {
		for _, pid := range stillAlive {
			if err := syscall.Kill(pid, syscall.SIGKILL); err != nil && !errors.Is(err, syscall.ESRCH) {
				return nil, fmt.Errorf("failed to send SIGKILL to pid %d: %w", pid, err)
			}
		}

		remaining, err := waitForPIDsExit(ctx, stillAlive, sigkillWaitTimeout, shutdownPollEvery)
		if err != nil {
			return nil, err
		}
		if len(remaining) > 0 {
			return nil, fmt.Errorf("pids %v remained alive after SIGKILL", remaining)
		}
	}

	result := &RestartResult{
		StoppedPIDs: targets,
	}

	if daemonDir == "" {
		result.NeedsReconnect = true
		return result, nil
	}

	respawned, err := waitForRespawn(ctx, daemonDir, targets, opts.RespawnWaitTimeout, respawnPollEvery)
	if err != nil {
		return result, err
	}
	if len(respawned) > 0 {
		result.RespawnedPIDs = respawned
	} else {
		result.NeedsReconnect = true
	}
	return result, nil
}

func startDetachedRestart(pids []int, delay time.Duration) error {
	exe, err := os.Executable()
	if err != nil {
		return fmt.Errorf("failed to resolve executable for detached restart: %w", err)
	}

	args := make([]string, 0, 4+len(pids))
	args = append(args, "hub", "_restart-pids", "--delay", delay.String())
	for _, pid := range pids {
		args = append(args, strconv.Itoa(pid))
	}

	devNull, err := os.OpenFile(os.DevNull, os.O_RDWR, 0)
	if err != nil {
		return fmt.Errorf("failed to open %s for detached restart: %w", os.DevNull, err)
	}
	defer func() { _ = devNull.Close() }()

	// Intentionally detached from ctx so the child outlives the exiting parent.
	cmd := exec.Command(exe, args...)
	cmd.Stdin = devNull
	cmd.Stdout = devNull
	cmd.Stderr = devNull
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("failed to start detached hub restart helper: %w", err)
	}
	return nil
}

func uniquePositivePIDs(daemons []DaemonInfo) []int {
	raw := make([]int, 0, len(daemons))
	for _, d := range daemons {
		raw = append(raw, d.PID)
	}
	return uniqueInts(raw)
}

func uniqueInts(in []int) []int {
	seen := make(map[int]struct{}, len(in))
	out := make([]int, 0, len(in))
	for _, v := range in {
		if v <= 0 {
			continue
		}
		if _, ok := seen[v]; ok {
			continue
		}
		seen[v] = struct{}{}
		out = append(out, v)
	}
	return out
}

func filterAlivePIDs(pids []int) []int {
	var alive []int
	for _, pid := range pids {
		if isProcessAlive(pid) {
			alive = append(alive, pid)
		}
	}
	return alive
}

func effectivePollInterval(timeout, desired time.Duration) time.Duration {
	if desired <= 0 || timeout < desired {
		return max(timeout/4, 10*time.Millisecond)
	}
	return desired
}

func waitForPIDsExit(ctx context.Context, pids []int, timeout, pollInterval time.Duration) ([]int, error) {
	remaining := filterAlivePIDs(pids)
	if len(remaining) == 0 {
		return nil, nil
	}

	timer := time.NewTimer(timeout)
	defer timer.Stop()
	ticker := time.NewTicker(effectivePollInterval(timeout, pollInterval))
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return remaining, ctx.Err()
		case <-timer.C:
			return filterAlivePIDs(remaining), nil
		case <-ticker.C:
			remaining = filterAlivePIDs(remaining)
			if len(remaining) == 0 {
				return nil, nil
			}
		}
	}
}

func waitForRespawn(ctx context.Context, daemonDir string, stoppedPIDs []int, timeout, pollInterval time.Duration) ([]int, error) {
	stoppedSet := make(map[int]struct{}, len(stoppedPIDs))
	for _, pid := range stoppedPIDs {
		stoppedSet[pid] = struct{}{}
	}

	findNew := func() ([]int, error) {
		daemons, err := DiscoverDaemons(daemonDir)
		if err != nil {
			return nil, err
		}
		var respawned []int
		seen := make(map[int]struct{}, len(daemons))
		for _, d := range daemons {
			if _, wasStopped := stoppedSet[d.PID]; wasStopped {
				continue
			}
			if _, dup := seen[d.PID]; dup {
				continue
			}
			seen[d.PID] = struct{}{}
			respawned = append(respawned, d.PID)
		}
		return respawned, nil
	}

	if respawned, err := findNew(); err != nil {
		return nil, err
	} else if len(respawned) > 0 {
		return respawned, nil
	}

	timer := time.NewTimer(timeout)
	defer timer.Stop()
	ticker := time.NewTicker(effectivePollInterval(timeout, pollInterval))
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-timer.C:
			return findNew()
		case <-ticker.C:
			respawned, err := findNew()
			if err != nil {
				return nil, err
			}
			if len(respawned) > 0 {
				return respawned, nil
			}
		}
	}
}
