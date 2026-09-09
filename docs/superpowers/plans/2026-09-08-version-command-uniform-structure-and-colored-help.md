# Version Command, Uniform Tool Structure & Colored Help Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated `version` command and `-V`/`--version` flags, unify all tool storage directories under `~/.promux/tools/<tool_name>/` with a safe manual migration of live Antigravity profiles, and deliver a dev-friendly, categorized help system with subtle ANSI color accents.

**Architecture:** Create a zero-dependency ANSI color and terminal detection engine in `src/promux/colors.py`. Align `AgyAdapter` storage paths to `~/.promux/tools/agy/`, mirroring `claude`, `codex`, and `cursor`. Provide version metadata extraction and formatted responses in `src/promux/version.py`. Build a categorized help renderer in `src/promux/help.py` and hook both into `src/promux/cli.py`.

**Tech Stack:** Python 3.10+ standard library (`argparse`, `sys`, `os`, `platform`, `pathlib`, `json`, `shutil`), pytest.

## Global Constraints

- Zero external runtime dependencies beyond the Python standard library.
- Colors must be automatically disabled when output is piped, `NO_COLOR` is set, `TERM=dumb`, or `--no-color` is passed.
- Safe manual migration of `~/.promux/` must create an atomic timestamped backup before touching any files.
- 100% test coverage across existing and new test suites.

---

### Task 1: Color Module & Terminal Detection Engine

**Files:**
- Create: `src/promux/colors.py`
- Test: `tests/test_colors.py`

**Interfaces:**
- Produces:
  - `is_color_enabled(stream=None, force_no_color=False) -> bool`
  - `bold(text: str, enabled: bool = True) -> str`
  - `dim(text: str, enabled: bool = True) -> str`
  - `cyan(text: str, enabled: bool = True) -> str`
  - `green(text: str, enabled: bool = True) -> str`
  - `yellow(text: str, enabled: bool = True) -> str`
  - `red(text: str, enabled: bool = True) -> str`
  - `style(text: str, code: str, enabled: bool = True) -> str`

- [ ] **Step 1: Write tests for color styling and auto-detection**

Create `tests/test_colors.py`:
```python
import os
import sys
from promux.colors import is_color_enabled, bold, cyan, green, yellow, red, dim


def test_is_color_enabled_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert is_color_enabled(force_no_color=False) is False


def test_is_color_enabled_term_dumb(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert is_color_enabled(force_no_color=False) is False


def test_is_color_enabled_forced_off(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert is_color_enabled(force_no_color=True) is False


def test_color_formatting_enabled():
    assert bold("hello", enabled=True) == "\033[1mhello\033[0m"
    assert dim("hello", enabled=True) == "\033[2mhello\033[0m"
    assert cyan("hello", enabled=True) == "\033[36mhello\033[0m"
    assert green("hello", enabled=True) == "\033[32mhello\033[0m"
    assert yellow("hello", enabled=True) == "\033[33mhello\033[0m"
    assert red("hello", enabled=True) == "\033[31mhello\033[0m"


def test_color_formatting_disabled():
    assert bold("hello", enabled=False) == "hello"
    assert dim("hello", enabled=False) == "hello"
    assert cyan("hello", enabled=False) == "hello"
    assert green("hello", enabled=False) == "hello"
    assert yellow("hello", enabled=False) == "hello"
    assert red("hello", enabled=False) == "hello"
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_colors.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'promux.colors'`)

- [ ] **Step 3: Implement `src/promux/colors.py`**

```python
import os
import sys
from typing import TextIO

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"


def is_color_enabled(stream: TextIO | None = None, force_no_color: bool = False) -> bool:
    """Check if ANSI color output should be enabled."""
    if force_no_color:
        return False
    if "NO_COLOR" in os.environ and os.environ["NO_COLOR"] != "":
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    target_stream = stream or sys.stdout
    return hasattr(target_stream, "isatty") and target_stream.isatty()


def style(text: str, code: str, enabled: bool = True) -> str:
    """Apply ANSI style code to text if enabled."""
    if not enabled:
        return text
    return f"{code}{text}{RESET}"


def bold(text: str, enabled: bool = True) -> str:
    return style(text, BOLD, enabled=enabled)


def dim(text: str, enabled: bool = True) -> str:
    return style(text, DIM, enabled=enabled)


def cyan(text: str, enabled: bool = True) -> str:
    return style(text, CYAN, enabled=enabled)


def green(text: str, enabled: bool = True) -> str:
    return style(text, GREEN, enabled=enabled)


def yellow(text: str, enabled: bool = True) -> str:
    return style(text, YELLOW, enabled=enabled)


def red(text: str, enabled: bool = True) -> str:
    return style(text, RED, enabled=enabled)
```

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_colors.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/colors.py tests/test_colors.py
git commit -m "feat(colors): add zero-dependency ANSI color and terminal detection engine"
```

---

### Task 2: Version Command & CLI Invocations

**Files:**
- Create: `src/promux/version.py`
- Modify: `src/promux/cli.py`
- Test: `tests/test_version.py`

**Interfaces:**
- Consumes: `promux.__version__`, `promux.adapters.get_default_registry`, `promux.colors`
- Produces:
  - `get_version_info() -> dict[str, Any]`
  - `cmd_version(json_out: bool = False, no_color: bool = False) -> int`
  - CLI flags: `-V`, `--version`, and subcommand: `version`

- [ ] **Step 1: Write version tests**

Create `tests/test_version.py`:
```python
import json
import pytest
from promux.cli import main
from promux.version import get_version_info


def test_get_version_info():
    info = get_version_info()
    assert "version" in info
    assert "python" in info
    assert "platform" in info
    assert "tools" in info
    assert "agy" in info["tools"]


def test_cli_version_subcommand(capsys):
    ret = main(["version"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "promux" in out
    assert "Python" in out


def test_cli_version_flag_long(capsys):
    ret = main(["--version"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "promux" in out


def test_cli_version_flag_short(capsys):
    ret = main(["-V"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "promux" in out


def test_cli_version_json(capsys):
    ret = main(["version", "--json"])
    assert ret == 0
    data = json.loads(capsys.readouterr().out)
    assert "version" in data
    assert "tools" in data
    assert data["default_tool"] == "agy"
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_version.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `src/promux/version.py` and connect in `src/promux/cli.py`**

Create `src/promux/version.py`:
```python
import json
import platform
import sys
from typing import Any

from . import __version__
from .adapters import get_default_registry
from .colors import bold, cyan, dim, is_color_enabled


def get_version_info() -> dict[str, Any]:
    registry = get_default_registry()
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()}-{platform.machine()}",
        "tools": registry.list_names(),
        "default_tool": registry.default_tool().name,
    }


def cmd_version(json_out: bool = False, no_color: bool = False) -> int:
    info = get_version_info()
    if json_out:
        print(json.dumps(info, indent=2))
        return 0

    use_color = is_color_enabled(force_no_color=no_color)
    prog_str = bold(f"promux {info['version']}", enabled=use_color)
    runtime_str = dim(f"(Python {info['python']}, {info['platform']})", enabled=use_color)
    tools_list = []
    for tool in info["tools"]:
        if tool == info["default_tool"]:
            tools_list.append(f"{cyan(tool, enabled=use_color)} (default)")
        else:
            tools_list.append(cyan(tool, enabled=use_color))
    tools_str = ", ".join(tools_list)

    print(f"{prog_str} {runtime_str}")
    print(f"Supported tools: {tools_str}")
    return 0
```

Update `src/promux/cli.py`:
- Add `version` parser to subcommands.
- Add `-V` / `--version` to top-level parser.
- In `main()`, check for `version` subcommand and `-V` / `--version` flag.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_version.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/version.py src/promux/cli.py tests/test_version.py
git commit -m "feat(cli): add version command and -V/--version flags"
```

---

### Task 3: Uniform Tool Storage Layout & Live Antigravity Migration

**Files:**
- Modify: `src/promux/adapters/agy.py`
- Modify: `tests/test_adapters.py`
- Live System: `~/.promux`

**Interfaces:**
- `AgyAdapter.get_storage(promux_home)` maps to `(promux_home or PROMUX_HOME) / "tools" / self.name`
- Backward-compatible `promux.cli.get_storage()` returns `AgyAdapter().get_storage()`.

- [ ] **Step 1: Write failing test for AgyAdapter scoped path**

In `tests/test_adapters.py`, update `test_agy_adapter_properties`:
```python
def test_agy_adapter_storage_path(tmp_path):
    adapter = AgyAdapter()
    storage = adapter.get_storage(promux_home=tmp_path)
    assert storage.promux_home == tmp_path / "tools" / "agy"
    assert storage.accounts_dir == tmp_path / "tools" / "agy" / "accounts"
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_adapters.py -k test_agy_adapter_storage_path -v`
Expected: FAIL (`assert ... == ... / 'tools' / 'agy'`)

- [ ] **Step 3: Update `src/promux/adapters/agy.py`**

In `src/promux/adapters/agy.py`:
```python
    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        """Return StorageEngine scoped for Antigravity under tools/agy."""
        env_home = Path(os.environ["PROMUX_HOME"]) if "PROMUX_HOME" in os.environ else None
        base = Path(promux_home or env_home or PROMUX_HOME)
        home = base / "tools" / self.name
        return StorageEngine(promux_home=home, gemini_home=self.gemini_home)
```

- [ ] **Step 4: Update test assertions and fixtures where necessary**
Update `tests/test_adapters.py` and verify all tests pass with the new path.
Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Perform manual live migration of user's `~/.promux` with timestamped backup**

Run bash commands:
1. `BACKUP_DIR=~/.promux.backup-$(date +%Y%m%d_%H%M%S)`
2. `cp -rp ~/.promux "$BACKUP_DIR"`
3. `mkdir -p ~/.promux/tools/agy`
4. If `~/.promux/accounts` exists, `mv ~/.promux/accounts ~/.promux/tools/agy/`
5. If `~/.promux/state.json` exists, `mv ~/.promux/state.json ~/.promux/tools/agy/`
6. If `~/.promux/manager.lock` exists, `mv ~/.promux/manager.lock ~/.promux/tools/agy/`
7. Verify permissions on `~/.promux/tools/agy` (`0700`) and files inside (`0600`).
8. Run `python3 -m promux.cli list` to verify all accounts (`main`, `hard`, `manova`) load properly.

- [ ] **Step 6: Commit**
```bash
git add src/promux/adapters/agy.py tests/test_adapters.py
git commit -m "feat(adapters): unify AgyAdapter storage under tools/agy"
```

---

### Task 4: Dev-Friendly Grouped & Colored Help System

**Files:**
- Create: `src/promux/help.py`
- Modify: `src/promux/cli.py`
- Test: `tests/test_help.py`

**Interfaces:**
- Consumes: `promux.colors`, `promux.adapters.get_default_registry`
- Produces:
  - `cmd_help(subcommand: str | None = None, tool: str | None = None, no_color: bool = False) -> int`
  - `print_main_help(no_color: bool = False) -> None`
  - `print_subcommand_help(command_name: str, no_color: bool = False) -> None`

- [ ] **Step 1: Write help command tests**

Create `tests/test_help.py`:
```python
from promux.cli import main


def test_cli_help_subcommand(capsys):
    ret = main(["help"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Profile Management" in out
    assert "Quota & Failover" in out
    assert "Tools & Utilities" in out
    assert "switch" in out
    assert "quota" in out


def test_cli_help_flag(capsys):
    ret = main(["--help"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Profile Management" in out
    assert "switch" in out


def test_cli_help_specific_command(capsys):
    ret = main(["help", "switch"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "switch" in out
    assert "Hot-swap" in out or "profile" in out


def test_cli_help_tool_specific(capsys):
    ret = main(["claude", "help"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "claude" in out
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_help.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `src/promux/help.py` and hook into `src/promux/cli.py`**

Create `src/promux/help.py` with categorized sections:
- Section 1: Usage patterns (`promux <tool> <command> [args]`, `promux <command> [args]`)
- Section 2: Profile Management (`list`, `switch`, `save`, `next`, `remove`, `whoami`)
- Section 3: Quota & Failover (`quota`, `refresh`, `watch`)
- Section 4: Tools & Utilities (`tools`, `version`, `completion`, `help`)
- Section 5: Options (`--json`, `--no-color`, `-h, --help`, `-V, --version`)
- Subcommand specific help handler.

Update `src/promux/cli.py`:
- Add `help` command to parser.
- Route `-h` / `--help` and `help` subcommand to `cmd_help`.
- Support `promux help <cmd>` and `promux <tool> help [cmd]`.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_help.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/promux/help.py src/promux/cli.py tests/test_help.py
git commit -m "feat(cli): add dev-friendly categorized help with ANSI color accents"
```

---

### Task 5: Documentation & End-to-End Verification

**Files:**
- Modify: `README.md`
- Modify: `SPEC.md`
- Test: Full pytest suite

- [ ] **Step 1: Update `README.md` and `SPEC.md`**
- Document `promux version` and `-V` / `--version`.
- Document `promux help [command]`.
- Update filesystem layout showing `~/.promux/tools/agy/`.
- Document `NO_COLOR` and `--no-color` support.

- [ ] **Step 2: Run complete test suite**
Run: `pytest -v`
Expected: 100% tests pass (170+ tests).

- [ ] **Step 3: Verify live commands on terminal**
Run:
- `promux version`
- `promux tools`
- `promux list`
- `promux help`
- `promux help quota`

- [ ] **Step 4: Commit**
```bash
git add README.md SPEC.md
git commit -m "docs: update specification and guide for uniform structure, version, and colored help"
```
