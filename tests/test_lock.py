import time
from multiprocessing import Process, Queue
from pathlib import Path

import pytest

from promux.lock import file_lock


def _worker_acquire(lock_path: Path, hold_seconds: float, queue: Queue):
    try:
        with file_lock(lock_path, timeout=2.0):
            queue.put("acquired")
            time.sleep(hold_seconds)
            queue.put("released")
    except Exception as e:
        queue.put(f"error: {e}")


def test_file_lock_mutual_exclusion(tmp_path):
    lock_file = tmp_path / "test.lock"
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


def test_file_lock_timeout(tmp_path):
    lock_file = tmp_path / "timeout.lock"
    with file_lock(lock_file, timeout=1.0):
        with pytest.raises(TimeoutError):
            with file_lock(lock_file, timeout=0.2):
                pass


def test_file_lock_creates_parent_directories(tmp_path):
    lock_file = tmp_path / "nested" / "deep" / "test.lock"
    assert not lock_file.parent.exists()
    with file_lock(lock_file, timeout=1.0) as acquired_path:
        assert lock_file.parent.exists()
        assert acquired_path == lock_file
        assert lock_file.exists()


def test_file_lock_releases_on_exception(tmp_path):
    lock_file = tmp_path / "error.lock"
    with pytest.raises(RuntimeError):
        with file_lock(lock_file, timeout=1.0):
            raise RuntimeError("something broke")

    # Should be immediately re-acquirable
    with file_lock(lock_file, timeout=0.2) as path:
        assert path == lock_file
