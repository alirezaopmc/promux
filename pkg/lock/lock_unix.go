//go:build !windows

package lock

import (
	"errors"
	"os"
	"syscall"
)

func openLockFile(path string) (*os.File, error) {
	flags := os.O_CREATE | os.O_RDWR | syscall.O_NOFOLLOW
	return os.OpenFile(path, flags, 0666)
}

func lockFile(f *os.File, nonBlocking bool) (bool, error) {
	flags := syscall.LOCK_EX
	if nonBlocking {
		flags |= syscall.LOCK_NB
	}

	err := syscall.Flock(int(f.Fd()), flags)
	if err == nil {
		return true, nil
	}

	if errors.Is(err, syscall.EWOULDBLOCK) || errors.Is(err, syscall.EAGAIN) {
		return false, nil
	}

	return false, err
}

func unlockFile(f *os.File) error {
	return syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
}
