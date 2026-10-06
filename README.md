# Promux

[![CI](https://github.com/alirezaopmc/promux/actions/workflows/ci.yml/badge.svg)](https://github.com/alirezaopmc/promux/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Code Style: PEP 8](https://img.shields.io/badge/code%20style-PEP%208-green.svg)](https://www.python.org/dev/peps/pep-0008/)

Universal developer CLI profile multiplexer and automated quota failover daemon.

`promux` provides isolated multi-account profile switching and automated quota failover for AI developer CLI tools including **Antigravity CLI (`agy`)**, **Claude Code (`claude`)**, **Codex CLI (`codex`)**, **Cursor CLI (`cursor`)**, and more.

Operating as a zero-intrusion filesystem overlay, `promux` keeps your active conversation context, session history, memory, and project artifacts completely intact while dynamically hot-swapping authentication tokens and tracking model API quotas.

---

## Key Features

- **Zero External Runtime Dependencies:** Built strictly using the Python 3.10+ standard library (`fcntl`, `urllib`, `argparse`, `dataclasses`, `datetime`, `pathlib`, `json`).
- **Multi-Tool Architecture:** Unified tool-first CLI grammar (`promux <tool> <command>`) with pluggable adapters and 100% backward compatibility with top-level commands defaulting to `agy`.
- **Hot-Swappable Overlay:** Atomically manages authentication tokens with `0600` permissions. Developer CLIs remain completely unaware of underlying account changes.
- **Shared Workspace & Conversation Continuity:** Workspace sessions, conversation history, and cached project state remain intact across profile switches.
- **Redesigned Quota & Reset Timers:** Real-time quota tracking with inline relative reset countdowns (`98.0% (2h 15m)`) directly after percentages for both 5-hour and weekly windows.
- **Dual-Loop Failover Awareness (Antigravity):**
  - **Proactive:** Direct REST client queries to Google Cloud Code Assist (`/v1internal:retrieveUserQuotaSummary`) for 5-hour and weekly remaining quotas across Gemini and Claude/GPT model tiers.
  - **Reactive:** Lightweight daemon tails `cli.log` and session logs to detect quota exhaustion events (`Individual quota reached`, `RESOURCE_EXHAUSTED 429`) and reset hints (`Resets in ~Xh Ym`) in real time.
- **POSIX Concurrency Guarantees:** File locking (`fcntl.flock`) and atomic file replacement (`.tmp` + `os.replace`) prevent race conditions between CLI commands, background daemons, and CLI operations.
- **LRU Standby Failover:** Intelligently rotates to the least-recently used eligible standby account when the active profile is exhausted, placing exhausted accounts into a temporary cooldown window.
- **Smart Quota-Aware Switching:** Automated candidate selection via `promux switch --smart` querying live Cloud Code Assist quotas across all standby accounts, ranking by highest available quota (5-hour and weekly buckets), and conditionally applying cooldowns only if the departing profile is exhausted.
- **Two-Tier Resilient Token Renewal:** Direct native Google OAuth refresh using official Antigravity client credentials with automatic token rotation; seamless headless `agy` fallback under file mutex. Proactively and automatically renews tokens during `promux switch`, `promux quota`, `promux watch`, and `promux refresh`.

---

## Multi-Tool Support

`promux` organizes profiles per developer tool, allowing developers to manage credentials across different AI coding CLIs with a consistent interface.

To inspect registered tool adapters and capabilities:
```bash
promux tools
```
Output:
```text
TOOL      NAME                  ACTIVE PROFILE  CAPABILITIES
-------------------------------------------------------------------------
agy       Antigravity CLI       work            vault, quota, watch, refresh
claude    Claude Code           -               vault (scaffolded)
codex     Codex CLI             -               vault (scaffolded)
cursor    Cursor CLI            -               vault (scaffolded)
```

### Invocations
- **Tool-First Mode:** `promux <tool> <command> [args...]`
  ```bash
  promux agy list
  promux agy quota
  promux claude switch personal
  promux cursor list
  ```
- **Backward-Compatible Default Mode:** `promux <command> [args...]`
  When invoked without an explicit tool prefix, `promux` defaults to `agy`:
  ```bash
  promux list       # equivalent to: promux agy list
  promux quota      # equivalent to: promux agy quota
  promux switch dev # equivalent to: promux agy switch dev
  ```

---

## Architecture & Filesystem Layout

```
~/.promux/
├── accounts/                          # Antigravity accounts (backward-compatible)
│   ├── main/
│   │   └── antigravity-oauth-token    (chmod 0600)
│   └── backup1/
│       └── antigravity-oauth-token    (chmod 0600)
├── state.json                         # Atomic state & metadata (agy, backward-compat)
├── manager.lock                       # POSIX fcntl advisory lock (agy, backward-compat)
└── tools/                             # Uniform scoped storage per developer tool
    ├── agy/                           # Antigravity CLI tool storage
    │   ├── accounts/
    │   │   └── <profile>/
    │   │       └── antigravity-oauth-token  (chmod 0600)
    │   ├── state.json
    │   └── manager.lock
    ├── claude/
    │   ├── accounts/
    │   ├── state.json
    │   └── manager.lock
    ├── codex/
    │   ├── accounts/
    │   ├── state.json
    │   └── manager.lock
    └── cursor/
        ├── accounts/
        ├── state.json
        └── manager.lock

~/.gemini/antigravity-cli/             # Antigravity CLI runtime
├── antigravity-oauth-token            (active live token, chmod 0600)
├── cli.log                            (monitored by watcher daemon)
├── log/                               (session logs monitored by watcher)
└── conversations/, brain/, ...        (shared session continuity)
```

All tool data lives under `~/.promux/tools/<tool>/`, providing a uniform layout. The `agy` tool follows the same structure as other tools under `tools/agy/`. The legacy `~/.promux/accounts/`, `~/.promux/state.json`, and `~/.promux/manager.lock` paths are preserved for backward compatibility.

---

## Installation

### Prerequisites
- Linux or macOS
- Python 3.10 or higher
- Antigravity CLI (`agy`) or other supported developer CLIs

### Recommended: Install via pipx
For an isolated CLI installation that is automatically linked into your `$PATH`:
```bash
pipx install promux
```

Or install directly from the Git repository:
```bash
pipx install git+https://github.com/alirezaopmc/promux.git
```

### Install in Editable Mode
```bash
pip install -e . --break-system-packages
```

### Install with Development Dependencies
```bash
pip install -e ".[dev]" --break-system-packages
```

Ensure `~/.local/bin` is in your shell `PATH`:
```bash
export PATH="$HOME/.local/bin:$PATH"
```

Verify installation:
```bash
promux --help
```

---

## Quick Start

### 1. View Supported Tools
```bash
promux tools
```

### 2. Save Existing Credentials into the Vault
Log into your primary account via `agy`, then save it to the vault:
```bash
promux save main --email developer1@gmail.com
```

Next, log into your backup account and save it:
```bash
promux save backup1 --email developer2@gmail.com
```

### 3. Inspect Vault Profiles
```bash
promux list
```
Output:
```text
ACTIVE  NAME              STATE       EMAIL                           COOLDOWN                LAST USED
--------------------------------------------------------------------------------------------------------------
*       main              standby     developer1@gmail.com            -                       2026-09-03T22:11:16+00:00
        backup1           standby     developer2@gmail.com            -                       2026-09-03T21:45:00+00:00
```

### 4. Check Real Quota Status with Inline Reset Timers
Query live Cloud Code Assist quota buckets (5-hour and weekly windows) across all accounts in your vault simultaneously:
```bash
promux quota
```
Output:
```text
ACTIVE  PROFILE           GEMINI (5H)          GEMINI (WK)          CLAUDE (5H)          CLAUDE (WK)         
---------------------------------------------------------------------------------------------------------
*       main              96.1% (2h 15m)       82.0% (3d 4h)        100.0% (-)           31.7% (4d 12h)      
        backup1           100.0% (-)           95.0% (5d 1h)        80.0% (1h 10m)       60.0% (2d 6h)       
```

To view the detailed breakdown and exact reset timestamps for an individual account, specify the profile name:
```bash
promux quota main
```
Output:
```text
Quota for account 'main' (project: aicode-consumers) [ACTIVE]:

MODEL GROUP              WINDOW     REMAINING & RESET
--------------------------------------------------------------------------------
Gemini Models            5h         96.1% (2h 15m left - 16:58 UTC)
Gemini Models            weekly     82.0% (3d 4h left - Sep 13 10:15 UTC)
Claude & GPT Models      5h         100.0% (-)
Claude & GPT Models      weekly     31.7% (4d 12h left - Sep 13 10:15 UTC)
```

### 5. Manually Switch Profiles
Hot-swap the active token at any time:
```bash
promux switch backup1
```

### 6. Smart Quota-Aware Switching
Automatically evaluate all standby accounts and switch to the profile with the highest remaining quota for Gemini or third-party models (Claude/GPT):
```bash
# Auto-switch to standby account with highest Gemini quota
promux switch --smart

# Auto-switch targeting Claude & GPT quota
promux switch --smart --model claude
```

---

## CLI Command Reference

`promux` supports human-readable ASCII tables and structured JSON outputs (via `--json`). The `--json` flag can be placed before or after any subcommand.

### `promux tools [list]`
Lists all registered tool adapters, active profiles, and capability support.

```bash
promux tools
promux tools --json
```

### `promux list`
List all accounts in the vault for the selected tool, displaying active status (`*`), account state (`active`, `standby`, `cooldown`, `disabled`), email, cooldown timestamp, and last-used timestamp.

```bash
promux list
promux claude list
promux list --json
```

### `promux save <name> [--email EMAIL]`
Saves the currently active credentials into the vault under `<name>`.

```bash
promux save personal
promux save work --email team@company.com
promux cursor save work
```

### `promux switch [<name>] [--smart] [--model {gemini,claude,gpt}]`
Hot-swaps the active profile credentials with atomic file replacement and `0600` permissions under an advisory lock.

- **Manual switch:** Specify `<name>` to switch directly to that profile.
- **Smart quota-aware switch:** Pass `--smart` to query live Cloud Code Assist API quotas across all eligible standby profiles and automatically switch to the candidate with the highest remaining quota.
  - `--model {gemini,claude,gpt}`: Target model tier to evaluate (default: `gemini`). Specifying `claude` or `gpt` evaluates the shared third-party model quota bucket.
  - **Ranking metric:** Prioritizes highest 5-hour available quota fraction, then weekly quota fraction, with least-recently used (LRU) tie-breaking.
  - **Conditional cooldown:** Quarantines the departing active profile only if its quota is exhausted; otherwise leaves it clean in `standby`.

```bash
# Manual profile switch
promux switch personal
promux claude switch work

# Smart switch targeting Gemini quota (default)
promux switch --smart

# Smart switch targeting Claude / GPT quota
promux switch --smart --model claude

# JSON output
promux switch --smart --json
```

Output:
```text
Switched active profile to 'backup1' (gemini 5h: 100.0%, weekly: 95.0%).
```

### `promux next [--reason REASON] [--cooldown COOLDOWN]`
Rotates to the next eligible standby profile based on least-recently used (LRU) order. Automatically applies a cooldown (default: 60 minutes) to the departing profile.

```bash
promux next
promux next --reason "manual rotation" --cooldown 120
```

### `promux quota [name]`
Queries Google Cloud Code Assist for live quota fractions and reset countdowns.
- **Default (no profile name):** Displays an overview table comparing 5-hour and weekly remaining quotas with inline countdowns (`% (countdown)`) across all accounts in the vault.
- **Single profile (`promux quota <name>`):** Displays detailed model group views (Gemini vs Claude & GPT across 5-hour and weekly windows) with relative countdown and UTC reset time.

```bash
# Multi-profile overview table
promux quota

# Multi-profile overview JSON array (includes 5h_reset_relative and weekly_reset_relative)
promux quota --json

# Single-account detailed breakdown
promux quota backup1

# Single-account detailed JSON object
promux quota backup1 --json
```

### `promux whoami`
Displays details of the active profile, including account name, email, OAuth token expiry, state, project ID, and plan type.

```bash
promux whoami
promux whoami --json
```

### `promux refresh [name] [--force]`
Inspects and refreshes OAuth access tokens across all vault accounts (or a specific account).
- Renews expired or near-expiry tokens using Tier 1 native OAuth refresh, falling back to Tier 2 headless `agy`.
- Handles Google OAuth refresh token rotation and syncs active account tokens atomically.
- `--force`: Force renewal even if the token has not yet reached its expiration buffer.

```bash
# Refresh all accounts in vault
promux refresh

# Refresh all accounts in JSON format
promux refresh --json

# Force refresh a single account
promux refresh backup1 --force
```

### `promux remove <name>`
Removes an account from the vault and cleans up its directory.

```bash
promux remove old-account
```

### `promux watch [--poll-seconds SECONDS] [--cooldown MINUTES]`
Starts the reactive failover daemon in the foreground, streaming log files for quota error patterns and performing automated hot failover to eligible standby accounts.

```bash
promux watch
promux watch --poll-seconds 0.5 --cooldown 90
```

### `promux completion <shell>`
Generates native shell auto-completion scripts for `bash` or `zsh`.

```bash
# Bash
source <(promux completion bash)

# Zsh
source <(promux completion zsh)
```

### `promux version`
Prints the promux version, Python runtime version, platform, and supported tools.

```bash
promux version           # human-readable
promux version --json    # machine-readable JSON
promux -V                # shorthand
promux --version         # shorthand
```

Output:
```text
promux 0.4.0 (Python 3.14.4, Linux-x86_64)
Supported tools: agy (default), claude, codex, cursor
```

JSON output:
```json
{
  "version": "0.4.0",
  "python": "3.14.4",
  "platform": "Linux-x86_64",
  "tools": ["agy", "claude", "codex", "cursor"],
  "default_tool": "agy"
}
```

### `promux help [command]`
Displays grouped, color-accented help. Pass a command name for per-subcommand help.

```bash
promux help              # grouped top-level help
promux help quota        # help for the quota subcommand
promux help switch       # help for the switch subcommand
promux <tool> help       # help scoped to a tool (e.g. promux claude help)
```

---

## Color Output & NO_COLOR

`promux` emits ANSI color accents in help output by default. Colors are **automatically disabled** when:
- Output is piped or redirected (non-TTY stdout)
- The `NO_COLOR` environment variable is set (any value)
- `TERM=dumb` is set
- The `--no-color` flag is passed

```bash
NO_COLOR=1 promux help       # force plain text
promux help --no-color       # flag equivalent
promux version --no-color    # also works on version
```

The `--no-color` flag is available on all top-level commands.

---

## Background Daemon Setup (Systemd)

To run `promux watch` automatically in the background across reboots, create a systemd user service:

1. Create directory:
```bash
mkdir -p ~/.config/systemd/user
```

2. Create `~/.config/systemd/user/promux.service`:
```ini
[Unit]
Description=Promux Antigravity Quota Failover Daemon
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/promux watch --poll-seconds 1.0
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

3. Enable and start the service:
```bash
systemctl --user daemon-reload
systemctl --user enable --now promux.service
```

4. Check status and live logs:
```bash
systemctl --user status promux.service
journalctl --user -u promux.service -f
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PROMUX_HOME` | `~/.promux` | Root directory for promux accounts vault, state file, and lock |
| `PROMUX_GEMINI_HOME` | `~/.gemini/antigravity-cli` | Root directory for Antigravity CLI live token and logs |
| `NO_COLOR` | _(unset)_ | When set (any value), disables all ANSI color output ([no-color.org](https://no-color.org/)) |
| `TERM` | _(system)_ | When set to `dumb`, disables ANSI color output |

---

## Testing

Run full test suite:
```bash
pytest -v
```

Run specific test modules:
```bash
pytest tests/test_adapters.py -v
pytest tests/test_formatters.py -v
pytest tests/test_cli.py -v
pytest tests/test_failover.py -v
pytest tests/test_watch.py -v
pytest tests/test_storage.py -v
pytest tests/test_quota.py -v
pytest tests/test_lock.py -v
pytest tests/test_completion.py -v
```

---

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, coding conventions, and pull request workflows.
Please also review our [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Security

To report security vulnerabilities or concerns regarding credential and token handling, please see our [Security Policy](SECURITY.md).

---

## License

This project is licensed under the terms of the [MIT License](LICENSE).
