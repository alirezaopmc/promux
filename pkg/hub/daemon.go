package hub

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	defaultShutdownTimeout    = 5 * time.Second
	defaultRespawnWaitTimeout = 3 * time.Second
	defaultSelfDetachDelay    = 300 * time.Millisecond
)

// DaemonInfo describes a live, verified Antigravity Hub language_server process.
type DaemonInfo struct {
	FilePath  string `json:"file_path"`
	PID       int    `json:"pid"`
	HTTPSPort int    `json:"https_port"`
	HTTPPort  int    `json:"http_port"`
	LSVersion string `json:"ls_version"`
}

// RestartOptions controls how Hub language_server processes are restarted.
type RestartOptions struct {
	NoRestart          bool          `json:"no_restart"`
	ShutdownTimeout    time.Duration `json:"shutdown_timeout"`
	RespawnWaitTimeout time.Duration `json:"respawn_wait_timeout"`
	SelfDetachDelay    time.Duration `json:"self_detach_delay"`
}

// RestartResult reports the outcome of a Hub language_server restart attempt.
type RestartResult struct {
	StoppedPIDs    []int `json:"stopped_pids,omitempty"`
	RespawnedPIDs  []int `json:"respawned_pids,omitempty"`
	Deferred       bool  `json:"deferred,omitempty"`
	DeferredPIDs   []int `json:"deferred_pids,omitempty"`
	NeedsReconnect bool  `json:"needs_reconnect,omitempty"`
}

type daemonDescriptor struct {
	PID       int    `json:"pid"`
	HTTPSPort int    `json:"httpsPort"`
	HTTPPort  int    `json:"httpPort"`
	LSPPort   int    `json:"lspPort"`
	LSVersion string `json:"lsVersion"`
	CSRFToken string `json:"csrfToken"`
}

func normalizeRestartOptions(opts RestartOptions) RestartOptions {
	if opts.ShutdownTimeout <= 0 {
		opts.ShutdownTimeout = defaultShutdownTimeout
	}
	if opts.RespawnWaitTimeout <= 0 {
		opts.RespawnWaitTimeout = defaultRespawnWaitTimeout
	}
	if opts.SelfDetachDelay <= 0 {
		opts.SelfDetachDelay = defaultSelfDetachDelay
	}
	return opts
}

// IsHubLanguageServerCmdline reports whether a process command line belongs
// to an Antigravity Hub language_server instance.
func IsHubLanguageServerCmdline(cmdline string) bool {
	if !strings.Contains(cmdline, "language_server") {
		return false
	}
	return strings.Contains(cmdline, "--subclient_type hub") ||
		strings.Contains(cmdline, "--subclient_type=hub") ||
		strings.Contains(cmdline, "agy-hub-ssh") ||
		strings.Contains(cmdline, ".antigravity-hub-server")
}

// DiscoverDaemons scans daemonDir for ls_*.json descriptors and returns those
// corresponding to live, verified Antigravity Hub language_server processes.
func DiscoverDaemons(daemonDir string) ([]DaemonInfo, error) {
	if daemonDir == "" {
		return nil, nil
	}

	matches, err := filepath.Glob(filepath.Join(daemonDir, "ls_*.json"))
	if err != nil {
		return nil, fmt.Errorf("failed to glob daemon descriptors in %s: %w", daemonDir, err)
	}

	var daemons []DaemonInfo
	for _, path := range matches {
		data, err := os.ReadFile(path)
		if err != nil {
			if !os.IsNotExist(err) {
				fmt.Fprintf(os.Stderr, "warning: failed to read daemon descriptor %s: %v\n", path, err)
			}
			continue
		}

		var desc daemonDescriptor
		if err := json.Unmarshal(data, &desc); err != nil {
			fmt.Fprintf(os.Stderr, "warning: failed to parse daemon descriptor %s: %v\n", path, err)
			continue
		}

		if desc.PID <= 0 || !isProcessAlive(desc.PID) {
			continue
		}

		cmdline, err := readProcessCmdline(desc.PID)
		if err != nil {
			if !errors.Is(err, os.ErrNotExist) && !errors.Is(err, errors.ErrUnsupported) {
				fmt.Fprintf(os.Stderr, "warning: failed to read cmdline for pid %d: %v\n", desc.PID, err)
			}
			continue
		}

		if !IsHubLanguageServerCmdline(cmdline) {
			continue
		}

		daemons = append(daemons, DaemonInfo{
			FilePath:  path,
			PID:       desc.PID,
			HTTPSPort: desc.HTTPSPort,
			HTTPPort:  desc.HTTPPort,
			LSVersion: desc.LSVersion,
		})
	}

	return daemons, nil
}
