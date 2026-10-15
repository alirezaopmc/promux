"""Windows file locking via kernel32.LockFileEx (ctypes, no external deps)."""

from __future__ import annotations

import ctypes
import msvcrt
import sys
from ctypes import wintypes
from typing import Any

_LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
_LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
_ERROR_LOCK_VIOLATION = 33


class _OVERLAPPED(ctypes.Structure):
    """Windows OVERLAPPED structure for async I/O (used for lock byte offset)."""

    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


_LockFileEx: Any = None
_UnlockFileEx: Any = None

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    _LockFileEx = _kernel32.LockFileEx
    _LockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    ]
    _LockFileEx.restype = wintypes.BOOL

    _UnlockFileEx = _kernel32.UnlockFileEx
    _UnlockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_OVERLAPPED),
    ]
    _UnlockFileEx.restype = wintypes.BOOL


def _acquire_lock(fd: int, non_blocking: bool) -> bool:
    """Acquire an exclusive lock on fd using LockFileEx.

    Uses OVERLAPPED with Offset=0 so lock position is absolute (no seek() needed).
    Returns False if non_blocking and the lock is contended.
    """
    if sys.platform != "win32" or _LockFileEx is None:
        raise OSError("LockFileEx is only supported on Windows")

    handle = msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined]
    flags = _LOCKFILE_EXCLUSIVE_LOCK
    if non_blocking:
        flags |= _LOCKFILE_FAIL_IMMEDIATELY
    overlapped = _OVERLAPPED()  # Offset=0, OffsetHigh=0 → lock byte 0
    success = _LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlapped))
    if not success:
        err = ctypes.get_last_error()  # type: ignore[attr-defined]
        if non_blocking and err == _ERROR_LOCK_VIOLATION:
            return False
        raise ctypes.WinError(err)  # type: ignore[attr-defined]
    return True


def _release_lock(fd: int) -> None:
    """Release the lock held on fd."""
    if sys.platform != "win32" or _UnlockFileEx is None:
        raise OSError("UnlockFileEx is only supported on Windows")

    handle = msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined]
    overlapped = _OVERLAPPED()
    if not _UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
