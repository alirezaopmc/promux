"""POSIX advisory file locking via fcntl.flock."""

from __future__ import annotations

import errno
import fcntl

_CONTENTION_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})


def _acquire_lock(fd: int, non_blocking: bool) -> bool:
    """Acquire an exclusive lock on fd. Returns False if non_blocking and contended."""
    flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if non_blocking else 0)
    try:
        fcntl.flock(fd, flags)
        return True
    except OSError as exc:
        if non_blocking and exc.errno in _CONTENTION_ERRNOS:
            return False
        raise


def _release_lock(fd: int) -> None:
    """Release the lock held on fd."""
    fcntl.flock(fd, fcntl.LOCK_UN)
