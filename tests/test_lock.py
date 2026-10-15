import threading
import time
from multiprocessing import Process, Queue
from pathlib import Path

import pytest

from promux.lock import file_lock


def test_lock_acquire_and_release(tmp_path):
    lock_path = tmp_path / "test.lock"
    with file_lock(lock_path, timeout=2.0):
        assert lock_path.exists()
    # Should not raise


def test_lock_creates_parent_dirs(tmp_path):
    lock_path = tmp_path / "nested" / "dir" / "test.lock"
    with file_lock(lock_path, timeout=2.0):
        assert lock_path.exists()


def test_lock_timeout_raises(tmp_path):
    lock_path = tmp_path / "contended.lock"
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock():
        with file_lock(lock_path, timeout=5.0):
            acquired.set()
            release.wait(timeout=5.0)

    t = threading.Thread(target=hold_lock)
    t.start()
    acquired.wait(timeout=3.0)

    with pytest.raises(TimeoutError):
        with file_lock(lock_path, timeout=0.2, poll_interval=0.05):
            pass

    release.set()
    t.join(timeout=3.0)


def test_lock_released_on_exception(tmp_path):
    lock_path = tmp_path / "release.lock"
    try:
        with file_lock(lock_path, timeout=2.0):
            raise ValueError("intentional")
    except ValueError:
        pass
    # Should be able to re-acquire immediately
    with file_lock(lock_path, timeout=2.0):
        pass  # No TimeoutError means lock was released


def test_lock_file_not_unlinked(tmp_path):
    """Lock files must never be deleted on release (inode-splitting race prevention)."""
    lock_path = tmp_path / "stay.lock"
    with file_lock(lock_path, timeout=2.0):
        pass
    assert lock_path.exists(), "Lock file must not be unlinked on release"


def _worker_acquire(lock_path: Path, hold_seconds: float, queue: Queue):
    try:
        with file_lock(lock_path, timeout=2.0):
            queue.put("acquired")
            time.sleep(hold_seconds)
            queue.put("released")
    except Exception as e:
        queue.put(f"error: {e}")


def test_lock_multiprocess_mutual_exclusion(tmp_path):
    lock_file = tmp_path / "multiprocess.lock"
    q1 = Queue()
    q2 = Queue()

    p1 = Process(target=_worker_acquire, args=(lock_file, 0.4, q1))
    p2 = Process(target=_worker_acquire, args=(lock_file, 0.1, q2))

    p1.start()
    time.sleep(0.05)  # ensure p1 gets it first
    p2.start()

    p1.join(timeout=5.0)
    p2.join(timeout=5.0)

    events = [q1.get(timeout=2.0), q1.get(timeout=2.0), q2.get(timeout=2.0), q2.get(timeout=2.0)]
    assert events == ["acquired", "released", "acquired", "released"]
