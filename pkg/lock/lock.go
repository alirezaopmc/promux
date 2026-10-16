package lock

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

var (
	ErrTimeout      = errors.New("timed out waiting for lock")
	ErrAlreadyLocked = errors.New("lock already held")
	ErrNotLocked     = errors.New("lock not held")

	processLocks   = make(map[string]*sync.Mutex)
	processLocksMu sync.Mutex
)

func getProcessMutex(path string) *sync.Mutex {
	processLocksMu.Lock()
	defer processLocksMu.Unlock()
	abs, err := filepath.Abs(path)
	if err != nil {
		abs = path
	}
	m, ok := processLocks[abs]
	if !ok {
		m = &sync.Mutex{}
		processLocks[abs] = m
	}
	return m
}

type FileLock struct {
	path       string
	file       *os.File
	isLocked   bool
	mu         sync.Mutex
	procMu     *sync.Mutex
	procLocked bool
}

func New(path string) *FileLock {
	return &FileLock{
		path:   path,
		procMu: getProcessMutex(path),
	}
}

// Lock acquires the lock, blocking until acquired or timeout.
func (fl *FileLock) Lock(timeout time.Duration) error {
	fl.mu.Lock()
	defer fl.mu.Unlock()

	if fl.isLocked {
		return ErrAlreadyLocked
	}

	start := time.Now()
	pollInterval := 10 * time.Millisecond

	// Acquire in-process mutex first
	for {
		if fl.procMu.TryLock() {
			fl.procLocked = true
			break
		}
		if timeout > 0 && time.Since(start) >= timeout {
			return fmt.Errorf("%w after %v waiting for process lock on %s", ErrTimeout, timeout, fl.path)
		}
		time.Sleep(pollInterval)
	}

	if err := os.MkdirAll(filepath.Dir(fl.path), 0755); err != nil {
		fl.procMu.Unlock()
		fl.procLocked = false
		return fmt.Errorf("failed to create lock directory: %w", err)
	}

	f, err := openLockFile(fl.path)
	if err != nil {
		fl.procMu.Unlock()
		fl.procLocked = false
		return fmt.Errorf("failed to open lock file: %w", err)
	}

	for {
		ok, err := lockFile(f, true)
		if err != nil {
			_ = f.Close()
			fl.procMu.Unlock()
			fl.procLocked = false
			return fmt.Errorf("failed to acquire lock: %w", err)
		}
		if ok {
			fl.file = f
			fl.isLocked = true
			return nil
		}

		if timeout > 0 && time.Since(start) >= timeout {
			_ = f.Close()
			fl.procMu.Unlock()
			fl.procLocked = false
			return fmt.Errorf("%w after %v on %s", ErrTimeout, timeout, fl.path)
		}

		time.Sleep(pollInterval)
	}
}

// TryLock attempts to acquire the lock immediately without blocking.
func (fl *FileLock) TryLock() (bool, error) {
	fl.mu.Lock()
	defer fl.mu.Unlock()

	if fl.isLocked {
		return false, ErrAlreadyLocked
	}

	if !fl.procMu.TryLock() {
		return false, nil
	}
	fl.procLocked = true

	if err := os.MkdirAll(filepath.Dir(fl.path), 0755); err != nil {
		fl.procMu.Unlock()
		fl.procLocked = false
		return false, fmt.Errorf("failed to create lock directory: %w", err)
	}

	f, err := openLockFile(fl.path)
	if err != nil {
		fl.procMu.Unlock()
		fl.procLocked = false
		return false, fmt.Errorf("failed to open lock file: %w", err)
	}

	ok, err := lockFile(f, true)
	if err != nil {
		_ = f.Close()
		fl.procMu.Unlock()
		fl.procLocked = false
		return false, fmt.Errorf("failed to acquire lock: %w", err)
	}

	if ok {
		fl.file = f
		fl.isLocked = true
		return true, nil
	}

	_ = f.Close()
	fl.procMu.Unlock()
	fl.procLocked = false
	return false, nil
}

// Unlock releases the lock.
func (fl *FileLock) Unlock() error {
	fl.mu.Lock()
	defer fl.mu.Unlock()

	if !fl.isLocked || fl.file == nil {
		return ErrNotLocked
	}

	err := unlockFile(fl.file)
	closeErr := fl.file.Close()
	fl.file = nil
	fl.isLocked = false

	if fl.procLocked {
		fl.procMu.Unlock()
		fl.procLocked = false
	}

	if err != nil {
		return fmt.Errorf("failed to release lock: %w", err)
	}
	return closeErr
}

// WithLock is a helper that runs fn within an acquired lock.
func WithLock(ctx context.Context, path string, timeout time.Duration, fn func() error) error {
	fl := New(path)
	if err := fl.Lock(timeout); err != nil {
		return err
	}
	defer func() {
		_ = fl.Unlock()
	}()
	return fn()
}
