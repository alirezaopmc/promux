# Multi-Tool Generalization & Quota Reset Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform `promux` into an extensible, multi-tool CLI manager supporting `agy`, `claude`, `codex`, and `cursor` with tool-first routing (`promux <tool> <cmd>`) and backward-compatible default fallback, while redesigning `promux quota` to display accurate reset countdowns (`98.0% (2h 15m)`) for both 5h and weekly windows.

**Architecture:** Introduce an extensible `BaseToolAdapter` interface and `ToolRegistry` in `src/promux/adapters/`, isolating tool-specific storage, auth paths, quota retrieval, and log watching. Implement concrete `AgyAdapter` with full feature parity and scaffold `ClaudeAdapter`, `CodexAdapter`, and `CursorAdapter`. Introduce a dedicated `src/promux/formatters.py` module for robust relative timedelta parsing and cell rendering, replacing the legacy truncated reset column with per-cell reset times.

**Tech Stack:** Python 3.10+ (Standard Library: `datetime`, `pathlib`, `json`, `argparse`, `abc`, `re`, `urllib`, `shutil`), `pytest`.

## Global Constraints
- Zero external third-party runtime dependencies beyond Python standard library.
- Strict 100% backward compatibility: existing commands without tool prefix (`promux quota`, `promux switch <name>`, `promux list`) must transparently execute against the default `agy` tool.
- Atomic file operations and POSIX file locking (`manager.lock`) scoped per tool.
- Test coverage must remain at 100% passing across all new and existing tests.

---

### Task 1: Quota Reset Formatting Module (`src/promux/formatters.py`)

**Files:**
- Create: `src/promux/formatters.py`
- Test: `tests/test_formatters.py`

**Interfaces:**
- Produces:
  - `parse_iso_utc(iso_str: str | None) -> datetime | None`
  - `format_relative_countdown(reset_iso: str | None, now: datetime | None = None) -> str`
  - `format_quota_cell(val: float | None, reset_iso: str | None, now: datetime | None = None) -> str`
  - `format_quota_detail(val: float | None, reset_iso: str | None, now: datetime | None = None) -> str`

- [ ] **Step 1: Write unit tests for formatters**
Create `tests/test_formatters.py`:
```python
from datetime import datetime, timezone, timedelta
from promux.formatters import (
    parse_iso_utc,
    format_relative_countdown,
    format_quota_cell,
    format_quota_detail,
)


def test_parse_iso_utc():
    assert parse_iso_utc(None) is None
    assert parse_iso_utc("") is None
    assert parse_iso_utc("invalid-date") is None

    dt = parse_iso_utc("2026-09-09T03:15:00Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2026
    assert dt.hour == 3
    assert dt.minute == 15

    dt2 = parse_iso_utc("2026-09-09T03:15:00+00:00")
    assert dt2 is not None
    assert dt2 == dt


def test_format_relative_countdown():
    base_now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)

    # Missing or invalid
    assert format_relative_countdown(None, now=base_now) == "-"
    assert format_relative_countdown("", now=base_now) == "-"

    # Expired / now
    past = (base_now - timedelta(minutes=5)).isoformat()
    assert format_relative_countdown(past, now=base_now) == "0m"

    # Minutes only
    t_15m = (base_now + timedelta(minutes=15)).isoformat()
    assert format_relative_countdown(t_15m, now=base_now) == "15m"

    # Hours and minutes
    t_2h_15m = (base_now + timedelta(hours=2, minutes=15)).isoformat()
    assert format_relative_countdown(t_2h_15m, now=base_now) == "2h 15m"

    # Days and hours
    t_3d_4h = (base_now + timedelta(days=3, hours=4, minutes=20)).isoformat()
    assert format_relative_countdown(t_3d_4h, now=base_now) == "3d 4h"


def test_format_quota_cell():
    base_now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    t_2h_15m = (base_now + timedelta(hours=2, minutes=15)).isoformat()

    assert format_quota_cell(None, None, now=base_now) == "-"
    assert format_quota_cell(1.0, None, now=base_now) == "100.0% (-)"
    assert format_quota_cell(0.98, t_2h_15m, now=base_now) == "98.0% (2h 15m)"


def test_format_quota_detail():
    base_now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    t_2h_15m = "2026-09-09T14:15:00Z"
    t_3d_4h = "2026-09-12T16:00:00Z"

    assert format_quota_detail(None, None, now=base_now) == "-"
    assert format_quota_detail(1.0, None, now=base_now) == "100.0% (-)"
    assert (
        format_quota_detail(0.98, t_2h_15m, now=base_now)
        == "98.0% (2h 15m left - 14:15 UTC)"
    )
    assert (
        format_quota_detail(0.85, t_3d_4h, now=base_now)
        == "85.0% (3d 4h left - Sep 12 16:00 UTC)"
    )
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_formatters.py -v`
Expected: FAIL with ModuleNotFoundError: No module named 'promux.formatters'

- [ ] **Step 3: Implement `src/promux/formatters.py`**
Create `src/promux/formatters.py`:
```python
from datetime import datetime, timezone, timedelta


def parse_iso_utc(iso_str: str | None) -> datetime | None:
    """Parse ISO8601 string into a timezone-aware UTC datetime."""
    if not iso_str or not isinstance(iso_str, str):
        return None
    try:
        clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def format_relative_countdown(
    reset_iso: str | None, now: datetime | None = None
) -> str:
    """Calculate compact relative countdown (e.g. '2h 15m', '3d 4h', '18m', or '-')."""
    reset_dt = parse_iso_utc(reset_iso)
    if reset_dt is None:
        return "-"

    current = now or datetime.now(timezone.utc)
    delta = reset_dt - current

    if delta <= timedelta(0):
        return "0m"

    days = delta.days
    total_seconds = delta.seconds
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_quota_cell(
    val: float | None, reset_iso: str | None, now: datetime | None = None
) -> str:
    """Format overview table cell: e.g. '98.0% (2h 15m)', '85.0% (3d 4h)', '100.0% (-)'."""
    if val is None:
        return "-"
    pct = f"{val * 100:.1f}%"
    countdown = format_relative_countdown(reset_iso, now=now)
    if countdown == "-":
        return f"{pct} (-)"
    return f"{pct} ({countdown})"


def format_quota_detail(
    val: float | None, reset_iso: str | None, now: datetime | None = None
) -> str:
    """Format detail mode: e.g. '98.0% (2h 15m left - 03:15 UTC)'."""
    if val is None:
        return "-"
    pct = f"{val * 100:.1f}%"
    reset_dt = parse_iso_utc(reset_iso)
    if reset_dt is None:
        return f"{pct} (-)"

    countdown = format_relative_countdown(reset_iso, now=now)
    current = now or datetime.now(timezone.utc)

    # Format absolute timestamp
    if reset_dt.date() == current.date():
        abs_str = reset_dt.strftime("%H:%M UTC")
    else:
        abs_str = reset_dt.strftime("%b %d %H:%M UTC")

    return f"{pct} ({countdown} left - {abs_str})"
```

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_formatters.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/formatters.py tests/test_formatters.py
git commit -m "feat(formatters): add quota reset countdown and cell formatting utilities"
```

---

### Task 2: Base Tool Adapter & Registry Framework (`src/promux/adapters/`)

**Files:**
- Create: `src/promux/adapters/__init__.py`
- Create: `src/promux/adapters/base.py`
- Create: `src/promux/adapters/registry.py`
- Create: `src/promux/adapters/stubs.py`
- Test: `tests/test_adapters.py`

**Interfaces:**
- Produces:
  - `BaseToolAdapter`: abstract base class
  - `ToolRegistry`: registry holding tool adapters
  - `ClaudeAdapter`, `CodexAdapter`, `CursorAdapter`: scaffolded tool adapters

- [ ] **Step 1: Write unit tests for adapters and registry**
Create `tests/test_adapters.py`:
```python
import pytest
from pathlib import Path
from promux.adapters.base import BaseToolAdapter
from promux.adapters.registry import ToolRegistry
from promux.adapters.stubs import ClaudeAdapter, CodexAdapter, CursorAdapter
from promux.storage import StorageEngine


class DummyAdapter(BaseToolAdapter):
    name = "dummy"
    display_name = "Dummy Tool"

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        home = promux_home or Path("/tmp/dummy")
        return StorageEngine(promux_home=home / "dummy")


def test_tool_registry():
    reg = ToolRegistry()
    dummy = DummyAdapter()
    reg.register(dummy)

    assert reg.has_tool("dummy")
    assert reg.get("dummy") is dummy
    assert "dummy" in [t.name for t in reg.list_all()]


def test_tool_registry_unknown():
    reg = ToolRegistry()
    with pytest.raises(KeyError):
        reg.get("nonexistent")


def test_stub_adapters(tmp_path):
    for adapter_cls in [ClaudeAdapter, CodexAdapter, CursorAdapter]:
        adapter = adapter_cls()
        assert adapter.name in ["claude", "codex", "cursor"]
        assert adapter.supports_quota is False
        assert adapter.supports_watch is False
        assert adapter.supports_refresh is False

        storage = adapter.get_storage(tmp_path)
        assert storage.home == tmp_path / "tools" / adapter.name

        qs, project_id, err = adapter.fetch_quota(storage, "test")
        assert qs is None
        assert "not supported" in err.lower()

        ok, msg = adapter.refresh_account(storage, "test")
        assert ok is False
        assert "not supported" in msg.lower()

        with pytest.raises(NotImplementedError):
            adapter.create_watcher(storage)
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_adapters.py -v`
Expected: FAIL with ModuleNotFoundError: No module named 'promux.adapters'

- [ ] **Step 3: Implement `src/promux/adapters/base.py`, `registry.py`, `stubs.py`, `__init__.py`**
Create `src/promux/adapters/base.py`:
```python
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..models import QuotaSummary
from ..storage import StorageEngine


class BaseToolAdapter(ABC):
    """Abstract base class for developer CLI tool adapters."""

    name: str
    display_name: str

    @abstractmethod
    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        """Return the scoped StorageEngine configured for this tool."""
        raise NotImplementedError

    @property
    def supports_quota(self) -> bool:
        """Whether this tool supports proactive quota retrieval."""
        return False

    def fetch_quota(
        self, storage: StorageEngine, account_name: str
    ) -> tuple[QuotaSummary | None, str | None, str | None]:
        """Fetch quota summary for a named account (qs, project_id, error)."""
        return None, None, f"Quota inspection not supported for {self.display_name}."

    @property
    def supports_watch(self) -> bool:
        """Whether this tool supports reactive log failover watching."""
        return False

    def create_watcher(self, storage: StorageEngine) -> Any:
        """Create log watcher daemon instance for this tool."""
        raise NotImplementedError(f"Log watching not supported for {self.display_name}.")

    @property
    def supports_refresh(self) -> bool:
        """Whether this tool supports automated token refresh."""
        return False

    def refresh_account(
        self, storage: StorageEngine, account_name: str, force: bool = False
    ) -> tuple[bool, str]:
        """Execute token refresh for a named account."""
        return False, f"Token refresh not supported for {self.display_name}."

    def list_capabilities(self) -> list[str]:
        """Return list of supported capabilities for status display."""
        caps = ["vault"]
        if self.supports_quota:
            caps.append("quota")
        if self.supports_watch:
            caps.append("watch")
        if self.supports_refresh:
            caps.append("refresh")
        return caps
```

Create `src/promux/adapters/stubs.py`:
```python
from pathlib import Path

from ..constants import PROMUX_HOME
from ..storage import StorageEngine
from .base import BaseToolAdapter


class ClaudeAdapter(BaseToolAdapter):
    name = "claude"
    display_name = "Claude Code"

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        home = (promux_home or PROMUX_HOME) / "tools" / self.name
        return StorageEngine(promux_home=home)


class CodexAdapter(BaseToolAdapter):
    name = "codex"
    display_name = "Codex CLI"

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        home = (promux_home or PROMUX_HOME) / "tools" / self.name
        return StorageEngine(promux_home=home)


class CursorAdapter(BaseToolAdapter):
    name = "cursor"
    display_name = "Cursor CLI"

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        home = (promux_home or PROMUX_HOME) / "tools" / self.name
        return StorageEngine(promux_home=home)
```

Create `src/promux/adapters/registry.py`:
```python
from .base import BaseToolAdapter


class ToolRegistry:
    """Central registry of supported developer CLI tool adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, BaseToolAdapter] = {}
        self._default_tool: str | None = None

    def register(self, adapter: BaseToolAdapter, default: bool = False) -> None:
        self._adapters[adapter.name] = adapter
        if default or self._default_tool is None:
            self._default_tool = adapter.name

    def get(self, name: str) -> BaseToolAdapter:
        if name not in self._adapters:
            available = ", ".join(sorted(self._adapters.keys()))
            raise KeyError(f"Unknown tool '{name}'. Available tools: {available}")
        return self._adapters[name]

    def has_tool(self, name: str) -> bool:
        return name in self._adapters

    def list_all(self) -> list[BaseToolAdapter]:
        return list(self._adapters.values())

    def default_tool(self) -> BaseToolAdapter:
        if not self._default_tool or self._default_tool not in self._adapters:
            raise RuntimeError("No default tool registered.")
        return self._adapters[self._default_tool]
```

Create `src/promux/adapters/__init__.py`:
```python
from .base import BaseToolAdapter
from .registry import ToolRegistry
from .stubs import ClaudeAdapter, CodexAdapter, CursorAdapter

__all__ = [
    "BaseToolAdapter",
    "ToolRegistry",
    "ClaudeAdapter",
    "CodexAdapter",
    "CursorAdapter",
]
```

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_adapters.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/adapters/ tests/test_adapters.py
git commit -m "feat(adapters): create BaseToolAdapter, ToolRegistry, and scaffolded tool stubs"
```

---

### Task 3: Antigravity Concrete Adapter (`src/promux/adapters/agy.py`)

**Files:**
- Create: `src/promux/adapters/agy.py`
- Modify: `src/promux/adapters/__init__.py`
- Test: `tests/test_adapters.py`

**Interfaces:**
- Consumes: `GEMINI_CLI_HOME`, `PROMUX_HOME`, `StorageEngine`, `QuotaClient`
- Produces: `AgyAdapter` implementing `BaseToolAdapter` with full quota, watch, and refresh capabilities

- [ ] **Step 1: Write unit tests for `AgyAdapter`**
Add to `tests/test_adapters.py`:
```python
from promux.adapters.agy import AgyAdapter


def test_agy_adapter_properties(tmp_path):
    gemini_dir = tmp_path / "gemini"
    gemini_dir.mkdir(parents=True)
    live_token = gemini_dir / "antigravity-oauth-token"
    live_token.write_text('{"token": {"access_token": "abc"}}')

    adapter = AgyAdapter(gemini_home=gemini_dir)
    assert adapter.name == "agy"
    assert adapter.display_name == "Antigravity CLI"
    assert adapter.supports_quota is True
    assert adapter.supports_watch is True
    assert adapter.supports_refresh is True

    storage = adapter.get_storage(promux_home=tmp_path)
    assert storage.home == tmp_path
    assert storage.live_token == live_token
    assert "vault" in adapter.list_capabilities()
    assert "quota" in adapter.list_capabilities()
    assert "watch" in adapter.list_capabilities()
    assert "refresh" in adapter.list_capabilities()
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_adapters.py::test_agy_adapter_properties -v`
Expected: FAIL with ImportError: cannot import name 'AgyAdapter'

- [ ] **Step 3: Implement `src/promux/adapters/agy.py`**
Create `src/promux/adapters/agy.py`:
```python
from pathlib import Path
from typing import Any

from ..constants import GEMINI_CLI_HOME, PROMUX_HOME
from ..models import QuotaSummary
from ..quota import QuotaClient
from ..storage import StorageEngine
from .base import BaseToolAdapter


class AgyAdapter(BaseToolAdapter):
    """Adapter for Google Antigravity CLI (`agy`)."""

    name = "agy"
    display_name = "Antigravity CLI"

    def __init__(self, gemini_home: Path | None = None) -> None:
        self.gemini_home = Path(gemini_home or GEMINI_CLI_HOME)

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        """Return StorageEngine scoped for Antigravity, preserving backward compatibility."""
        home = Path(promux_home or PROMUX_HOME)
        return StorageEngine(promux_home=home, gemini_home=self.gemini_home)

    @property
    def supports_quota(self) -> bool:
        return True

    def fetch_quota(
        self, storage: StorageEngine, account_name: str
    ) -> tuple[QuotaSummary | None, str | None, str | None]:
        from ..cli import _fetch_account_quota

        return _fetch_account_quota(storage, account_name)

    @property
    def supports_watch(self) -> bool:
        return True

    def create_watcher(self, storage: StorageEngine) -> Any:
        from ..watch import LogWatcher

        return LogWatcher(storage=storage)

    @property
    def supports_refresh(self) -> bool:
        return True

    def refresh_account(
        self, storage: StorageEngine, account_name: str, force: bool = False
    ) -> tuple[bool, str]:
        from ..cli import _refresh_account_token

        return _refresh_account_token(storage, account_name, force=force)
```
Update `src/promux/adapters/__init__.py` to export `AgyAdapter` and create `get_default_registry()`:
```python
from .agy import AgyAdapter
from .base import BaseToolAdapter
from .registry import ToolRegistry
from .stubs import ClaudeAdapter, CodexAdapter, CursorAdapter


def get_default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(AgyAdapter(), default=True)
    reg.register(ClaudeAdapter())
    reg.register(CodexAdapter())
    reg.register(CursorAdapter())
    return reg


__all__ = [
    "AgyAdapter",
    "BaseToolAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "ToolRegistry",
    "get_default_registry",
]
```

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_adapters.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/adapters/ tests/test_adapters.py
git commit -m "feat(adapters): implement AgyAdapter and default registry factory"
```

---

### Task 4: Redesign `promux quota` Display Logic

**Files:**
- Modify: `src/promux/cli.py:659-805`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `format_quota_cell`, `format_quota_detail`, `format_relative_countdown` from `promux.formatters`
- Produces: Updated `cmd_quota` printing formatted reset countdowns in cells and extended JSON schema

- [ ] **Step 1: Write test verifying new quota table output and JSON fields**
In `tests/test_cli.py`, add `test_cmd_quota_redesigned_display`:
```python
def test_cmd_quota_redesigned_display(storage_with_profiles, monkeypatch, capsys):
    from promux.cli import cmd_quota
    from promux.models import QuotaSummary

    mock_qs = QuotaSummary(
        gemini_5h_remaining=0.98,
        gemini_weekly_remaining=0.85,
        third_party_5h_remaining=1.0,
        third_party_weekly_remaining=0.925,
        gemini_5h_reset="2099-01-01T14:15:00Z",
        gemini_weekly_reset="2099-01-05T16:00:00Z",
    )

    def mock_fetch(storage, name):
        return mock_qs, "test-proj", None

    monkeypatch.setattr("promux.cli._fetch_account_quota", mock_fetch)

    # Overview mode
    ret = cmd_quota(storage_with_profiles, name=None, json_out=False)
    assert ret == 0
    captured = capsys.readouterr().out

    # Verify column headers: NEXT RESET is gone, cells contain reset countdowns
    assert "NEXT RESET" not in captured
    assert "GEMINI (5H)" in captured
    assert "GEMINI (WK)" in captured
    assert "98.0%" in captured
    assert "85.0%" in captured

    # Single profile detail mode
    capsys.readouterr()  # clear buffer
    ret_detail = cmd_quota(storage_with_profiles, name="work", json_out=False)
    assert ret_detail == 0
    detail_captured = capsys.readouterr().out
    assert "REMAINING & RESET" in detail_captured
    assert "98.0%" in detail_captured
    assert "UTC" in detail_captured
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_cli.py::test_cmd_quota_redesigned_display -v`
Expected: FAIL because `NEXT RESET` is currently present and `REMAINING & RESET` is not used.

- [ ] **Step 3: Update `cmd_quota` in `src/promux/cli.py`**
Modify `cmd_quota` in `src/promux/cli.py`:
- Import formatters:
```python
from .formatters import (
    format_quota_cell,
    format_quota_detail,
    format_relative_countdown,
)
```
- In `cmd_quota`:
  - When `json_out=True`:
    Add `"5h_reset_relative": format_relative_countdown(qs.gemini_5h_reset)` and `"weekly_reset_relative": format_relative_countdown(qs.gemini_weekly_reset)` to JSON response.
  - In single-profile text mode:
    ```python
    active_tag = " [ACTIVE]" if name == active_profile else ""
    print(f"Quota for account '{name}' (project: {project_id}){active_tag}:\n")
    header = f"{'MODEL GROUP':<24} {'WINDOW':<10} {'REMAINING & RESET'}"
    print(header)
    print("-" * 80)

    g_5h = format_quota_detail(qs.gemini_5h_remaining, qs.gemini_5h_reset)
    g_wk = format_quota_detail(qs.gemini_weekly_remaining, qs.gemini_weekly_reset)
    c_5h = format_quota_detail(qs.third_party_5h_remaining, qs.third_party_5h_reset)
    c_wk = format_quota_detail(qs.third_party_weekly_remaining, qs.third_party_weekly_reset)

    print(f"{'Gemini Models':<24} {'5h':<10} {g_5h}")
    print(f"{'Gemini Models':<24} {'weekly':<10} {g_wk}")
    print(f"{'Claude & GPT Models':<24} {'5h':<10} {c_5h}")
    print(f"{'Claude & GPT Models':<24} {'weekly':<10} {c_wk}")
    return 0
    ```
  - In multi-profile overview mode:
    ```python
    # format cells
    if qs:
        g5 = format_quota_cell(qs.gemini_5h_remaining, qs.gemini_5h_reset)
        gw = format_quota_cell(qs.gemini_weekly_remaining, qs.gemini_weekly_reset)
        c5 = format_quota_cell(qs.third_party_5h_remaining, qs.third_party_5h_reset)
        cw = format_quota_cell(qs.third_party_weekly_remaining, qs.third_party_weekly_reset)
    else:
        g5 = "[AUTH ERROR]" if "401" in str(err) else "[ERROR]"
        gw = "-"
        c5 = "-"
        cw = "-"
    text_rows.append((active_mark, acct_name, g5, gw, c5, cw))

    ...
    header = (
        f"{'ACTIVE':<7} {'PROFILE':<17} {'GEMINI (5H)':<20} {'GEMINI (WK)':<20} "
        f"{'CLAUDE (5H)':<20} {'CLAUDE (WK)':<20}"
    )
    print(header)
    print("-" * len(header))
    for active_mark, acct_name, g5, gw, c5, cw in text_rows:
        print(
            f"{active_mark:<7} {acct_name:<17} {g5:<20} {gw:<20} {c5:<20} {cw:<20}"
        )
    ```

- [ ] **Step 4: Update any existing quota tests in `tests/test_cli.py` to match new columns**
Update assertions in `tests/test_cli.py` that check for old `NEXT RESET (UTC)` to check the new headers.
Run: `pytest tests/test_cli.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/cli.py tests/test_cli.py
git commit -m "feat(cli): redesign quota display to show reset countdowns directly after percentages"
```

---

### Task 5: Tool-First CLI Dispatcher & `promux tools` Command

**Files:**
- Modify: `src/promux/cli.py`
- Modify: `src/promux/completion.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_completion.py`

**Interfaces:**
- Consumes: `get_default_registry()`, `ToolRegistry`
- Produces:
  - `promux tools [list]` command
  - `promux <tool> <command>` tool-first dispatch
  - Backward-compatible top-level fallback (`promux <command>` defaulting to `agy`)

- [ ] **Step 1: Write CLI routing tests**
Add to `tests/test_cli.py`:
```python
def test_tools_command(capsys):
    from promux.cli import main

    ret = main(["tools"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "agy" in captured
    assert "claude" in captured
    assert "codex" in captured
    assert "cursor" in captured


def test_tool_first_dispatch(storage_with_profiles, monkeypatch, capsys):
    from promux.cli import main

    # Calling `promux agy list`
    ret = main(["agy", "list"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "work" in captured
    assert "personal" in captured


def test_top_level_fallback(storage_with_profiles, monkeypatch, capsys):
    from promux.cli import main

    # Calling `promux list` defaults to agy
    ret = main(["list"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "work" in captured
    assert "personal" in captured
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_cli.py::test_tools_command -v`
Expected: FAIL (unrecognized argument 'tools')

- [ ] **Step 3: Implement CLI routing and `tools` command in `src/promux/cli.py`**
In `src/promux/cli.py`:
1. Add `cmd_tools(registry: ToolRegistry, json_out: bool) -> int`:
   - Prints table or JSON of registered tools, active profile for each tool, and capabilities.
2. Update CLI parser to include `tools` command:
   ```python
   tools_p = sub.add_parser("tools", help="List supported developer CLI tools")
   tools_p.add_argument("--json", action="store_true", help="Output in JSON format")
   ```
3. Update `main(argv=None)`:
   - Instantiate `registry = get_default_registry()`.
   - Inspect `raw_args = list(argv) if argv is not None else sys.argv[1:]`.
   - If `raw_args` starts with a registered tool name (e.g. `raw_args[0] in [t.name for t in registry.list_all()]`):
     - `target_tool = registry.get(raw_args[0])`
     - Strip the tool prefix: `remaining_args = raw_args[1:]`
     - Parse args with `remaining_args`, pass `target_tool.get_storage()` to command handlers.
   - Else if `raw_args` starts with `tools`:
     - Route to `cmd_tools`.
   - Else:
     - Use `default_tool = registry.default_tool()`, pass `default_tool.get_storage()` to command handlers.

4. In `src/promux/completion.py`:
   - Add tool names (`agy`, `claude`, `codex`, `cursor`) and `tools` to completion candidates.

- [ ] **Step 4: Run tests to verify they pass**
Run: `pytest tests/test_cli.py tests/test_completion.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/cli.py src/promux/completion.py tests/test_cli.py tests/test_completion.py
git commit -m "feat(cli): add tool-first syntax routing and promux tools command"
```

---

### Task 6: Documentation, Specification Update & Verification

**Files:**
- Modify: `README.md`
- Modify: `SPEC.md`
- Test: Full test suite

- [ ] **Step 1: Update `SPEC.md` and `README.md`**
- In `SPEC.md`: Document the multi-tool architecture, `BaseToolAdapter`, `ToolRegistry`, and updated quota display format.
- In `README.md`: Add section on Multi-Tool Support (`promux <tool> <command>`, `promux tools`), and show the updated `quota` table with reset countdowns after percentages.

- [ ] **Step 2: Run complete test suite and linters**
Run: `pytest -v`
Expected: 100% tests pass (145+ tests).

- [ ] **Step 3: Commit**
```bash
git add README.md SPEC.md
git commit -m "docs: document multi-tool CLI support and new quota reset format"
```
