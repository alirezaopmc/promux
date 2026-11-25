//go:build darwin

package hub

import (
	"errors"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
)

func runPSColumn(pid int, column string) (string, error) {
	if pid <= 0 {
		return "", fmt.Errorf("invalid pid: %d", pid)
	}
	out, err := exec.Command("ps", "-p", strconv.Itoa(pid), "-o", column+"=").Output()
	if err != nil {
		return "", fmt.Errorf("ps -o %s for pid %d: %w", column, pid, err)
	}
	return strings.TrimSpace(string(out)), nil
}

func readProcessCmdline(pid int) (string, error) {
	return runPSColumn(pid, "args")
}

func readParentPID(pid int) (int, error) {
	out, err := runPSColumn(pid, "ppid")
	if err != nil {
		return 0, err
	}
	ppid, err := strconv.Atoi(out)
	if err != nil {
		return 0, fmt.Errorf("invalid ppid %q for pid %d: %w", out, pid, err)
	}
	return ppid, nil
}

func isProcessDeadOrZombie(pid int) bool {
	out, err := runPSColumn(pid, "state")
	if err != nil {
		var exitErr *exec.ExitError
		return errors.As(err, &exitErr)
	}
	return strings.HasPrefix(out, "Z")
}
