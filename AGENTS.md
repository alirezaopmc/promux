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
