# Promux

Antigravity CLI profile multiplexer and automated quota failover daemon.

`promux` provides isolated multi-account profile switching and automated quota failover (`429 RESOURCE_EXHAUSTED`) for the Antigravity CLI (`agy`).

Operating as a zero-intrusion filesystem overlay, `promux` keeps your active conversation context, session history, memory, and project artifacts completely intact while dynamically hot-swapping authentication tokens and tracking Cloud Code Assist API quotas.

---

## Key Features

- **Zero External Runtime Dependencies:** Built strictly using the Python 3.10+ standard library (`fcntl`, `urllib`, `argparse`, `dataclasses`, `pathlib`, `json`).
- **Hot-Swappable Overlay:** Treats `~/.gemini/antigravity-cli/antigravity-oauth-token` as an atomic hot-swappable interface with `0600` permissions. The Antigravity CLI remains completely unaware of underlying account changes.
- **Shared Workspace & Conversation Continuity:** Workspace sessions, conversation history (`conversations/`, `brain/`, `history.jsonl`), and cached state remain shared across all accounts.
- **Dual-Loop Quota Awareness:**
  - **Proactive:** Direct REST client queries to Google Cloud Code Assist (`/v1internal:retrieveUserQuotaSummary`) for 5-hour and weekly remaining quotas across Gemini and Claude/GPT model tiers.
  - **Reactive:** Lightweight daemon tails `cli.log` and session logs to detect quota exhaustion events (`Individual quota reached`, `RESOURCE_EXHAUSTED 429`) and reset hints (`Resets in ~Xh Ym`) in real time.
- **POSIX Concurrency Guarantees:** File locking (`fcntl.flock`) and atomic file replacement (`.tmp` + `os.replace`) prevent race conditions between CLI commands, background daemons, and `agy` operations.
- **LRU Standby Failover:** Intelligently rotates to the least-recently used eligible standby account when the active profile is exhausted, placing exhausted accounts into a temporary cooldown window.

---

## Architecture & Filesystem Layout

```
~/.promux/
├── accounts/
│   ├── main/
│   │   └── antigravity-oauth-token   (chmod 0600)
│   ├── backup1/
│   │   └── antigravity-oauth-token   (chmod 0600)
│   └── backup2/
│       └── antigravity-oauth-token   (chmod 0600)
├── state.json                         (atomic state & metadata)
└── manager.lock                       (fcntl advisory lock)

~/.gemini/antigravity-cli/
├── antigravity-oauth-token            (active live token, chmod 0600)
├── cli.log                            (monitored by watcher daemon)
├── log/                               (session logs monitored by watcher)
└── conversations/, brain/, ...        (shared session continuity)
```

---

## Installation

### Prerequisites
- Linux or macOS
- Python 3.10 or higher
- Antigravity CLI (`agy`) installed

### Install in Editable Mode
```bash
pip install -e . --break-system-packages
```

Or for regular user installation:
```bash
pip install . --break-system-packages
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

### 1. Save Your Current Account
When you are logged in with your primary Antigravity account:
```bash
promux save main
```
This copies the live token into `~/.promux/accounts/main/` and queries the Google OAuth userinfo and Cloud Code Assist APIs to record your email, companion project ID, and plan type.

### 2. Add Secondary Accounts
Log in with your secondary Google account using `agy`:
```bash
agy auth login
```
Once authenticated, save it as a named standby profile:
```bash
promux save backup1
```

Repeat for any additional accounts (`backup2`, `work`, etc.).

### 3. Inspect Vault Profiles
```bash
promux list
```
Output:
```
ACTIVE  NAME              STATE       EMAIL                           COOLDOWN                LAST USED
--------------------------------------------------------------------------------------------------------------
*       main              standby     developer1@gmail.com            -                       2026-09-03T22:11:16+00:00
        backup1           standby     developer2@gmail.com            -                       2026-09-03T21:45:00+00:00
```

### 4. Check Real Quota Status
Query live Cloud Code Assist quota buckets (5-hour and weekly windows) for Gemini and 3rd-party (Claude/GPT) models:
```bash
promux quota
```
Output:
```
Quota for account 'main' (project: aicode-consumers):

MODEL GROUP              WINDOW     REMAINING    RESET TIME
----------------------------------------------------------------------
Gemini Models            5h         85.0%        2026-09-03T22:55:18Z
Gemini Models            weekly     57.3%        2026-09-08T17:46:10Z
Claude & GPT Models      5h         0.0%         2026-09-04T02:36:43Z
Claude & GPT Models      weekly     31.7%        2026-09-09T01:11:22Z
```

### 5. Manually Switch Profiles
Hot-swap the active token at any time:
```bash
promux switch backup1
```

---

## CLI Command Reference

`promux` supports human-readable ASCII tables and structured JSON outputs (via `--json`). The `--json` flag can be placed before or after any subcommand.

### `promux list`
List all accounts in the vault, displaying active status (`*`), account state (`active`, `standby`, `cooldown`, `disabled`), email, cooldown timestamp, and last-used timestamp.

```bash
promux list
promux list --json
```

### `promux save <name> [--email EMAIL]`
Saves the currently active `antigravity-oauth-token` into the vault under `<name>`. If `--email` is not supplied, `promux` automatically fetches the email via Google OAuth UserInfo API and project metadata via Cloud Code Assist API.

```bash
promux save personal
promux save work --email team@company.com
```

### `promux switch <name>`
Hot-swaps the active profile to `<name>`. Copies the vaulted token into `~/.gemini/antigravity-cli/antigravity-oauth-token` with atomic file replace and `0600` permissions under an advisory lock.

```bash
promux switch personal
```

### `promux next [--reason REASON] [--cooldown COOLDOWN]`
Rotates to the next eligible standby profile based on least-recently used (LRU) order. Automatically applies a cooldown (default: 60 minutes) to the departing profile.

```bash
promux next
promux next --reason "manual rotation" --cooldown 120
```

### `promux quota [name]`
Queries Google Cloud Code Assist for live quota fractions and reset timestamps. If `name` is omitted, the active profile is checked.

```bash
promux quota
promux quota backup1
promux quota --json
```

### `promux whoami`
Displays details of the active profile, including account name, email, OAuth token expiry, state, project ID, and plan type.

```bash
promux whoami
promux whoami --json
```

### `promux remove <name>`
Removes an account from the vault and cleans up its directory.

```bash
promux remove old-account
```

### `promux watch [--poll-seconds SEC] [--cooldown MIN]`
Starts the reactive log-tailing daemon. Tails `~/.gemini/antigravity-cli/cli.log` and session logs in `~/.gemini/antigravity-cli/log/` for quota depletion errors. Upon detecting exhaustion, it parses reset hints, assigns cooldowns, and hot-swaps to the next eligible profile automatically.

```bash
promux watch
promux watch --poll-seconds 0.5 --cooldown 60
```

---

## Automated Failover Daemon (`promux watch`)

The failover daemon provides autonomous quota failover during continuous coding sessions.

### How Log Tailing Works
1. **Multi-file Inode Tracking:** The watcher monitors `~/.gemini/antigravity-cli/cli.log` and any active session logs in `~/.gemini/antigravity-cli/log/`. It handles file creation, deletion, truncation, and log rotation by tracking file inodes and size.
2. **Quota Depletion Signatures:**
   - `Individual quota reached`
   - `RESOURCE_EXHAUSTED (code 429)`
   - `weekly quota reached`
3. **Reset Time Hint Extraction:** Detects regex patterns like `Resets in ~1h 45m` and calculates precise cooldown intervals. If no reset hint is found, defaults to 60 minutes (or the `--cooldown` override).
4. **LRU Rotation & Hot-Swap:** Acquires `~/.promux/manager.lock`, selects the LRU standby account not in cooldown, copies its token into `~/.gemini/antigravity-cli/antigravity-oauth-token`, and updates `~/.promux/state.json`.

### Running as a systemd User Service

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

### Running with nohup or tmux
Alternatively, run the daemon inside a tmux session or via nohup:
```bash
nohup promux watch > ~/.promux/watch.log 2>&1 &
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PROMUX_HOME` | `~/.promux` | Root directory for promux accounts vault, state file, and lock |
| `PROMUX_GEMINI_HOME` | `~/.gemini/antigravity-cli` | Root directory for Antigravity CLI live token and logs |

---

## Testing

The project includes an extensive test suite verifying locking semantics, atomic storage operations, Cloud Code REST API client mocking, LRU failover algorithms, reactive log tailing, and CLI subcommands.

Run full test suite:
```bash
pytest -v
```

Run specific test modules:
```bash
pytest tests/test_lock.py -v
pytest tests/test_storage.py -v
pytest tests/test_quota.py -v
pytest tests/test_failover.py -v
pytest tests/test_watch.py -v
pytest tests/test_cli.py -v
```

---

## License

MIT
