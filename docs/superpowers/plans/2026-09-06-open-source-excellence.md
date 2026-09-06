# Open Source Excellence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform Promux into a tier-1, professional-grade open source project with strict static analysis (Ruff + Mypy), pre-commit hooks, CI/CD with matrix testing and coverage, CodeQL security scanning, PyPI Trusted Publishing (OIDC), and native zero-dependency shell completions for Bash and Zsh.

**Architecture:** Maintain Promux's zero runtime external dependencies constraint while adding rich developer tooling, native shell completion generation in standard library Python, and comprehensive GitHub Actions automation.

**Tech Stack:** Python 3.10+ (stdlib: `argparse`, `pathlib`), `ruff`, `mypy`, `pytest`, `pytest-cov`, GitHub Actions (CodeQL, PyPI Trusted Publisher OIDC).

## Global Constraints

- Strict zero external runtime dependencies (Python standard library only for `src/promux`).
- POSIX concurrency guarantees (`fcntl.flock`, `0o600` permissions, `.tmp` + `os.replace`).
- Python version compatibility: Python 3.10, 3.11, 3.12, 3.13, 3.14 across Linux and macOS.

---

### Task 1: Tooling Configuration & Developer Ergonomics

**Files:**
- Modify: `pyproject.toml`
- Create: `.pre-commit-config.yaml`
- Test: `tests/test_models.py` (verify configuration loads and test runner executes)

**Interfaces:**
- Consumes: Existing project packaging metadata in `pyproject.toml`.
- Produces: Ruff, Mypy, and coverage configs in `pyproject.toml`; pre-commit hooks.

- [ ] **Step 1: Write configuration additions to pyproject.toml**

Add Ruff, Mypy, and Coverage configurations to `pyproject.toml`:
```toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B"]
ignore = []

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.mypy]
python_version = "3.10"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
check_untyped_defs = true
no_implicit_optional = true

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false

[tool.coverage.run]
source = ["promux"]
omit = ["tests/*"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
]
```
And expand `[project.optional-dependencies].dev` to include `"pytest-cov>=5.0.0"`, `"ruff>=0.5.0"`, `"mypy>=1.10.0"`.

- [ ] **Step 2: Create .pre-commit-config.yaml**

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
      - id: check-merge-conflict

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.4
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

- [ ] **Step 3: Run pytest to ensure test suite remains passing with new configuration**

Run: `pytest tests/test_models.py -v`  
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .pre-commit-config.yaml
git commit -m "feat(tooling): configure ruff, mypy, coverage, and pre-commit hooks"
```

---

### Task 2: Native Zero-Dependency Shell Completion Module

**Files:**
- Create: `src/promux/completion.py`
- Create: `tests/test_completion.py`

**Interfaces:**
- Consumes: None (pure stdlib).
- Produces: `generate_bash_completion() -> str`, `generate_zsh_completion() -> str`, `SUPPORTED_SHELLS: list[str]`.

- [ ] **Step 1: Write failing tests for completion generators**

Create `tests/test_completion.py`:
```python
import pytest
from promux.completion import generate_bash_completion, generate_zsh_completion, SUPPORTED_SHELLS


def test_supported_shells():
    assert "bash" in SUPPORTED_SHELLS
    assert "zsh" in SUPPORTED_SHELLS


def test_generate_bash_completion():
    script = generate_bash_completion()
    assert "_promux_completion()" in script
    assert "complete -F _promux_completion promux" in script
    assert "list save switch next quota whoami remove watch completion" in script
    # Dynamic account lookup check
    assert "accounts" in script


def test_generate_zsh_completion():
    script = generate_zsh_completion()
    assert "#compdef promux" in script
    assert "_promux()" in script
    assert "switch" in script
    assert "quota" in script
    assert "completion" in script
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_completion.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'promux.completion'`

- [ ] **Step 3: Implement src/promux/completion.py**

Create `src/promux/completion.py`:
```python
"""Native zero-dependency shell completion script generators for Promux."""

from typing import List

SUPPORTED_SHELLS: List[str] = ["bash", "zsh"]

SUBCOMMANDS = [
    "list",
    "save",
    "switch",
    "next",
    "quota",
    "whoami",
    "remove",
    "watch",
    "completion",
]


def generate_bash_completion() -> str:
    """Generate Bash completion script for promux."""
    return r"""# bash completion for promux
_promux_completion() {
    local cur prev words cword
    _init_completion || return

    local commands="list save switch next quota whoami remove watch completion"
    local common_opts="--json --help -h"

    # Accounts helper
    _promux_accounts() {
        local promux_home="${PROMUX_HOME:-$HOME/.promux}"
        if [[ -d "$promux_home/accounts" ]]; then
            command ls -1 "$promux_home/accounts" 2>/dev/null
        fi
    }

    if [[ $cword -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "$commands $common_opts" -- "$cur") )
        return 0
    fi

    case "${words[1]}" in
        switch|remove|quota)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "$common_opts" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "$(_promux_accounts)" -- "$cur") )
            fi
            return 0
            ;;
        save)
            if [[ "$prev" == "--email" ]]; then
                return 0
            fi
            COMPREPLY=( $(compgen -W "--email $common_opts" -- "$cur") )
            return 0
            ;;
        next)
            if [[ "$prev" == "--reason" || "$prev" == "--cooldown" ]]; then
                return 0
            fi
            COMPREPLY=( $(compgen -W "--reason --cooldown $common_opts" -- "$cur") )
            return 0
            ;;
        watch)
            if [[ "$prev" == "--poll-seconds" || "$prev" == "--cooldown" ]]; then
                return 0
            fi
            COMPREPLY=( $(compgen -W "--poll-seconds --cooldown $common_opts" -- "$cur") )
            return 0
            ;;
        completion)
            COMPREPLY=( $(compgen -W "bash zsh" -- "$cur") )
            return 0
            ;;
        *)
            COMPREPLY=( $(compgen -W "$common_opts" -- "$cur") )
            return 0
            ;;
    esac
}

complete -F _promux_completion promux
"""


def generate_zsh_completion() -> str:
    """Generate Zsh completion script for promux."""
    return r"""#compdef promux

_promux_accounts() {
    local promux_home="${PROMUX_HOME:-$HOME/.promux}"
    local -a accounts
    if [[ -d "$promux_home/accounts" ]]; then
        accounts=(${(f)"$(command ls -1 "$promux_home/accounts" 2>/dev/null)"})
        _describe 'account' accounts
    fi
}

_promux() {
    local context state line
    typeset -A opt_args

    local -a subcommands
    subcommands=(
        'list:List all accounts in the vault'
        'save:Save current active token as a named account'
        'switch:Hot-swap active profile to specified account'
        'next:Rotate to next eligible standby account'
        'quota:Query live Cloud Code Assist quota'
        'whoami:Display details of active profile'
        'remove:Remove an account from vault'
        'watch:Start reactive quota failover daemon'
        'completion:Generate shell completion script'
    )

    _arguments -C \
        '--json[Output structured JSON]' \
        '(-h --help)'{-h,--help}'[Show help]' \
        '1: :->command' \
        '*:: :->args'

    case $state in
        command)
            _describe 'promux command' subcommands
            ;;
        args)
            case $words[1] in
                switch|remove|quota)
                    _arguments \
                        '--json[Output structured JSON]' \
                        '1:account:_promux_accounts'
                    ;;
                save)
                    _arguments \
                        '--email[Account email address]:email:_message "email address"' \
                        '--json[Output structured JSON]' \
                        '1:name:_message "account name"'
                    ;;
                next)
                    _arguments \
                        '--reason[Reason for rotation]:reason:_message "reason"' \
                        '--cooldown[Cooldown minutes]:cooldown:_message "minutes"' \
                        '--json[Output structured JSON]'
                    ;;
                watch)
                    _arguments \
                        '--poll-seconds[Log polling interval]:seconds:_message "seconds"' \
                        '--cooldown[Fallback cooldown minutes]:cooldown:_message "minutes"'
                    ;;
                completion)
                    _arguments \
                        '1:shell:(bash zsh)'
                    ;;
            esac
            ;;
    esac
}

_promux "$@"
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_completion.py -v`  
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/promux/completion.py tests/test_completion.py
git commit -m "feat(completion): add native zero-dependency bash and zsh script generators"
```

---

### Task 3: Wire `completion` Subcommand into CLI & Update Documentation

**Files:**
- Modify: `src/promux/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`

**Interfaces:**
- Consumes: `src/promux/completion.py` (`generate_bash_completion`, `generate_zsh_completion`, `SUPPORTED_SHELLS`).
- Produces: CLI subcommand `promux completion <shell>`.

- [ ] **Step 1: Write failing CLI tests for completion subcommand**

Add tests to `tests/test_cli.py`:
```python
def test_cli_completion_bash(capsys):
    rc = main(["completion", "bash"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "_promux_completion" in out


def test_cli_completion_zsh(capsys):
    rc = main(["completion", "zsh"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert "#compdef promux" in out


def test_cli_completion_invalid_shell(capsys):
    rc = main(["completion", "fish"])
    assert rc == 2
    out, err = capsys.readouterr()
    assert "invalid choice" in (out + err).lower()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_cli.py::test_cli_completion_bash -v`  
Expected: FAIL (unrecognized subcommand `completion`)

- [ ] **Step 3: Modify `src/promux/cli.py` to add `cmd_completion` and parser entry**

In `src/promux/cli.py`:
1. Import `generate_bash_completion`, `generate_zsh_completion`, `SUPPORTED_SHELLS` from `.completion`.
2. Add subcommand function:
```python
def cmd_completion(shell: str) -> int:
    if shell == "bash":
        print(generate_bash_completion(), end="")
        return 0
    elif shell == "zsh":
        print(generate_zsh_completion(), end="")
        return 0
    return 1
```
3. Add to `build_parser()`:
```python
p_comp = subparsers.add_parser("completion", help="Generate shell auto-completion script")
p_comp.add_argument("shell", choices=SUPPORTED_SHELLS, help="Target shell (bash or zsh)")
```
4. Dispatch in `main()`:
```python
elif args.subcommand == "completion":
    return cmd_completion(args.shell)
```

- [ ] **Step 4: Run CLI completion tests to verify they pass**

Run: `pytest tests/test_cli.py -k "completion" -v`  
Expected: PASS (3 passed)

- [ ] **Step 5: Update documentation in `README.md` and `CONTRIBUTING.md`**

In `README.md`:
- Add `pipx install promux` section under Installation.
- Add `promux completion` to CLI reference and show quick setup:
  ```bash
  # In ~/.bashrc
  eval "$(promux completion bash)"
  # In ~/.zshrc
  eval "$(promux completion zsh)"
  ```
In `CONTRIBUTING.md`:
- Document how to test shell completion.

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`  
Expected: PASS (86 passed)

- [ ] **Step 7: Commit**

```bash
git add src/promux/cli.py tests/test_cli.py README.md CONTRIBUTING.md
git commit -m "feat(cli): wire completion subcommand and document shell setup & pipx"
```

---

### Task 4: GitHub Actions CI Matrix with Linting, Typecheck & Coverage

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `pyproject.toml` tool configs, Ruff, Mypy, `pytest-cov`.
- Produces: Multi-stage CI pipeline (lint, typecheck, matrix testing).

- [ ] **Step 1: Update `.github/workflows/ci.yml`**

Replace contents of `.github/workflows/ci.yml` with:
```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    name: Lint & Style Check (Ruff)
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install Ruff
        run: pip install ruff>=0.5.0

      - name: Run Ruff Lint
        run: ruff check src tests

      - name: Run Ruff Format Check
        run: ruff format --check src tests

  typecheck:
    name: Type Check (Mypy)
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies & Mypy
        run: |
          pip install -e ".[dev]"
          pip install mypy>=1.10.0

      - name: Run Mypy
        run: mypy src

  test:
    name: Test Python ${{ matrix.python-version }} on ${{ matrix.os }}
    needs: [lint]
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
          pip install pytest-cov

      - name: Run test suite with coverage
        run: |
          pytest -v --cov=promux --cov-report=xml --cov-report=term
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add ruff lint, mypy typecheck, and coverage to CI workflow"
```

---

### Task 5: Security Scanning & PyPI Automated Publishing Workflows

**Files:**
- Create: `.github/workflows/codeql.yml`
- Create: `.github/workflows/publish.yml`

**Interfaces:**
- Consumes: GitHub Actions environment, PyPI Trusted Publisher OIDC configuration.
- Produces: Automated semantic code security scanning and automated wheel/sdist releases.

- [ ] **Step 1: Create `.github/workflows/codeql.yml`**

```yaml
name: CodeQL Security Analysis

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  schedule:
    - cron: "0 6 * * 1"

jobs:
  analyze:
    name: Analyze Python
    runs-on: ubuntu-latest
    permissions:
      actions: read
      contents: read
      security-events: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Initialize CodeQL
        uses: github/codeql-action/init@v3
        with:
          languages: python

      - name: Perform CodeQL Analysis
        uses: github/codeql-action/analyze@v3
```

- [ ] **Step 2: Create `.github/workflows/publish.yml`**

```yaml
name: Publish to PyPI

on:
  release:
    types: [published]

jobs:
  build-and-publish:
    name: Build & Publish to PyPI
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/p/promux
    permissions:
      id-token: write
      contents: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install build tools
        run: python -m pip install --upgrade pip build

      - name: Build sdist and wheel
        run: python -m build

      - name: Publish package to PyPI (OIDC Trusted Publisher)
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          skip-existing: true

      - name: Attach artifacts to GitHub Release
        uses: softprops/action-gh-release@v2
        if: startsWith(github.ref, 'refs/tags/')
        with:
          files: dist/*
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/codeql.yml .github/workflows/publish.yml
git commit -m "ci: add codeql security analysis and pypi trusted publisher workflows"
```

---

### Task 6: Final Verification, Push & Sync to GitHub

**Files:**
- All touched files

**Interfaces:**
- Consumes: All commits across Tasks 1–5.
- Produces: Synced upstream repository at `origin/main`.

- [ ] **Step 1: Run complete test suite with coverage**

Run: `pytest -v --cov=promux`  
Expected: PASS (86+ passed, 0 failures)

- [ ] **Step 2: Verify git status is clean**

Run: `git status`  
Expected: Working tree clean

- [ ] **Step 3: Push changes to GitHub**

Run: `git push origin main`  
Expected: Successfully pushed to `git@github.com:alirezaopmc/promux.git`

- [ ] **Step 4: Verify remote repository on GitHub**

Run: `gh repo view alirezaopmc/promux`  
Expected: Healthy public repo metadata
