//go:build linux

package hub

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
)

func readProcessCmdline(pid int) (string, error) {
	if pid <= 0 {
		return "", fmt.Errorf("invalid pid: %d", pid)
	}
	data, err := os.ReadFile(fmt.Sprintf("/proc/%d/cmdline", pid))
	if err != nil {
		return "", fmt.Errorf("failed to read /proc/%d/cmdline: %w", pid, err)
	}
	return strings.TrimSpace(strings.ReplaceAll(string(data), "\x00", " ")), nil
}

func parseProcStat(pid int) (byte, int, error) {
	if pid <= 0 {
		return 0, 0, fmt.Errorf("invalid pid: %d", pid)
	}
	data, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return 0, 0, fmt.Errorf("failed to read /proc/%d/stat: %w", pid, err)
	}

	raw := string(data)
	// Skip (comm), which may contain spaces or parentheses, by anchoring on the last ')'.
	idx := strings.LastIndexByte(raw, ')')
	if idx < 0 || idx+1 >= len(raw) {
		return 0, 0, fmt.Errorf("malformed /proc/%d/stat", pid)
	}

	fields := strings.Fields(raw[idx+1:])
	if len(fields) < 2 || len(fields[0]) == 0 {
		return 0, 0, fmt.Errorf("unexpected /proc/%d/stat field count", pid)
	}

	ppid, err := strconv.Atoi(fields[1])
	if err != nil {
		return 0, 0, fmt.Errorf("invalid ppid in /proc/%d/stat: %w", pid, err)
	}

	return fields[0][0], ppid, nil
}

func readParentPID(pid int) (int, error) {
	_, ppid, err := parseProcStat(pid)
	return ppid, err
}

func isProcessDeadOrZombie(pid int) bool {
	state, _, err := parseProcStat(pid)
	if err != nil {
		return errors.Is(err, os.ErrNotExist)
	}
	return state == 'Z' || state == 'X'
}
