# Promux

[![CI](https://github.com/alirezaopmc/promux/actions/workflows/ci.yml/badge.svg)](https://github.com/alirezaopmc/promux/actions/workflows/ci.yml)
[![Go Reference](https://pkg.go.dev/badge/github.com/alirezaopmc/promux.svg)](https://pkg.go.dev/github.com/alirezaopmc/promux)
[![Binary Size](https://img.shields.io/badge/binary_size-6.8_MB-blue.svg)](#-high-performance-native-engine)
[![Startup Latency](https://img.shields.io/badge/startup-~5ms-brightgreen.svg)](#-high-performance-native-engine)
[![Platform](https://img.shields.io/badge/platform-Linux_%7C_macOS_%7C_Windows-lightgrey.svg)](#-cross-platform-architecture)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

High-performance, zero-runtime-dependency CLI profile multiplexer and autonomous quota failover engine for AI developer CLI tools.

Statically compiled into a standalone **6.8 MB binary** with **~5ms startup latency**, `promux` provides multi-account credential management, real-time quota tracking, and autonomous PTY execution with automatic quota failover and `'continue'` injection for **Google Antigravity CLI (`agy`)**, **Anthropic Claude Code (`claude`)**, **OpenAI Codex CLI (`codex`)**, **Cursor CLI (`cursor`)**, and more.

Operating as a zero-intrusion filesystem overlay, `promux` keeps your active conversation context, session history, memory, and workspace artifacts completely intact while dynamically hot-swapping authentication tokens and tracking API limits in the background.

---

## ⚡ Highlights

- **Standalone Single Binary (6.8 MB):** Statically compiled Go executable with zero external runtime dependencies. No Python interpreter, Node.js runtime, or shared C libraries required.
- **Ultra-Low Latency (~5ms Startup):** Sub-millisecond locking and instantaneous CLI dispatch—ideal for interactive shell sessions, terminal wrappers, and high-frequency background daemons.
- **Autonomous PTY Runner (`promux run`):** Spawns your AI CLI tool inside an interactive pseudo-terminal, scans terminal output in real time for quota limits (`429 RESOURCE_EXHAUSTED`, `Individual quota reached`), atomically hot-swaps to an eligible standby profile, and automatically injects `'continue'` keystrokes so work resumes without human intervention.
- **Multi-Tool Profile Management:** Unified profile vault architecture managing credentials across Antigravity CLI (`agy`), Claude Code (`claude`), Codex CLI (`codex`), and Cursor CLI (`cursor`).
- **Quota-Aware Monitoring:** Direct REST client queries to Google Cloud Code Assist (`/v1internal:retrieveUserQuotaSummary`) calculating 5-hour and weekly remaining quotas with live, color-coded relative countdowns (`98.0% (resets in 2h 15m left)`).
- **Weekly Quota Priming Engine (`promux prime`):** Eliminates Google's "floating reset delay" by detecting accounts that reached weekly reset and executing a minimal headless prompt turn under lock to fix the next 7-day countdown (`promux prime`, `promux quota --all --prime`, and `promux watch --auto-prime`).
- **Cross-Platform Support (Linux, macOS, Windows):** Native OS-level advisory file locking (`syscall.Flock` on POSIX, `LockFileEx` on Windows) and platform-specific terminal handling.
- **Shared Workspace & Conversation Continuity:** Workspace sessions, conversation history, and cached project state remain 100% intact across profile rotations.
- **Dual-Loop Failover Protection:**
  - *Proactive Loop:* Live API quota inspection to anticipate exhaustion and rotate accounts based on available capacity.
  - *Reactive Loop:* Real-time background daemon (`promux watch`) and interactive runner (`promux run`) monitoring logs and terminal output for instant failover upon hitting quota walls.
- **Full Backward Compatibility:** 100% drop-in compatibility with existing `~/.promux` vaults, configuration files, and `state.json` formats from the original Python implementation.

---

## 🚀 Autonomous PTY Runner (`promux run`)

The `promux run` command launches developer AI tools within an autonomous pseudo-terminal (PTY) wrapper. It intercepts and visualizes full ANSI interactive terminal output while continuously monitoring for rate limits and quota walls.

```bash
promux run agy [args...]
```

```text
┌────────────────────────────────────────────────────────────────────────┐
│                          User Terminal                                 │
└───────────────────────────────────▲────────────────────────────────────┘
                                    │ PTY Passthrough & Terminal Resizing
┌───────────────────────────────────▼────────────────────────────────────┐
│                    Promux Autonomous PTY Runner                        │
│                                                                        │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │ OutputScanner: Scans stream for RESOURCE_EXHAUSTED / Quota     │   │
│   └───────────────────────────────┬────────────────────────────────┘   │
└───────────────────────────────────┼────────────────────────────────────┘
                                    │ Quota Wall Detected!
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        Failover Engine                                 │
│  1. Marks current profile in Cooldown (parsed from reset hint)         │
│  2. Selects next eligible standby profile (LRU / Quota-aware)          │
│  3. Atomically swaps OAuth credentials under file mutex                │
│  4. Injects "continue\r\n" keystroke into child process stdin          │
└────────────────────────────────────────────────────────────────────────┘
```

When a quota wall is encountered during long-running tasks, `promux run`:
1. Detects the exhaustion pattern and extracts the reset hint (e.g. `Resets in ~2h 15m`).
2. Rotates active credentials to the next eligible standby account atomically.
3. Automatically injects `"continue\r\n"` into the child process after a 500ms stabilization window.
4. Your CLI session seamlessly continues generating code without dropping context or requiring you to restart!

*To disable automated keystroke injection, pass the `--no-auto-continue` flag.*

---

## 🔄 Weekly Quota Priming Strategy

Google Antigravity CLI (`agy`) and Google Cloud Code Assist enforce two rolling quota tiers:
1. **5-Hour Rolling Window:** High-frequency prompt limit refreshed continuously every 5 hours.
2. **Weekly Rolling Window:** 7-day (168-hour) quota allocation for larger model capacity.

### The Problem: Floating Reset Delay
When an account's weekly quota period elapses, the quota capacity returns to 100%. However, Google's backend **does not start the next 7-day reset countdown until that account sends its first new prompt turn**.

If a standby profile resets on Monday but sits unused until Wednesday, its next reset countdown doesn't begin until Wednesday—effectively wasting 2 full days of reset cycle interval. Across multiple accounts, this causes significant quota cycle loss over time.

### The Solution: Automated & On-Demand Priming
Promux introduces **Quota Priming**:
- **Automatic Detection:** Identifies accounts whose weekly reset timestamp has elapsed, or whose quota is floating at 100% capacity without an active countdown.
- **Minimal Headless Turn:** Safely activates the account by dispatching a minimal prompt turn (`agy -p "ping" --model gemini-2.5-flash`) under advisory lock (`manager.lock`).
- **Instant Reset Pinning:** Immediately locks in the new 7-day countdown (`6d 23h`) without human intervention or token waste.
- **Fail-Safe Token Swapping:** If priming an inactive standby profile, Promux creates a temporary backup of the active live token, swaps in the target vault token, executes the turn, syncs back updated tokens, and guarantees atomic rollback to the original active token upon completion or error.

Priming can be triggered on demand via `promux prime`, during quota inspection via `promux quota --all --prime`, or continuously in the background using `promux watch --auto-prime`.

---

## 📦 Installation

### Option 1: Direct Install with Go (Recommended)

If you have Go 1.21+ installed, install the latest compiled binary directly:

```bash
go install github.com/alirezaopmc/promux/cmd/promux@latest
```

Ensure your Go bin path is in your shell's `$PATH`:
```bash
export PATH="$(go env GOPATH)/bin:$PATH"
```

### Option 2: Build from Source via Makefile

Clone the repository and compile the native binary using the included `Makefile`:

```bash
git clone https://github.com/alirezaopmc/promux.git
cd promux

# Build standalone binary in bin/promux (CGO_ENABLED=0, stripped symbols)
make build

# Install to ~/.local/bin/promux
make install
```

Verify your installation:
```bash
promux version
```

### Option 3: Precompiled Binaries

Download precompiled standalone binaries for **Linux (x86_64, arm64)**, **macOS (Intel, Apple Silicon)**, and **Windows (x64)** from the [Releases](https://github.com/alirezaopmc/promux/releases) page.

```bash
# Example for Linux x86_64
curl -L -o promux https://github.com/alirezaopmc/promux/releases/latest/download/promux-linux-amd64
chmod +x promux
mv promux ~/.local/bin/
```

### Option 4: Python Legacy Installation (Historical Reference)

Promux was originally authored as a zero-dependency Python tool and has since evolved into this high-performance Go native binary. For users maintaining legacy Python environments, the Python package remains compatible:

```bash
pipx install promux
# Or for local development:
pip install -e ".[dev]"
```

*Note: The Go binary is a 100% drop-in replacement that natively reads, writes, and locks existing `~/.promux` accounts and `state.json` without requiring any migration.*

---

## 🎯 Quick Start

### 1. View Supported AI Tools
```bash
promux tools
```
Output:
```text
  TOOL    NAME                    STATUS     CAPABILITIES
✓ agy     Google Antigravity CLI  active     [vault quota watch run]
  claude  Anthropic Claude Code   supported  [vault watch]
  codex   OpenAI Codex CLI        supported  [vault]
  cursor  Cursor CLI              supported  [vault]
```

### 2. Save Credentials into the Vault
Log in with your primary account in `agy`, then save it to the vault:
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
  PROFILE   STATUS   LAST USED         EMAIL                 PLAN
* main      active   2026-10-16 01:05  developer1@gmail.com  STANDARD
  backup1   standby  2026-10-15 22:10  developer2@gmail.com  STANDARD
```

### 4. Check Real Quota Status with Inline Reset Timers
Query live Cloud Code Assist quota buckets (5-hour and weekly windows) with real-time countdowns:
```bash
promux quota
```
Output:
```text
Quota Summary for Profile: main

Gemini Models:
  • 5-Hour Window: 96.1% (resets in 2h 15m)
  • Weekly Window: 82.0% (resets in 3d 4h)

Third-Party Models:
  • 5-Hour Window: 100.0%
  • Weekly Window: 31.7% (resets in 4d 12h)
```

### 5. Run Antigravity with Autonomous Failover
Launch your session using the autonomous PTY runner:
```bash
promux run agy
```
When a quota limit occurs, Promux rotates to `backup1` in the background and sends `continue` to keep the agent working.

### 6. Manually Switch or Rotate Profiles
```bash
# Switch directly to a profile
promux switch backup1

# Rotate to the least-recently used eligible standby account
promux next
```

---

## 🛠️ Multi-Tool Architecture

`promux` provides a consistent interface across different AI coding CLIs.

### Tool Selection
You can target any supported tool using the `-t` / `--tool` flag:

```bash
# Manage Claude Code profiles
promux -t claude list
promux -t claude switch personal

# Manage Cursor profiles
promux -t cursor list
promux -t cursor save work
```

When no `-t` flag is specified, `promux` defaults to `agy` (Google Antigravity CLI) for seamless everyday developer experience.

---

## 📐 Architecture & Filesystem Layout

Promux acts as a non-intrusive filesystem overlay:

```
~/.promux/
├── cache/
│   └── quota.json                     # Cached quota responses with TTL
├── tools/                             # Uniform scoped storage per developer tool
│   ├── agy/                           # Antigravity CLI tool storage
│   │   ├── accounts/
│   │   │   └── <profile>/
│   │   │       └── antigravity-oauth-token  (chmod 0600)
│   │   ├── state.json                 # Atomic profile state & metadata
│   │   └── manager.lock               # Cross-process advisory lock
│   ├── claude/
│   │   ├── accounts/
│   │   ├── state.json
│   │   └── manager.lock
│   ├── codex/
│   └── cursor/
│
├── accounts/                          # Legacy fallback paths (100% backward-compat)
├── state.json
└── manager.lock

~/.gemini/antigravity-cli/             # Antigravity CLI runtime (unmodified)
├── antigravity-oauth-token            (active live token, chmod 0600)
├── cli.log                            (monitored by watcher daemon)
├── log/                               (session logs monitored by watcher)
└── conversations/, brain/, ...        (shared session continuity)
```

All credentials are saved with strict `0600` permissions. File mutations are conducted using atomic file replacements (`.tmp` + rename) guarded by OS-level advisory file locks.

---

## 📖 CLI Command Reference

`promux` supports human-readable terminal output and structured JSON via `--json`.

### `promux run <command> [args...]`
Runs a developer CLI command in an autonomous pseudo-terminal (PTY) with continuous output scanning, automatic quota failover, and `'continue'` keystroke injection.
- `--no-auto-continue`: Disable automatic keystroke injection upon quota failover.

```bash
# Run Antigravity CLI autonomously
promux run agy

# Run with subcommands or arguments
promux run agy --resume

# Run Claude Code
promux run -t claude claude
```

### `promux prime [account]`
Primes weekly quota reset countdowns by detecting accounts whose weekly quota has reset (or is floating at 100% capacity) and executing a minimal headless prompt turn under lock to fix the new 7-day reset countdown.
- `-n, --dry-run`: Check eligibility and list accounts without sending prompt tokens.
- `-f, --force`: Force priming even if an active countdown is already ticking.
- `--json`: Output machine-readable JSON records.

```bash
# Prime all eligible vaulted profiles that have reset or have floating quota
promux prime

# Check which profiles are eligible without sending tokens
promux prime --dry-run

# Prime a specific profile
promux prime personal

# Force priming regardless of countdown status
promux prime personal --force

# Machine-readable JSON output
promux prime --json
```

### `promux list`
Lists all saved account profiles in the vault, their current status (`active`, `standby`, `cooldown`, `disabled`), email, last-used timestamp, and plan tier.

```bash
promux list
promux -t claude list
promux list --json
```

### `promux save <name>`
Saves the currently active credentials from the tool's runtime directory into the vault.
- `--email <email>`: Optional email identifier.
- `--project <id>`: Cloud companion project ID.
- `--plan <type>`: Plan type (e.g. `STANDARD`, `PRO`).

```bash
promux save personal --email me@example.com
promux save work --email team@company.com --plan PRO
```

### `promux switch <name>`
Hot-swaps the active credentials to the specified profile atomically under a file lock.

```bash
promux switch personal
promux -t claude switch work
```

### `promux next`
Rotates to the next eligible standby profile based on least-recently used (LRU) order. Automatically places the departing profile into a cooldown window.
- `--reason <string>`: Reason recorded for the failover rotation.
- `--cooldown <minutes>`: Cooldown duration in minutes (default: 60).

```bash
promux next
promux next --reason "quota reached" --cooldown 120
```

### `promux whoami`
Displays details of the currently active profile, including name, email, and plan tier.

```bash
promux whoami
promux whoami --json
```

### `promux quota [account]`
Inspects Cloud Code Assist quota limits and rolling window reset countdowns. When queried without an argument, checks the active profile; when queried with `--all`, lists all vaulted accounts in a concurrent overview table.
- `-A, --all`: Query and display quota for all accounts in the vault concurrently.
- `-a, --account <name>`: Target a specific account profile.
- `-p, --prime`: Prime accounts that have reset weekly quota before displaying.
- `-r, --refresh`: Bypass the local quota cache and query Google Cloud Code Assist live.
- `--json`: Output machine-readable JSON (single object or array of all accounts).

```bash
# Check active profile quota
promux quota

# List all account quotas together in an overview table
promux quota --all
promux quota all

# Check quota for a specific profile
promux quota personal

# Bypass local cache and force live inspection
promux quota --all --refresh

# Prime eligible profiles and display updated quota overview
promux quota --all --prime

# Prime and check quota for a specific profile
promux quota personal --prime

# Export all quotas as JSON
promux quota --all --json
```

### `promux refresh [account]`
Forces renewal of OAuth credentials for the active or specified profile using native OAuth token endpoints.
- `-f, --force`: Force refresh even if the token has not reached its expiry buffer.

```bash
promux refresh
promux refresh backup1 --force
```

### `promux watch`
Starts the real-time background log watcher daemon in the foreground, tailing log files for quota errors, proactively renewing expiring OAuth tokens, and executing instant hot failovers.
- `--auto-prime`: Automatically prime weekly quota resets in the background as soon as their reset timestamp passes or floating capacity is detected.

```bash
promux watch

# Start watcher with background weekly quota auto-priming
promux watch --auto-prime
```

### `promux tools [list]`
Lists all registered developer CLI tools, status, and supported capabilities.

```bash
promux tools
promux tools --json
```

### `promux remove <name>`
Removes a saved profile from the vault and deletes its associated credential files.

```bash
promux remove old-account
```

### `promux completion <shell>`
Generates native shell completion scripts for `bash`, `zsh`, `fish`, or `powershell`.

```bash
# Bash
source <(promux completion bash)

# Zsh
source <(promux completion zsh)
```

### `promux version`
Prints the compiled Promux CLI version, target architecture, and runtime info.

```bash
promux version
promux version --json
```

---

## ⚡ High-Performance Native Engine

Promux was re-engineered in Go to eliminate runtime startup lag and deliver rock-solid reliability across operating systems.

| Metric | Promux (Go Native) | Python Implementation | Node.js / TypeScript |
|---|---|---|---|
| **Startup Latency** | **~5 ms** | ~85 ms | ~180 ms |
| **Binary Footprint** | **6.8 MB** (single binary) | Requires Python 3.10+ runtime | Requires Node.js + `node_modules` |
| **Dependencies** | **Zero runtime dependencies** | Python standard library | NPM package dependencies |
| **Locking Mechanism** | OS-level `flock` / `LockFileEx` | POSIX `fcntl` / `msvcrt` | Platform-dependent file locking |
| **Execution Wrapper** | Native Unix PTY / Windows Pipes | Subprocess pipe interception | Node child_process wrapper |

---

## 🌐 Cross-Platform Architecture

Promux is fully engineered for cross-platform portability:

- **Linux & macOS (POSIX):**
  - Uses native `syscall.Flock` for robust process-level concurrency control.
  - Interactive PTY allocated via `github.com/creack/pty` with full terminal raw mode and `SIGWINCH` resize handling.
  - Inode and device ID file tracking for seamless log rotation detection.
- **Windows:**
  - Uses native Windows API locking via `syscall.LockFileEx` and `syscall.UnlockFileEx`.
  - Stdin/stdout pipe relays with Windows-compatible scanner hooks.
  - Path normalization respecting Windows user profiles (`%USERPROFILE%`).

---

## 🔄 Background Daemon Setup (Systemd)

To run `promux watch` automatically in the background across reboots on Linux:

1. Create `~/.config/systemd/user/promux.service`:
```ini
[Unit]
Description=Promux AI CLI Quota Failover Daemon
After=network.target

[Service]
Type=simple
# Pass --auto-prime to automatically prime weekly quota resets in the background
ExecStart=%h/.local/bin/promux watch --auto-prime
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

2. Enable and start the service:
```bash
systemctl --user daemon-reload
systemctl --user enable --now promux.service
```

3. Check service status:
```bash
systemctl --user status promux.service
```

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PROMUX_HOME` | `~/.promux` | Root directory for Promux vault accounts, cache, and state |
| `PROMUX_GEMINI_HOME` | `~/.gemini/antigravity-cli` | Root directory for Antigravity CLI live tokens and logs |
| `PROMUX_QUOTA_CACHE_TTL` | `300` | Quota cache time-to-live in seconds (5 minutes) |
| `NO_COLOR` | _(unset)_ | When set, disables ANSI color styling ([no-color.org](https://no-color.org/)) |

---

## 🧪 Testing & Development

### Go Test Suite (Primary)
Run all Go unit, integration, and race detection tests:
```bash
make test
# Or directly:
go test -v -race ./...
```

### Python Legacy Test Suite (Validation)
To verify backwards compatibility with the Python reference implementation:
```bash
pytest -v
ruff check src tests
mypy src
```

---

## 🤝 Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines, code style standards, and pull request workflows.
Please also review our [Code of Conduct](CODE_OF_CONDUCT.md).

---

## 🔒 Security

To report security vulnerabilities or concerns regarding credential and token handling, please see our [Security Policy](SECURITY.md).

---

## 📄 License

This project is licensed under the terms of the [MIT License](LICENSE).
