import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Union
from .constants import DEFAULT_LOCK_TIMEOUT


@contextmanager
def file_lock(
    lock_path: Union[str, Path],
    timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> Generator[Path, None, None]:
    """Advisory POSIX flock context manager with polling timeout."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
    start_time = time.monotonic()
    acquired = False

    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() - start_time >= timeout:
                    raise TimeoutError(f"Timed out after {timeout}s waiting for lock: {lock_path}")
                time.sleep(0.05)

        yield lock_path
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)
