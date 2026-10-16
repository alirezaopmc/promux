"""Cross-platform advisory file lock. Dispatches to fcntl (POSIX) or LockFileEx (Windows)."""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from .constants import DEFAULT_LOCK_TIMEOUT

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    from ._lock_windows import _acquire_lock, _release_lock
else:
    from ._lock_unix import _acquire_lock, _release_lock


@contextmanager
def file_lock(
    lock_path: str | Path,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
    poll_interval: float = 0.05,
) -> Generator[Path, None, None]:
    """Advisory file lock context manager. Works on POSIX (fcntl) and Windows (LockFileEx).

    IMPORTANT: The lock file is never deleted on release. Deleting it would introduce
    an inode-splitting race condition: two processes could hold the lock simultaneously
    on the same path but different inodes.

    Args:
        lock_path: Path of the file to use as lock holder.
        timeout: Seconds to wait before raising TimeoutError. Default 10s.
        poll_interval: Sleep between non-blocking retry attempts.

    Yields:
        The resolved lock_path as a Path.

    Raises:
        TimeoutError: If lock cannot be acquired within timeout.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    open_flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW  # POSIX: prevent symlink TOCTOU attacks

    fd = os.open(str(path), open_flags, 0o666)
    acquired = False
    try:
        start = time.monotonic()
        while True:
            if _acquire_lock(fd, non_blocking=True):
                acquired = True
                break
            if time.monotonic() - start >= timeout:
                raise TimeoutError(f"Timed out after {timeout}s waiting for lock: {path}")
            time.sleep(poll_interval)

        yield path
    finally:
        if acquired:
            try:
                _release_lock(fd)
            except OSError:
                logger.debug("Failed to release lock on fd %s", fd, exc_info=True)
        os.close(fd)
