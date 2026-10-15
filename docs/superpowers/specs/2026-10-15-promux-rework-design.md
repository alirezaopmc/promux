# Promux OSS Rework Design Spec

**Date:** 2026-10-15  
**Status:** Approved — pending implementation  
**Scope:** Structural, cross-platform, observability, and agentic-friendliness improvements  
**Author:** Antigravity AI (design) + alirezaopmc (review)

---

## 1. Context & Motivation

Promux is a well-functioning tool with 230 passing tests, zero runtime dependencies, and excellent user-facing documentation. However, a deep structural review (see `promux-deep-review.md`) identified a cluster of problems that limit maintainability, cross-platform support, and agentic friendliness:

| Problem | Impact |
|---------|--------|
| `cli.py` is 1,569 lines doing 6 jobs | Hard to modify, hard to test in isolation |
| Circular imports (storage.py, watch.py → cli.py) | Prevents safe decomposition |
| `import fcntl` at module top level in `lock.py` | Hard crash on Windows |
| 30+ silent `except: pass` blocks, zero logging | Invisible failures in production |
| Version string defined in two places | Drift risk on every release |
| 5 dead `TypeError` shims against own API | Misleading code, dead weight |
| Double-print bug in `cmd_remove` and `cmd_switch` | Corrupts JSON output when `--json` is set |
| Import-time side effects in `constants.py` | Slows import, breaks isolated testing |
| No AGENTS.md | AI agents have no project context |

**Design invariants that must be preserved:**

- Zero external runtime dependencies (non-negotiable)
- Existing 230 tests must pass at every phase boundary
- Atomic writes (`tmp` + `os.replace` + `0o600` permissions) stay unchanged
- Adapter pattern and ToolRegistry stay unchanged
- OIDC-based PyPI publishing pipeline stays unchanged

---

## 2. Approach: Surgical Module Extraction (5 Phases)

Work is split into 5 independent, mergeable phases. Each phase leaves `pytest` green and the CLI functional. Phases are ordered by dependency: Phase 1 unblocks Phases 2–4, and Phase 5 is pure housekeeping that can land at any time.

```
Phase 1: auth.py extraction → fixes circular deps
Phase 2: cross-platform lock.py → Windows CI
Phase 3: logging framework → observability
Phase 4: commands/ package → cli.py shrinks to dispatcher
Phase 5: version, lint, AGENTS.md, stale docs
```

---

## 3. Phase 1 — Extract `auth.py` (Eliminate Circular Dependencies)

### 3.1 Problem

Token management lives in `cli.py` but is needed by `storage.py` and `watch.py`, causing deferred local imports:

```python
# storage.py:147 — circular import
from .cli import _get_or_refresh_access_token

# watch.py:158 — circular import
from . import cli
```

### 3.2 Solution: New `src/promux/auth.py`

Extract the following cohesive block of ~350 lines from `cli.py` into a standalone `auth.py`. This module has no circular dependency: it imports only from `storage`, `lock`, `constants`, and stdlib.

**Functions moving to `auth.py`:**

| Function (current name in cli.py) | Public name in auth.py | Lines |
|-----------------------------------|------------------------|-------|
| `_read_token_data()` | `read_token_data()` | 54–63 |
| `_extract_access_token()` | `extract_access_token()` | 66–75 |
| `_extract_expiry()` | `extract_expiry()` | 78–87 |
| `_extract_refresh_token()` | `extract_refresh_token()` | 90–99 |
| `_atomic_copy_token()` | `atomic_copy_token()` | 102–108 |
| `_sync_active_tokens()` | `sync_active_tokens()` | 111–125 |
| `_get_oauth_credentials()` | `get_oauth_credentials()` | 128–142 |
| `_parse_token_expiry_dt()` | `parse_token_expiry_dt()` | 145–156 |
| `_is_token_expired()` | `is_token_expired()` | 159–171 |
| `_refresh_token_native()` | `_refresh_token_native()` (private) | 174–225 |
| `_refresh_token_fallback_agy()` | `_refresh_token_fallback_agy()` (private) | 228–307 |
| `_refresh_token_file()` | `_refresh_token_file()` (private) | 329–358 |
| `_get_or_refresh_access_token()` | `get_or_refresh_access_token()` | 361–377 |
| Thread-local state + getters/setters | (kept private in auth.py) | 310–326 |

**Public API of `auth.py`:**

```python
def read_token_data(token_path: Path) -> dict[str, Any] | None: ...
def extract_access_token(token_data: dict[str, Any] | None) -> str | None: ...
def extract_expiry(token_data: dict[str, Any] | None) -> str | None: ...
def extract_refresh_token(token_data: dict[str, Any] | None) -> str | None: ...
def is_token_expired(token_data: dict[str, Any] | None, buffer_seconds: int = 60) -> bool: ...
def atomic_copy_token(src: Path, dst: Path) -> None: ...
def sync_active_tokens(src: Path, dst: Path) -> None: ...
def get_or_refresh_access_token(
    token_path: Path, storage: StorageEngine | None = None
) -> str | None: ...
```

### 3.3 Dependency Arrow After Phase 1

```
cli.py  ──►  auth.py  ──►  constants.py
                      ──►  lock.py
                      ──►  models.py
                      ──►  storage.py  (TYPE_CHECKING only — no runtime cycle)
storage.py  ──►  auth.py   (no longer imports from cli.py)
watch.py    ──►  auth.py   (no longer imports from cli)
```

**Important:** `auth.py` functions accept `StorageEngine` as a parameter type. To avoid re-introducing a circular import, `StorageEngine` is imported under `TYPE_CHECKING` only:

```python
# auth.py
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .storage import StorageEngine
```

At runtime, `StorageEngine` is only used in function signatures as a type annotation (never instantiated inside `auth.py`), so the `TYPE_CHECKING`-only import is safe. `from __future__ import annotations` makes all annotations lazy strings at runtime, eliminating the `NameError`.

### 3.4 `cli.py` Impact

`cli.py` drops from **1,569 → ~1,200 lines**. All existing `from .cli import _*` references in storage.py and watch.py are updated to `from .auth import *`.

### 3.5 Backward Compatibility

- Private functions that were previously accessed via `from . import cli` in watch.py are accessed via `from . import auth` instead.
- All existing tests that mock `cli._read_token_data` etc. are updated to mock `auth.read_token_data`.

---

## 4. Phase 2 — Cross-Platform `lock.py`

### 4.1 Problem

`lock.py` line 1 is `import fcntl` — this crashes on Windows with `ModuleNotFoundError` at import time, before any code runs.

### 4.2 Solution: Platform-Dispatched Lock Module

Split into three files using the `filelock` architecture pattern, but zero external dependencies (pure stdlib):

```
src/promux/lock.py          # public API — unchanged context manager signature
src/promux/_lock_unix.py    # fcntl.flock implementation (POSIX only)
src/promux/_lock_windows.py # kernel32.LockFileEx via ctypes (Windows only)
```

**Why `LockFileEx` via ctypes, not `msvcrt.locking()`:**

`msvcrt.locking()` locks relative to the current file offset (must `seek(0)` before every call) and retries only 10× with 1-second intervals. `LockFileEx` uses an absolute byte offset via an `OVERLAPPED` struct (no seek-position bugs), supports true non-blocking (`LOCKFILE_FAIL_IMMEDIATELY`), and is how `filelock` (50M downloads/month) works internally.

**Platform dispatch in `lock.py`:**

```python
import sys
if sys.platform == "win32":
    from ._lock_windows import _acquire_lock, _release_lock
else:
    from ._lock_unix import _acquire_lock, _release_lock
```

**`_lock_unix.py`** (`fcntl.flock` — existing logic extracted):

```python
import fcntl, errno
_CONTENTION_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})

def _acquire_lock(fd: int, non_blocking: bool) -> bool:
    flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if non_blocking else 0)
    try:
        fcntl.flock(fd, flags)
        return True
    except OSError as exc:
        if non_blocking and exc.errno in _CONTENTION_ERRNOS:
            return False
        raise

def _release_lock(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
```

**`_lock_windows.py`** (`LockFileEx` via ctypes):

```python
import ctypes
from ctypes import wintypes
import msvcrt as _msvcrt

_LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
_LOCKFILE_EXCLUSIVE_LOCK   = 0x00000002
_ERROR_LOCK_VIOLATION      = 33

class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_LockFileEx   = _kernel32.LockFileEx
_UnlockFileEx = _kernel32.UnlockFileEx
# ... argtypes/restype setup ...

def _acquire_lock(fd: int, non_blocking: bool) -> bool:
    handle = _msvcrt.get_osfhandle(fd)
    flags = _LOCKFILE_EXCLUSIVE_LOCK | (_LOCKFILE_FAIL_IMMEDIATELY if non_blocking else 0)
    overlapped = _OVERLAPPED()  # offset 0 — absolute, no seek() needed
    success = _LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlapped))
    if not success:
        err = ctypes.get_last_error()
        if non_blocking and err == _ERROR_LOCK_VIOLATION:
            return False
        raise ctypes.WinError(err)
    return True

def _release_lock(fd: int) -> None:
    handle = _msvcrt.get_osfhandle(fd)
    overlapped = _OVERLAPPED()
    if not _UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
        raise ctypes.WinError(ctypes.get_last_error())
```

**Critical invariant:** Lock files are **never `os.unlink()`-ed** on release. Unlinking causes an inode-splitting race on POSIX: two processes can both believe they hold the lock on the same path but on different inodes. The existing code correctly avoids this; this invariant is preserved and documented in a comment.

### 4.3 Additional Windows Fixes

- `os.chmod(0o600)` is a no-op on Windows. Wrap it: `if sys.platform != "win32": path.chmod(0o600)`
- Inode identity checks (`st_ino == 0` on NTFS) — guard: `if stat_result.st_ino != 0`

### 4.4 CI Matrix Addition

Add `windows-latest` (and optionally `macos-latest`) to GitHub Actions test matrix. The Windows job runs all 230 existing tests plus new Windows-specific locking tests.

**New test file: `tests/test_lock_windows.py`** — tests:
1. Lock acquire/release round-trip on the current platform
2. Second acquire times out while first is held
3. Context manager releases on exception

### 4.5 `pyproject.toml` Classifiers

Add Windows classifier:
```toml
"Operating System :: Microsoft :: Windows",
```

---

## 5. Phase 3 — Logging Framework

### 5.1 Problem

The entire codebase has zero `import logging`. There are 30+ silent `except Exception: pass` blocks. Failures are invisible to operators and impossible to debug in production.

### 5.2 Solution: stdlib `logging`, per-module loggers, configured at CLI entry point

**Rule 1: Every module gets a module-level logger:**
```python
import logging
logger = logging.getLogger(__name__)
```

**Rule 2: `__init__.py` registers a NullHandler** (library best practice — prevents "No handlers could be found" warnings when used as a library):
```python
# src/promux/__init__.py
import logging
logging.getLogger(__name__).addHandler(logging.NullHandler())
```

**Rule 3: Logging is configured once, at CLI entry point:**

New file `src/promux/logging_config.py`:

```python
def configure_cli_logging(verbose: int = 0, quiet: bool = False) -> None:
    """Configure interactive CLI logging. Call once from main()."""
    if quiet:
        level = logging.ERROR
    elif verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    else:
        level = logging.WARNING

    pkg = logging.getLogger("promux")
    pkg.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)  # never stdout
    fmt = "%(levelname)s [%(name)s:%(lineno)d] %(message)s" if level == logging.DEBUG else "%(levelname)s: %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    pkg.addHandler(handler)

def configure_daemon_logging(log_file: Path | None = None, debug: bool = False) -> None:
    """Configure daemon (watch) mode logging with timestamps and PID."""
    level = logging.DEBUG if debug else logging.INFO
    pkg = logging.getLogger("promux")
    pkg.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [pid:%(process)d] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    if log_file:
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5)
    else:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    pkg.addHandler(handler)
```

**Rule 4: `sys.stdout` is reserved for data output only.** Logs go to `sys.stderr`. This preserves `promux list --json | jq .` pipes.

### 5.3 New CLI Flags

Added to the common argument parser:
- `-v` / `--verbose` — `count=True`, sets INFO (once) or DEBUG (twice)
- `-q` / `--quiet` — suppress all but ERROR

### 5.4 Silent Swallow Triage

Every `except Exception: pass` is classified and updated:

| Category | Count | Action |
|----------|-------|--------|
| Token I/O failures (expected in normal flow) | 8 | `logger.debug("...", exc_info=True)` |
| HTTP/network errors (operator should know) | 6 | `logger.warning("...", exc_info=True)` |
| Config read failures (fallback to defaults) | 4 | `logger.debug(...)` |
| Subprocess failures (`agy` not found, etc.) | 3 | `logger.info(...)` |
| Dead `TypeError` shims against own API | **5** | **Delete entirely** |
| Genuine swallowed errors (re-evaluate) | 4 | `logger.error(...)` |

The 5 `TypeError` shims (cli.py lines 485, 690, 754, 798, 874) guard against `_fetch_account_quota()` returning a 3-tuple vs 4-tuple. After Phase 4 standardizes the return type to always 4-tuple, these are simply deleted.

### 5.5 `cmd_watch` Daemon Integration

`cmd_watch` calls `configure_daemon_logging()` instead of `configure_cli_logging()` to get timestamped, PID-tagged log output appropriate for a long-running process.

---

## 6. Phase 4 — Extract `commands/` Package

### 6.1 Problem

After Phase 1 removes ~350 lines of auth functions, `cli.py` is still ~1,200 lines because it contains all command handler implementations. This is still a God module.

### 6.2 Solution: `src/promux/commands/` Package

```
src/promux/commands/
├── __init__.py       # re-exports all cmd_* for backward compat
├── profile.py        # cmd_list, cmd_save, cmd_switch, cmd_remove, cmd_whoami
├── quota.py          # cmd_quota, cmd_refresh, _fetch_account_quota, _refresh_account_token
├── watch.py          # cmd_watch
└── tools.py          # cmd_tools, cmd_completion
```

**`cli.py` after Phase 4: ~250 lines** — contains only:
- `setup_logging()` call (or imports from `logging_config`)
- `build_parser()` — argument parser definition
- `main()` — dispatch to `commands.*`

### 6.3 Bug Fixes Bundled in Phase 4

**Double-print bug in `cmd_remove` (cli.py L644–646):**

```python
# BEFORE (always prints stderr even when json_out=True):
if json_out:
    print(json.dumps({...}))
print(f"Error: Account '{name}' not found", file=sys.stderr)

# AFTER:
if json_out:
    print(json.dumps({...}))
else:
    print(f"Error: Account '{name}' not found", file=sys.stderr)
```

Same fix applies to `cmd_switch` (L567–568) and `cmd_quota` (L808–810).

**`_fetch_account_quota` return type standardization:**

The function currently sometimes returns a 3-tuple and sometimes a 4-tuple (`is_cached`). All callers have defensive `len(res) == 4` guards. After standardization:

```python
# Always returns 4-tuple:
def _fetch_account_quota(...) -> tuple[QuotaSummary | None, str | None, str | None, bool]:
```

All 5 `TypeError` shims and `len(res)` guards are deleted.

**`_format_expiry_table()` moved to `formatters.py`** — it belongs there alongside other formatting helpers.

### 6.4 Module Responsibility Table (Final State)

| Module | Responsibility | Max LOC |
|--------|---------------|---------|
| `cli.py` | Argument parser + main() dispatcher | ~250 |
| `auth.py` | Token I/O, expiry, refresh, sync | ~350 |
| `commands/profile.py` | list, save, switch, remove, whoami | ~300 |
| `commands/quota.py` | quota, refresh, fetch internals | ~350 |
| `commands/watch.py` | watch daemon wrapper | ~40 |
| `commands/tools.py` | tools, completion | ~50 |
| `lock.py` | Platform-dispatched file lock API | ~60 |
| `_lock_unix.py` | POSIX fcntl implementation | ~25 |
| `_lock_windows.py` | Windows LockFileEx implementation | ~60 |
| `logging_config.py` | CLI and daemon logging setup | ~60 |
| `storage.py` | Profile vault I/O | unchanged |
| `watch.py` | Log watcher engine | unchanged |
| `failover.py` | Rotation engine | unchanged |
| `quota.py` | Quota HTTP client | unchanged |
| `cache.py` | Quota cache | unchanged |
| `formatters.py` | Output formatting helpers | +_format_expiry_table |
| `constants.py` | Constants (lazy Path.home) | fixed |
| `models.py` | Data models | unchanged |
| `adapters/` | Tool adapters (ToolRegistry) | unchanged |

---

## 7. Phase 5 — Housekeeping & Agentic Friendliness

### 7.1 Single-Source Version

**Current problem:** Version string `"0.4.0"` is hardcoded in both `__init__.py:3` and `pyproject.toml:7`.

**Solution:** `setuptools-scm` (build-time only, not a runtime dep) + `importlib.metadata` lazily at runtime:

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=68", "setuptools-scm>=8.0", "wheel"]

[project]
dynamic = ["version"]

[tool.setuptools-scm]
version_file = "src/promux/_version.py"
```

```python
# src/promux/__init__.py
import logging
logging.getLogger(__name__).addHandler(logging.NullHandler())

try:
    from ._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"
```

`_version.py` is auto-generated at build time from Git tags and listed in `.gitignore`.

**Lazy `--version` in CLI** — `importlib.metadata.version()` scans disk (5–20ms overhead). Only evaluate it when `--version` is actually invoked:

```python
# version.py
def get_version() -> str:
    """Resolve installed package version lazily."""
    try:
        from ._version import __version__
        return __version__
    except ImportError:
        pass
    from importlib.metadata import version, PackageNotFoundError
    try:
        return version("promux")
    except PackageNotFoundError:
        return "0.0.0+unknown"
```

### 7.2 `constants.py` Import-Time Side Effects

`constants.py` currently evaluates `Path.home()` at import time (line 7) and calls `get_default_http_timeout()` at module level (line 102), which reads `config.json` from disk.

Fix: Use Python's module-level `__getattr__` (PEP 562, stdlib since 3.7) to defer evaluation:

```python
# constants.py — lazy PROMUX_HOME via module __getattr__
import os
from pathlib import Path
from typing import Any

def __getattr__(name: str) -> Any:
    if name == "PROMUX_HOME":
        val = Path(os.environ.get("PROMUX_HOME", Path.home() / ".promux"))
        # Cache by injecting into module namespace so __getattr__ isn't called again
        globals()["PROMUX_HOME"] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

`DEFAULT_HTTP_TIMEOUT` (which calls `get_default_http_timeout()` at module level, reading disk) is moved to the same lazy pattern. All existing consumers use `from .constants import PROMUX_HOME` — the module `__getattr__` fires on first access and caches the result in the module globals, so subsequent accesses are a direct dict lookup with zero overhead.

### 7.3 Lint Cleanup

Fix all 80 ruff violations (23 in `src/`, 57 in `tests/`):
- E501: line length — reformat long lines
- I001: unsorted imports — `ruff check --fix` handles this automatically
- F401: unused imports — remove or add `__all__` entries

Add ruff to pre-commit hooks (already present in `.pre-commit-config.yaml`; ensure it's wired).

### 7.4 `AGENTS.md`

Create `AGENTS.md` at repository root. This file is the universal standard adopted by Claude Code, GitHub Copilot, Cursor, Gemini CLI, and OpenAI Codex. Keep it under 200 lines — document only tacit knowledge that cannot be inferred from reading code.

```markdown
# AGENTS.md

## Project Overview
Promux is a **zero-runtime-dependency** Python CLI tool for managing multiple
Antigravity CLI (agy) OAuth profiles and automatic quota-aware failover.

- Python: 3.10+
- Build: `setuptools` + `setuptools-scm` (version from git tags)
- Package manager: `pip` or `uv` (both supported)

## Development Commands
| Task | Command |
|------|---------|
| Install (dev) | `pip install -e ".[dev]"` |
| Run all tests | `pytest -v` |
| Run single test | `pytest tests/test_auth.py -k test_token_expired` |
| Lint | `ruff check src tests` |
| Auto-fix lint | `ruff check --fix src tests` |
| Type check | `mypy src` |

## Architecture — Module Responsibilities
- `cli.py` — Argument parser + `main()` dispatch. **Nothing else.** ~250 LOC.
- `auth.py` — Token read/write/refresh/sync. No UI, no argparse. ~350 LOC.
- `commands/` — One module per command group (profile, quota, watch, tools).
- `lock.py` — Cross-platform advisory file lock (POSIX + Windows). Public: `file_lock()`.
- `storage.py` — Profile vault I/O. Always uses `lock.py` for concurrent access.
- `watch.py` — Log-tailing daemon engine. Does not depend on `cli.py`.
- `failover.py` — Rotation engine (next / smart). Pure business logic.
- `constants.py` — All configuration defaults and path constants.
- `adapters/` — ToolRegistry + per-tool adapters. Do not modify unless adding a new tool.

## Constraints — Never Do
- **Never import from `cli.py` in non-CLI modules.** This was the source of circular
  deps before Phase 1. Token logic lives in `auth.py`.
- **Never swallow exceptions silently.** Use `logger.debug("...", exc_info=True)` at minimum.
- **Never add runtime dependencies.** Zero-dep is a core design invariant.
- **Never write to `sys.stdout` from logging.** Stdout is for data; stderr is for logs.
- **Never hardcode version strings.** Version comes from git tags via `setuptools-scm`.

## Definition of Done
Before marking any task complete:
1. `pytest` passes (all 230+ tests)
2. `ruff check src tests` → 0 errors
3. `mypy src` → 0 errors
4. New code has corresponding tests in `tests/`
5. No new `except Exception: pass` blocks — use `logger.debug` at minimum
```

### 7.5 Stale Documentation Fixes

| File | Issue | Fix |
|------|-------|-----|
| `SECURITY.md` | Lists only `0.1.x` as supported version | Update to current release line |
| `CONTRIBUTING.md` | Mentions `pip` only, no `uv` | Add `uv` instructions alongside pip |
| `pyproject.toml` | Missing Windows classifier | Add `"Operating System :: Microsoft :: Windows"` |
| `README.md` | Scaffolded adapters (claude, codex, cursor) listed without TODO marker | Add note that these are planned/stub adapters |

---

## 8. Final Module Tree

```
src/promux/
├── __init__.py              # NullHandler + __version__ from _version.py
├── _version.py              # Auto-generated by setuptools-scm (gitignored)
├── auth.py                  # NEW — token I/O, expiry, refresh, sync
├── cli.py                   # ~250 lines — parser + dispatch only
├── commands/
│   ├── __init__.py          # re-exports cmd_* for backward compat
│   ├── profile.py           # cmd_list, cmd_save, cmd_switch, cmd_remove, cmd_whoami
│   ├── quota.py             # cmd_quota, cmd_refresh, _fetch_account_quota
│   ├── watch.py             # cmd_watch
│   └── tools.py             # cmd_tools, cmd_completion
├── lock.py                  # Platform dispatch + file_lock() context manager
├── _lock_unix.py            # fcntl.flock (POSIX)
├── _lock_windows.py         # kernel32.LockFileEx via ctypes (Windows)
├── logging_config.py        # NEW — configure_cli_logging(), configure_daemon_logging()
├── storage.py               # Imports from auth.py (not cli.py)
├── watch.py                 # Imports from auth.py (not cli)
├── failover.py              # Unchanged
├── quota.py                 # Unchanged
├── cache.py                 # Unchanged
├── formatters.py            # + _format_expiry_table (moved from cli.py)
├── models.py                # Unchanged
├── constants.py             # Fixed: lazy Path.home()
├── version.py               # Fixed: lazy importlib.metadata lookup
├── help.py                  # Unchanged
├── completion.py            # Unchanged
├── colors.py                # Unchanged
└── adapters/                # Unchanged
    ├── __init__.py
    ├── base.py
    ├── registry.py
    ├── stubs.py
    └── agy.py
```

---

## 9. Test Strategy

**Invariant:** All 230 existing tests must pass at the end of every phase.

**New tests per phase:**

| Phase | New Test File(s) | Coverage Target |
|-------|-----------------|-----------------|
| 1 | `tests/test_auth.py` | All public auth functions |
| 2 | `tests/test_lock.py` (cross-platform) | Acquire, release, timeout, concurrency |
| 3 | `tests/test_logging_config.py` | Verbosity levels, stderr-only |
| 4 | Update existing `test_cli.py` to `tests/commands/test_*.py` | Command handlers in isolation |
| 5 | (Existing tests sufficient for housekeeping) | — |

**Coverage floor:** After Phase 5, run `pytest --cov=promux --cov-report=term-missing` and document the baseline. Consider enforcing `--cov-fail-under=80` in CI.

---

## 10. Phase Rollout Order & Merge Strategy

```
main
 ├─ phase/1-auth-extraction     ──► merge → main
 ├─ phase/2-crossplatform-lock  ──► merge → main
 ├─ phase/3-logging             ──► merge → main
 ├─ phase/4-commands-package    ──► merge → main
 └─ phase/5-housekeeping        ──► merge → main
```

Each branch is independent. Phases 2, 3, and 5 can begin in parallel once Phase 1 is merged (Phase 4 depends on Phase 1 and Phase 3 for full benefit, but can proceed independently if needed).

---

## 11. Out of Scope

The following were explicitly discussed and excluded from this rework:

- **Replacing argparse with Click/Typer** — would require test rewrites; deferred
- **Removing scaffolded adapter stubs** (claude, codex, cursor) — kept as stubs; documented as TODO in README
- **Structured logging (`structlog`)** — adds a runtime dependency; stdlib logging is sufficient
- **OAuth credential rotation** — the question of whether `DEFAULT_OAUTH_CLIENT_ID` in `constants.py` is intentionally public is out of scope; no change
- **Breaking API changes** — all existing CLI commands and flags stay identical
