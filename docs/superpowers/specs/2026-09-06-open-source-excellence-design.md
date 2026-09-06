# Promux Open Source Excellence Design

**Date:** 2026-09-06  
**Status:** Approved for implementation planning  
**Authors:** Antigravity & Alireza Opmc  

---

## 1. Objective & Scope

The goal of this design is to elevate `promux` from a functional utility to a tier-1, production-grade open-source project following industry best practices for Python CLI tools and GitHub community standards.

Key objectives:
1. **Static Analysis & Strict Typing**: Enforce code formatting, linting, and type consistency via Ruff and Mypy with zero external runtime dependencies.
2. **Robust CI/CD & Security**: Expand GitHub Actions to include linting, type-checking, matrix testing across Python 3.10–3.14 on Linux and macOS with coverage, GitHub CodeQL security analysis, and automated PyPI Trusted Publishing via OIDC.
3. **Native Shell Auto-Completion**: Implement zero-dependency shell auto-completion for `bash` and `zsh` supporting dynamic account name lookup.
4. **Enhanced Distribution & Developer Ergonomics**: Support pre-commit hooks, recommend `pipx` installation, and maintain documentation clarity.

---

## 2. Component Design & Specifications

### 2.1 Code Quality & Static Typing

#### 2.1.1 Tool Configuration (`pyproject.toml`)
- **Ruff (`[tool.ruff]`)**:
  - `line-length = 100`
  - `target-version = "py310"`
  - Linter rules: `select = ["E", "F", "W", "I", "UP", "B"]` (Pyflakes, pycodestyle, isort, pyupgrade, flake8-bugbear).
  - Formatting: standard Ruff formatter with 4-space indentation, double quotes, and space around operators.
- **Mypy (`[tool.mypy]`)**:
  - `python_version = "3.10"`
  - `strict = false` (baseline transition) with `warn_return_any = true`, `warn_unused_configs = true`, `disallow_untyped_defs = true`, `check_untyped_defs = true`, `no_implicit_optional = true`.
  - Targeted package: `promux`.
- **Coverage (`[tool.coverage.run]`)**:
  - `source = ["promux"]`
  - `omit = ["tests/*"]`

#### 2.1.2 Pre-commit Hooks (`.pre-commit-config.yaml`)
- A standard `.pre-commit-config.yaml` to run:
  - `pre-commit-hooks` (check-yaml, end-of-file-fixer, trailing-whitespace, check-merge-conflict).
  - `ruff-pre-commit` (`ruff` lint + `ruff format`).

#### 2.1.3 Dev Dependencies Update (`pyproject.toml`)
- Expand `[project.optional-dependencies].dev`:
  ```toml
  [project.optional-dependencies]
  dev = [
      "pytest>=8.0.0",
      "pytest-mock>=3.14.0",
      "pytest-cov>=5.0.0",
      "ruff>=0.5.0",
      "mypy>=1.10.0",
  ]
  ```

---

### 2.2 CI/CD, Security & Automated Release Pipelines

#### 2.2.1 Unified CI Matrix (`.github/workflows/ci.yml`)
1. **`lint` job:**
   - Runs on `ubuntu-latest` with Python 3.12.
   - Executes `ruff check src tests` and `ruff format --check src tests`.
2. **`typecheck` job:**
   - Runs on `ubuntu-latest` with Python 3.12.
   - Executes `mypy src`.
3. **`test` job:**
   - Runs matrix across:
     - OS: `[ubuntu-latest, macos-latest]`
     - Python: `["3.10", "3.11", "3.12", "3.13", "3.14"]`
   - Runs `pytest -v --cov=promux --cov-report=xml --cov-report=term`.
   - Uploads coverage reports.

#### 2.2.2 CodeQL Security Analysis (`.github/workflows/codeql.yml`)
- Runs standard GitHub CodeQL semantic security scanning on pushes/PRs targeting `main` and on a weekly cron schedule.
- Checks Python source code for security vulnerabilities, path traversal risks, and unsafe input handling.

#### 2.2.3 Automated PyPI Publishing (`.github/workflows/publish.yml`)
- Trigger: Tagged commits matching `v*` (e.g. `v0.1.0`) or GitHub Releases.
- Security: Uses GitHub Actions OpenID Connect (OIDC) via `pypa/gh-action-pypi-publish@release/v1` (PyPI Trusted Publisher model). No static API tokens stored in repository secrets.
- Steps:
  1. Check out repository.
  2. Install `build`.
  3. Build source distribution (`sdist`) and binary wheel (`bdist_wheel`).
  4. Publish to PyPI via Trusted Publisher.
  5. Attach `.tar.gz` and `.whl` artifacts to the GitHub Release.

---

### 2.3 Native Zero-Dependency Shell Completion

#### 2.3.1 CLI Subcommand: `promux completion <shell>`
- Supported shells: `bash`, `zsh`.
- Implementation: Defined in `src/promux/completion.py` and wired into `cli.py`.
- **Zero Runtime Dependencies**: The script generators are purely static string templates with standard shell logic, generating standalone completion files without requiring external libraries.

#### 2.3.2 Features & Dynamic Introspection
- **Subcommands**: `list`, `save`, `switch`, `next`, `quota`, `whoami`, `remove`, `watch`, `completion`.
- **Options & Flags**: Autocompletes `--json`, `--email`, `--reason`, `--cooldown`, `--poll-seconds`, `--help`.
- **Dynamic Account Completion**:
  - For commands that accept an account name argument (`switch`, `quota`, `remove`), the shell completion function inspects `~/.promux/accounts/` or invokes `promux list --json` to autocomplete available profile names in the terminal.

#### 2.3.3 Bash Completion Spec
- Registers completion with `complete -F _promux_completion promux`.
- Uses `COMPREPLY` and `compgen` to match available subcommands, options, and directory names from `~/.promux/accounts`.

#### 2.3.4 Zsh Completion Spec
- Uses `#compdef promux`.
- Uses Zsh `_arguments` and `_describe` commands for rich flag descriptions and account names.

---

### 2.4 User Experience & Distribution

#### 2.4.1 `pipx` Support & Installation Guide
- Document `pipx install promux` in `README.md` as the premier method for running Python CLI tools in isolated environments.
- Provide instructions for enabling shell completion:
  ```bash
  # For Bash
  eval "$(promux completion bash)"

  # For Zsh
  eval "$(promux completion zsh)"
  ```

---

## 3. Verification & Testing Plan

1. **Ruff & Mypy Conformance**:
   - Run `ruff check src tests` and resolve any lint warnings.
   - Run `ruff format --check src tests` to ensure formatting consistency.
   - Run `mypy src` and fix any type annotation discrepancies.
2. **Shell Completion Testing**:
   - Add unit tests in `tests/test_cli.py` and `tests/test_completion.py`:
     - Test `promux completion bash` produces valid bash syntax containing `_promux_completion`.
     - Test `promux completion zsh` produces valid zsh syntax containing `#compdef promux`.
     - Test `promux completion invalid` returns exit code 2 / error.
3. **Full Test Suite Execution**:
   - Run `pytest -v --cov=promux` to verify all existing and new tests pass cleanly.
4. **CI Workflow Validation**:
   - Validate YAML syntax and action schemas for all workflow files (`ci.yml`, `codeql.yml`, `publish.yml`).

---

## 4. Acceptance Criteria

- [x] All design sections approved by user.
- [ ] `pyproject.toml` configured with Ruff, Mypy, and coverage settings.
- [ ] `.pre-commit-config.yaml` added.
- [ ] GitHub Actions CI workflow updated with lint, typecheck, and test matrix with coverage.
- [ ] GitHub Actions CodeQL security analysis workflow added.
- [ ] GitHub Actions PyPI publish workflow added with OIDC trusted publisher.
- [ ] `promux completion` implemented for bash and zsh with dynamic account name completion.
- [ ] All unit and completion tests passing with 100% success rate.
- [ ] `README.md` and docs updated with `pipx` and completion documentation.
- [ ] Changes committed and pushed cleanly to GitHub repository.
