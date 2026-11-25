//go:build windows

package hub

import (
	"context"
	"errors"
)

func isProcessAlive(pid int) bool {
	return false
}

func readProcessCmdline(pid int) (string, error) {
	return "", errors.ErrUnsupported
}

// IsDescendantOf always returns false on Windows, where Hub SSH LS restart is unsupported.
func IsDescendantOf(targetPIDs []int) bool {
	return false
}

// RestartDaemons returns errors.ErrUnsupported on Windows unless NoRestart is set.
func RestartDaemons(ctx context.Context, daemonDir string, opts RestartOptions) (*RestartResult, error) {
	if opts.NoRestart {
		return &RestartResult{}, nil
	}
	return &RestartResult{}, errors.ErrUnsupported
}

// RestartPIDs returns errors.ErrUnsupported on Windows unless NoRestart is set.
func RestartPIDs(ctx context.Context, pids []int, daemonDir string, opts RestartOptions) (*RestartResult, error) {
	if opts.NoRestart {
		return &RestartResult{}, nil
	}
	return &RestartResult{}, errors.ErrUnsupported
}
