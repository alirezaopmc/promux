# Promux

Antigravity CLI profile multiplexer and automated quota failover daemon.

`promux` provides isolated multi-account profile switching and automated quota failover (`429 RESOURCE_EXHAUSTED`) for the Antigravity CLI (`agy`).

## Features

- **Zero External Runtime Dependencies:** Pure Python standard library (`urllib`, `json`, `fcntl`, `subprocess`, `argparse`, `dataclasses`, `pathlib`).
- **Hot-Swappable Overlay:** Treats `~/.gemini/antigravity-cli/antigravity-oauth-token` as an atomic hot-swappable interface with `0600` permissions.
- **Shared Workspace & Conversation Continuity:** Sessions (`conversations/`, `brain/`, `history.jsonl`) remain untouched and shared across all accounts.
- **Dual-Loop Quota Awareness:** Proactive Cloud Code Assist REST API queries combined with a reactive log-tailing daemon (`cli.log`).
- **POSIX Concurrency Guarantees:** File locking (`fcntl.flock`) and atomic state persistence.

## Installation

Requires Python 3.10+.

```bash
pip install -e .
```

For development:
```bash
pip install -e ".[dev]"
```

## License

MIT
