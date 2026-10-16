package lock

import (
	"context"
	"errors"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestFileLockBasic(t *testing.T) {
	tmpDir := t.TempDir()
	lockPath := filepath.Join(tmpDir, "test.lock")

	fl := New(lockPath)
	if err := fl.Lock(1 * time.Second); err != nil {
		t.Fatalf("failed to lock: %v", err)
	}

	if err := fl.Unlock(); err != nil {
		t.Fatalf("failed to unlock: %v", err)
	}
}

func TestFileLockMutualExclusion(t *testing.T) {
	tmpDir := t.TempDir()
	lockPath := filepath.Join(tmpDir, "test.lock")

	fl1 := New(lockPath)
	if err := fl1.Lock(1 * time.Second); err != nil {
		t.Fatalf("fl1 failed to lock: %v", err)
	}
	defer fl1.Unlock()

	fl2 := New(lockPath)
	ok, err := fl2.TryLock()
	if err != nil {
		t.Fatalf("fl2 TryLock error: %v", err)
	}
	if ok {
		t.Fatalf("fl2 acquired lock while fl1 was holding it")
	}

	// fl2 Lock should timeout
	err = fl2.Lock(100 * time.Millisecond)
	if !errors.Is(err, ErrTimeout) {
		t.Fatalf("expected ErrTimeout, got %v", err)
	}
}

func TestWithLockHelper(t *testing.T) {
	tmpDir := t.TempDir()
	lockPath := filepath.Join(tmpDir, "test.lock")

	executed := false
	err := WithLock(context.Background(), lockPath, 1*time.Second, func() error {
		executed = true
		return nil
	})
	if err != nil {
		t.Fatalf("WithLock failed: %v", err)
	}
	if !executed {
		t.Fatalf("WithLock body did not execute")
	}
}

func TestFileLockConcurrentStress(t *testing.T) {
	tmpDir := t.TempDir()
	lockPath := filepath.Join(tmpDir, "stress.lock")

	counter := 0
	var wg sync.WaitGroup
	workers := 10
	iterations := 20

	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < iterations; j++ {
				fl := New(lockPath)
				if err := fl.Lock(5 * time.Second); err != nil {
					t.Errorf("worker failed to lock: %v", err)
					return
				}
				// Critical section
				curr := counter
				time.Sleep(1 * time.Millisecond)
				counter = curr + 1
				_ = fl.Unlock()
			}
		}()
	}

	wg.Wait()
	expected := workers * iterations
	if counter != expected {
		t.Fatalf("expected counter %d, got %d (race condition detected!)", expected, counter)
	}
}
