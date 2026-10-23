# Contributing to Promux

Thank you for your interest in contributing to Promux! We welcome bug reports, feature suggestions, documentation improvements, and pull requests.

## Philosophy & Architecture Guidelines

Promux is intentionally designed around these core principles:
1. **Zero External Runtime Dependencies**: Promux is compiled to a standalone static binary with zero external runtime requirements.
2. **POSIX Safety & Concurrency**: All file mutations touching credentials or state must use atomic writes (`.tmp` + `os.Rename`), strict file permissions (`0600` for tokens, `0700` for directories), and cross-platform advisory file locking (`pkg/lock`).
3. **Session Continuity**: Never overwrite or disrupt conversations, histories, or project context belonging to the host CLI (`agy`).

## Development Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/alirezaopmc/promux.git
   cd promux
   ```

2. **Verify Go installation**:
   Promux requires Go 1.24 or higher:
   ```bash
   go version
   ```

3. **Build the binary**:
   ```bash
   make build
   # Or directly:
   go build -ldflags="-s -w" -o bin/promux ./cmd/promux
   ```

4. **Install locally** (optional):
   ```bash
   make install
   # Installs to ~/.local/bin/promux
   ```

## Running Tests

Run the full test suite with the race detector enabled:
```bash
make test
# Or directly:
go test -v -race ./...
```

Run static analysis:
```bash
make vet
# Or directly:
go vet ./...
```

Run focused tests for specific packages:
```bash
go test -v -race ./pkg/lock/...
go test -v -race ./pkg/storage/...
go test -v -race ./pkg/quota/...
go test -v -race ./pkg/failover/...
go test -v -race ./pkg/watcher/...
go test -v -race ./pkg/cmd/...
```

### Testing Shell Completion

When making changes to commands or flags, verify shell completion generation:
```bash
# Validate bash completion script syntax
bash -n <(./bin/promux completion bash)

# Validate zsh completion script syntax (if zsh is available)
zsh -n <(./bin/promux completion zsh)
```

Before submitting a pull request, ensure all tests pass with zero warnings, errors, or data races.

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
