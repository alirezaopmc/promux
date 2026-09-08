# Design Specification: Version Command, Uniform Tool Structure & Colored Help System

> **Date:** 2026-09-08  
> **Status:** Proposed  
> **Target:** `promux` v0.1.0+  

---

## 1. Problem Statement & Motivation

1. **Missing `version` command and `-V`/`--version` flags**: `promux` currently lacks a native version inquiry command or flag, returning an argument error when developers run `promux version` or `promux --version`.
2. **Asymmetric Storage Structure**: Currently, `AgyAdapter` stores profiles at `~/.promux/accounts`, `state.json`, and `manager.lock` directly in `~/.promux/`, whereas other tools (`claude`, `codex`, `cursor`) are scoped under `~/.promux/tools/<tool_name>/`. Best practice requires a clean, uniform directory layout where every tool, including `agy`, is isolated under `~/.promux/tools/<tool_name>/`.
3. **Manual Migration Requirement**: The user's live profiles (`main`, `hard`, `manova`) need to be safely migrated to `~/.promux/tools/agy/` with full backup and permission preservation, without adding migration complexity to the runtime CLI code.
4. **Uncolored, Monolithic Help Output**: The default `argparse` `--help` output lacks categorization and visual hierarchy, does not support `promux help [command]`, and lacks dev-friendly color accents.

---

## 2. Architectural Design

### 2.1 Uniform Tool Storage Hierarchy
All tool profiles, state files, and locks reside strictly in:
```text
~/.promux/
├── tools/
│   ├── agy/
│   │   ├── accounts/
│   │   │   ├── main/
│   │   │   │   └── antigravity-oauth-token  (0600)
│   │   │   ├── hard/
│   │   │   └── manova/
│   │   ├── state.json                       (0600)
│   │   └── manager.lock                     (0600)
│   ├── claude/
│   │   ├── accounts/
│   │   ├── state.json
│   │   └── manager.lock
│   ├── codex/
│   │   └── ...
│   └── cursor/
│       └── ...
└── backups/
    └── backup-YYYYMMDD-HHMMSS/              (pre-migration snapshots)
```

`AgyAdapter.get_storage(promux_home)` will be updated to:
```python
def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
    env_home = Path(os.environ["PROMUX_HOME"]) if "PROMUX_HOME" in os.environ else None
    base = Path(promux_home or env_home or PROMUX_HOME)
    home = base / "tools" / self.name
    return StorageEngine(promux_home=home, gemini_home=self.gemini_home)
```

`promux.cli.get_storage()` is retained for backward compatibility, delegating to `AgyAdapter().get_storage()`.

### 2.2 Terminal Detection & Color Engine (`src/promux/colors.py`)
A zero-dependency color helper:
- **Detection**: Enabled only if:
  - `sys.stdout.isatty()` is True, AND
  - `NO_COLOR` is not set in `os.environ`, AND
  - `TERM` != `"dumb"`, AND
  - `--no-color` is not requested, AND
  - Output format is not JSON (`--json`).
- **Styles**:
  - `bold(text)` -> `\033[1m{text}\033[0m`
  - `dim(text)` -> `\033[2m{text}\033[0m`
  - `cyan(text)` -> `\033[36m{text}\033[0m` (commands, subcommands, tools)
  - `green(text)` -> `\033[32m{text}\033[0m` (active status, success)
  - `yellow(text)` -> `\033[33m{text}\033[0m` (cooldown, warnings)
  - `red(text)` -> `\033[31m{text}\033[0m` (errors)

### 2.3 Version System (`promux version` & `-V` / `--version`)
- **Version Source**: Centrally referenced from `promux.__version__`.
- **Invocations**:
  - `promux version`
  - `promux --version`
  - `promux -V`
  - `promux agy version`
- **Output Format (Text)**:
  ```text
  promux 0.1.0 (Python 3.14.4, Linux-x86_64)
  Supported tools: agy (default), claude, codex, cursor
  ```
- **Output Format (JSON)** (`--json`):
  ```json
  {
    "version": "0.1.0",
    "python": "3.14.4",
    "platform": "Linux-x86_64",
    "tools": ["agy", "claude", "codex", "cursor"],
    "default_tool": "agy"
  }
  ```

### 2.4 Dev-Friendly Help System (`promux help`, `promux --help`)
- **Invocation**:
  - `promux help` or `promux --help` or `promux -h`
  - `promux help <subcommand>` (e.g. `promux help quota`)
  - `promux <tool> help` (e.g. `promux claude help`)
- **Categorized Sections**:
  1. **Usage Overview**: Syntax patterns for tool-first and default modes.
  2. **Profile Management**: `list`, `switch`, `save`, `next`, `remove`, `whoami`.
  3. **Quota & Failover**: `quota`, `refresh`, `watch`.
  4. **Tools & Utilities**: `tools`, `version`, `completion`, `help`.
  5. **Global Flags**: `--json`, `--no-color`, `-h, --help`, `-V, --version`.
- **Styling**:
  - Headers in bold white.
  - Subcommands in cyan with 16-character column padding.
  - Descriptions in dim/regular text.
  - Examples indented with subtle accents.

---

## 3. Manual Data Migration Plan
1. Create timestamped backup: `~/.promux.backup-$(date +%Y%m%d_%H%M%S)` containing complete snapshot of `~/.promux/`.
2. Ensure directory `~/.promux/tools/agy` exists with `0700` / `0755` permissions.
3. Move `~/.promux/accounts/`, `~/.promux/state.json`, and `~/.promux/manager.lock` into `~/.promux/tools/agy/`.
4. Verify directory permissions (`0600` for token and state files).
5. Verify live active token in `~/.gemini/antigravity-cli/` remains untouched and matching active account (`main`).
6. Run `promux list` and `promux tools` to confirm live operation.

---

## 4. Test & Verification Plan
1. Unit tests for `src/promux/colors.py`:
   - Tests with simulated TTY and `NO_COLOR` disabled (ANSI escapes present).
   - Tests with `NO_COLOR=1` or `TERM=dumb` (ANSI escapes stripped).
2. Unit tests for `version` command and flags:
   - `promux version` exit code 0, contains version string and python info.
   - `promux --version` / `promux -V` prints version string.
   - `promux version --json` outputs valid schema.
3. Unit tests for `help` command:
   - `promux help` displays categorized groups.
   - `promux help <subcommand>` displays subcommand details.
   - `promux claude help` displays tool-filtered help.
4. Regression suite:
   - All existing tests in `tests/` pass against the updated `AgyAdapter.get_storage()` pointing to `tools/agy`.
