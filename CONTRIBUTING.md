# Contributing to Promux

Thank you for your interest in contributing to Promux! We welcome bug reports, feature suggestions, documentation improvements, and pull requests.

## Philosophy & Architecture Guidelines

Promux is intentionally designed around these core principles:
1. **Zero External Runtime Dependencies**: All runtime functionality must rely exclusively on the Python standard library (`fcntl`, `urllib`, `argparse`, `dataclasses`, `pathlib`, `json`, etc.). External dependencies are reserved strictly for development and testing (`pytest`, `pytest-mock`).
2. **POSIX Safety & Concurrency**: All file mutations touching credentials or state must use atomic writes (`.tmp` + `os.replace`), strict file permissions (`0o600` for tokens, `0o700` for directories), and advisory file locking (`fcntl.flock`).
3. **Session Continuity**: Never overwrite or disrupt conversations, histories, or project context belonging to the host CLI (`agy`).

## Development Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/alirezaopmc/promux.git
   cd promux
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install editable package with development dependencies**:
   ```bash
   pip install -e ".[dev]"
   ```

## Running Tests

Run the full test suite using `pytest`:
```bash
pytest -v
```

Run focused tests for specific modules:
```bash
pytest tests/test_lock.py -v
pytest tests/test_storage.py -v
pytest tests/test_quota.py -v
pytest tests/test_failover.py -v
pytest tests/test_watch.py -v
pytest tests/test_cli.py -v
pytest tests/test_completion.py -v
```

### Testing Shell Completion

When making changes to completion scripts or CLI commands, verify shell completions:
```bash
# Run unit tests for completion generation and CLI command dispatch
pytest tests/test_completion.py tests/test_cli.py -k "completion" -v

# Validate bash script syntax
bash -n <(python3 -m promux.cli completion bash)

# Validate zsh script syntax (if zsh is available)
zsh -n <(python3 -m promux.cli completion zsh)
```

Before submitting a pull request, ensure all tests pass with zero warnings or failures.

## Submitting a Pull Request

1. **Fork the repository** and create a feature branch from `main`:
   ```bash
   git checkout -b feat/my-new-feature
   ```
2. **Write tests** for any new functionality or bug fixes.
3. **Keep commits clean and descriptive** following Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`).
4. **Push to your fork** and open a Pull Request against `main`.
5. Clearly describe the problem being solved, the approach taken, and the verification evidence.

## Community Code of Conduct

All contributors and participants are expected to adhere to our [Code of Conduct](CODE_OF_CONDUCT.md).
