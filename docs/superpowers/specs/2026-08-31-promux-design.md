# Promux Implementation Design

**Date:** 2026-08-31  
**Status:** Approved for implementation planning  
**Source Spec:** `SPEC.md` (architectural specification)

---

## 1. Architecture Overview

`promux` is a standalone Python CLI daemon managing multiple Antigravity CLI (`agy`) OAuth profiles with automated quota failover. It operates as a zero-intrusion filesystem overlay — `agy` remains completely unaware.

### Module Structure (src/promux/)

| Module | Responsibility | Key Exports |
|--------|----------------|-------------|
| `constants.py` | Default paths, API endpoints, regex patterns, timeouts | `PROMUX_HOME`, `GEMINI_CLI_PATH`, `QUOTA_BASE_URL`, regex constants |
| `models.py` | Dataclasses for type-safe data flow | `AccountMeta`, `QuotaSummary`, `RotationResult`, `AccountState` enum |
| `lock.py` | Cross-platform file locking (fcntl/msvcrt) | `FileLock` context manager |
| `storage.py` | State persistence, atomic writes, profile vault ops | `StorageEngine` class |
| `quota.py` | Google Cloud Code Assist REST client | `QuotaClient` class |
| `failover.py` | Candidate selection, cooldown coordination | `FailoverEngine` class |
| `watch.py` | Reactive log tailing & pattern detection | `LogWatcher` class |
| `cli.py` | Argparse CLI with subcommands & formatters | `main()` entrypoint |

### Data Flow

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   User      │────▶│   CLI        │────▶│  Storage    │
│  Commands   │     │  (cli.py)    │     │  Engine     │
└─────────────┘     └──────────────┘     └──────┬──────┘
                                                 │
                        ┌──────────────┐         │
                        │  Failover    │◀────────┤
                        │  Engine      │         │
                        └──────┬───────┘         │
                               │                 │
              ┌────────────────┼────────────────┘
              ▼                ▼
       ┌────────────┐  ┌──────────────┐
       │  Quota     │  │   Watch      │
       │  Client    │  │  (LogWatch)  │
       └────────────┘  └──────────────┘
              │                │
              └───────┬────────┘
                      ▼
             ┌────────────────┐
             │  Google Cloud  │
             │  Code Assist   │
             │  API           │
             └────────────────┘
```

---

## 2. Component Designs

### 2.1 Constants (`constants.py`)

```python
# Filesystem
PROMUX_HOME = Path.home() / ".promux"
ACCOUNTS_DIR = PROMUX_HOME / "accounts"
STATE_FILE = PROMUX_HOME / "state.json"
LOCK_FILE = PROMUX_HOME / "manager.lock"

# Antigravity CLI
GEMINI_CLI_HOME = Path.home() / ".gemini" / "antigravity-cli"
LIVE_TOKEN = GEMINI_CLI_HOME / "antigravity-oauth-token"
CLI_LOG = GEMINI_CLI_HOME / "cli.log"
LOG_DIR = GEMINI_CLI_HOME / "log"

# API
QUOTA_BASE_URL = "https://cloudcode-pa.googleapis.com"
LOAD_ENDPOINT = "/v1internal:loadCodeAssist"
QUOTA_ENDPOINT = "/v1internal:retrieveUserQuotaSummary"
USER_AGENT = "antigravity"

# Regex Patterns (from SPEC §5.1)
INDIVIDUAL_QUOTA_RE = re.compile(r"(?i)Individual quota reached")
RESOURCE_EXHAUSTED_RE = re.compile(r"(?i)RESOURCE_EXHAUSTED\s*\(\s*code\s*429\s*\)|RESOURCE_EXHAUSTED")
WEEKLY_QUOTA_RE = re.compile(r"(?i)weekly quota reached")
RESET_HINT_RE = re.compile(r"(?i)Resets in\s+(?P<reset>~?[^.)]+)")

# Defaults
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_COOLDOWN_MINUTES = 60
HTTP_TIMEOUT = 30
```

### 2.2 Models (`models.py`)

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

class AccountState(Enum):
    STANDBY = "standby"
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"

@dataclass
class AccountMeta:
    name: str
    enabled: bool = True
    cooldown_until: Optional[datetime] = None
    saved_at: datetime = field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = None
    email: Optional[str] = None
    project_id: Optional[str] = None
    plan_type: str = "STANDARD"
    
    @property
    def state(self) -> AccountState:
        if not self.enabled:
            return AccountState.DISABLED
        if self.cooldown_until and self.cooldown_until > datetime.utcnow():
            return AccountState.COOLDOWN
        return AccountState.STANDBY

@dataclass
class QuotaSummary:
    short_window_remaining: float  # 0.0-1.0
    weekly_remaining: float
    short_window_reset: Optional[datetime] = None
    weekly_reset: Optional[datetime] = None
    monthly_credits: int = 0
    available_credits: int = 0

@dataclass
class RotationResult:
    success: bool
    from_account: Optional[str]
    to_account: Optional[str]
    reason: str
    cooldown_until: Optional[datetime] = None
```

### 2.3 Lock (`lock.py`)

```python
import os
import fcntl
from contextlib import contextmanager
from pathlib import Path

@contextmanager
def file_lock(lock_path: Path, timeout: float = 10.0):
    """Cross-platform advisory file lock."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield True
        except BlockingIOError:
            # Wait with polling
            start = time.time()
            while time.time() - start < timeout:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    yield True
                    return
                except BlockingIOError:
                    time.sleep(0.1)
            raise TimeoutError(f"Could not acquire lock on {lock_path}")
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
```

### 2.4 Storage (`storage.py`)

```python
import json
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

from .models import AccountMeta, AccountState
from .lock import file_lock

class StorageEngine:
    def __init__(self, promux_home: Path = None):
        self.home = promux_home or Path.home() / ".promux"
        self.accounts_dir = self.home / "accounts"
        self.state_file = self.home / "state.json"
        self.lock_file = self.home / "manager.lock"
        
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
    
    def _atomic_write(self, path: Path, data: dict):
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, path)
    
    def load_state(self) -> dict:
        if not self.state_file.exists():
            return {"active": None, "accounts": {}}
        with open(self.state_file) as f:
            return json.load(f)
    
    def save_state(self, state: dict):
        with file_lock(self.lock_file):
            self._atomic_write(self.state_file, state)
    
    def save_profile(self, name: str) -> AccountMeta:
        """Copy live token to vault and register account."""
        with file_lock(self.lock_file):
            # Copy token
            acct_dir = self.accounts_dir / name
            acct_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(LIVE_TOKEN, acct_dir / "antigravity-oauth-token")
            
            # Extract email from token (JWT decode without verification)
            email = self._extract_email(LIVE_TOKEN)
            
            # Update state
            state = self.load_state()
            state["accounts"][name] = AccountMeta(
                name=name, email=email, saved_at=datetime.utcnow()
            ).__dict__
            if not state["active"]:
                state["active"] = name
            self.save_state(state)
            
            return state["accounts"][name]
    
    def switch_profile(self, name: str) -> bool:
        """Hot-swap token to live location."""
        with file_lock(self.lock_file):
            token_path = self.accounts_dir / name / "antigravity-oauth-token"
            if not token_path.exists():
                return False
            shutil.copy2(token_path, LIVE_TOKEN)
            state = self.load_state()
            state["active"] = name
            state["accounts"][name]["last_used_at"] = datetime.utcnow().isoformat()
            self.save_state(state)
            return True
```

### 2.5 Quota Client (`quota.py`)

```python
import urllib.request
import urllib.error
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from .models import QuotaSummary
from .constants import *

class QuotaClient:
    def __init__(self, token: str, timeout: int = HTTP_TIMEOUT):
        self.token = token
        self.timeout = timeout
    
    def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{QUOTA_BASE_URL}{endpoint}"
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT
            }, method="POST"
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)
    
    def load_metadata(self) -> Tuple[str, str, int, int]:
        """Returns (project_id, plan_type, monthly_credits, available_credits)"""
        resp = self._post(LOAD_ENDPOINT, {
            "metadata": {"ideType": "ANTIGRAVITY", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}
        })
        project = resp.get("cloudaicompanionProject")
        if isinstance(project, dict):
            project = project.get("id", "")
        plan = resp.get("planInfo", {})
        return (
            project or "",
            plan.get("planType", "STANDARD"),
            plan.get("monthlyPromptCredits", 0),
            resp.get("availablePromptCredits", 0)
        )
    
    def get_quota(self, project_id: str) -> QuotaSummary:
        resp = self._post(QUOTA_ENDPOINT, {"project": project_id})
        short_rem = 1.0
        weekly_rem = 1.0
        short_reset = None
        weekly_reset = None
        
        for group in resp.get("groups", []):
            if group.get("name") == "short_window":
                for bucket in group.get("buckets", []):
                    if bucket.get("name") == "model_requests":
                        short_rem = bucket.get("remainingFraction", 1.0)
                        short_reset = self._parse_reset(bucket.get("resetTime"))
            elif group.get("name") == "weekly_window":
                for bucket in group.get("buckets", []):
                    if bucket.get("name") == "model_requests":
                        weekly_rem = bucket.get("remainingFraction", 1.0)
                        weekly_reset = self._parse_reset(bucket.get("resetTime"))
        
        return QuotaSummary(
            short_window_remaining=short_rem,
            weekly_remaining=weekly_rem,
            short_window_reset=short_reset,
            weekly_reset=weekly_reset
        )
```

### 2.6 Failover Engine (`failover.py`)

```python
from datetime import datetime, timedelta
from typing import Optional, List

from .models import AccountMeta, AccountState, RotationResult
from .storage import StorageEngine
from .quota import QuotaClient
from .constants import DEFAULT_COOLDOWN_MINUTES

class FailoverEngine:
    def __init__(self, storage: StorageEngine):
        self.storage = storage
    
    def get_eligible_standby(self, exclude: str = None) -> List[AccountMeta]:
        state = self.storage.load_state()
        now = datetime.utcnow()
        eligible = []
        for name, acct_data in state["accounts"].items():
            if name == exclude:
                continue
            acct = AccountMeta(**acct_data)
            if acct.state == AccountState.STANDBY:
                eligible.append(acct)
        # Sort by last_used_at (least recently used first)
        eligible.sort(key=lambda a: a.last_used_at or datetime.min)
        return eligible
    
    def rotate_next(self, reason: str = "manual", cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES) -> RotationResult:
        state = self.storage.load_state()
        active_name = state.get("active")
        
        # Put current active into cooldown
        if active_name:
            state["accounts"][active_name]["cooldown_until"] = (
                datetime.utcnow() + timedelta(minutes=cooldown_minutes)
            ).isoformat()
        
        # Find next eligible
        candidates = self.get_eligible_standby(exclude=active_name)
        if not candidates:
            self.storage.save_state(state)
            return RotationResult(
                success=False, from_account=active_name, to_account=None,
                reason=f"No eligible standby accounts ({reason})"
            )
        
        next_acct = candidates[0]
        # Switch
        if not self.storage.switch_profile(next_acct.name):
            return RotationResult(
                success=False, from_account=active_name, to_account=next_acct.name,
                reason="Switch failed"
            )
        
        state = self.storage.load_state()
        state["accounts"][next_acct.name]["last_used_at"] = datetime.utcnow().isoformat()
        self.storage.save_state(state)
        
        return RotationResult(
            success=True, from_account=active_name, to_account=next_acct.name,
            reason=reason, cooldown_until=datetime.utcnow() + timedelta(minutes=cooldown_minutes)
        )
    
    def check_and_failover(self, token: str) -> Optional[RotationResult]:
        """Proactive quota check — returns RotationResult if failover triggered."""
        state = self.storage.load_state()
        active_name = state.get("active")
        if not active_name:
            return None
        
        acct_data = state["accounts"][active_name]
        project_id = acct_data.get("project_id")
        if not project_id:
            return None
        
        client = QuotaClient(token)
        quota = client.get_quota(project_id)
        
        # Threshold: if short window < 10% or weekly < 5%, trigger
        if quota.short_window_remaining < 0.10 or quota.weekly_remaining < 0.05:
            return self.rotate_next(
                reason=f"proactive: short={quota.short_window_remaining:.0%}, weekly={quota.weekly_remaining:.0%}",
                cooldown_minutes=DEFAULT_COOLDOWN_MINUTES
            )
        return None
```

### 2.7 Log Watcher (`watch.py`)

```python
import re
import time
from pathlib import Path
from typing import Callable, Optional
from dataclasses import dataclass

from .constants import *
from .failover import FailoverEngine
from .storage import StorageEngine

@dataclass
class LogMatch:
    pattern: str
    line: str
    reset_hint: Optional[str] = None

class LogWatcher:
    PATTERNS = [
        ("INDIVIDUAL_QUOTA", INDIVIDUAL_QUOTA_RE),
        ("RESOURCE_EXHAUSTED", RESOURCE_EXHAUSTED_RE),
        ("WEEKLY_QUOTA", WEEKLY_QUOTA_RE),
    ]
    
    def __init__(self, failover_engine: FailoverEngine, poll_seconds: float = DEFAULT_POLL_SECONDS):
        self.failover = failover_engine
        self.poll_seconds = poll_seconds
        self.offsets: dict[Path, int] = {}
        self.running = False
    
    def _get_log_files(self) -> list[Path]:
        files = [CLI_LOG]
        if LOG_DIR.exists():
            files.extend(LOG_DIR.glob("*.log"))
        return [f for f in files if f.exists()]
    
    def _read_new_lines(self, path: Path) -> list[str]:
        offset = self.offsets.get(path, 0)
        try:
            size = path.stat().st_size
            if size < offset:  # Log rotation
                offset = 0
            if size == offset:
                return []
            with open(path, "r") as f:
                f.seek(offset)
                lines = f.readlines()
            self.offsets[path] = size
            return lines
        except OSError:
            return []
    
    def _check_line(self, line: str) -> Optional[LogMatch]:
        for name, pattern in self.PATTERNS:
            if pattern.search(line):
                reset_match = RESET_HINT_RE.search(line)
                return LogMatch(
                    pattern=name, line=line.strip(),
                    reset_hint=reset_match.group("reset") if reset_match else None
                )
        return None
    
    def run_once(self) -> list[LogMatch]:
        matches = []
        for log_file in self._get_log_files():
            for line in self._read_new_lines(log_file):
                match = self._check_line(line)
                if match:
                    matches.append(match)
        return matches
    
    def run_forever(self, on_match: Callable[[LogMatch], None] = None):
        self.running = True
        # Initialize offsets to EOF
        for f in self._get_log_files():
            self.offsets[f] = f.stat().st_size
        
        while self.running:
            matches = self.run_once()
            for match in matches:
                if on_match:
                    on_match(match)
                # Trigger failover
                self.failover.rotate_next(reason=f"reactive: {match.pattern}")
            time.sleep(self.poll_seconds)
    
    def stop(self):
        self.running = False
```

### 2.8 CLI (`cli.py`)

```python
import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .storage import StorageEngine
from .quota import QuotaClient
from .failover import FailoverEngine
from .watch import LogWatcher
from .constants import *

def main() -> int:
    parser = argparse.ArgumentParser(prog="promux", description="Antigravity CLI profile multiplexer")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    sub = parser.add_subparsers(dest="command", required=True)
    
    # list
    sub.add_parser("list", help="List all accounts")
    
    # save
    save_p = sub.add_parser("save", help="Save current token as named profile")
    save_p.add_argument("name")
    
    # switch
    switch_p = sub.add_parser("switch", help="Switch to named profile")
    switch_p.add_argument("name")
    
    # next
    next_p = sub.add_parser("next", help="Rotate to next eligible standby")
    next_p.add_argument("--reason", default="manual")
    next_p.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN_MINUTES)
    
    # quota
    quota_p = sub.add_parser("quota", help="Check quota for account")
    quota_p.add_argument("name", nargs="?")
    
    # whoami
    sub.add_parser("whoami", help="Show current active account")
    
    # remove
    remove_p = sub.add_parser("remove", help="Remove account from vault")
    remove_p.add_argument("name")
    
    # watch
    watch_p = sub.add_parser("watch", help="Daemon: tail logs for quota errors")
    watch_p.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    watch_p.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN_MINUTES)
    
    args = parser.parse_args()
    
    storage = StorageEngine()
    failover = FailoverEngine(storage)
    
    try:
        if args.command == "list":
            return cmd_list(storage, args.json)
        elif args.command == "save":
            return cmd_save(storage, args.name, args.json)
        elif args.command == "switch":
            return cmd_switch(storage, args.name, args.json)
        elif args.command == "next":
            return cmd_next(failover, args.reason, args.cooldown, args.json)
        elif args.command == "quota":
            return cmd_quota(storage, args.name, args.json)
        elif args.command == "whoami":
            return cmd_whoami(storage, args.json)
        elif args.command == "remove":
            return cmd_remove(storage, args.name, args.json)
        elif args.command == "watch":
            return cmd_watch(failover, args.poll_seconds, args.cooldown)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0

def cmd_list(storage: StorageEngine, json_out: bool) -> int:
    state = storage.load_state()
    active = state.get("active")
    rows = []
    for name, data in state["accounts"].items():
        from .models import AccountMeta, AccountState
        acct = AccountMeta(**data)
        rows.append({
            "name": name, "active": name == active,
            "state": acct.state.value, "email": acct.email,
            "cooldown_until": data.get("cooldown_until"),
            "last_used": data.get("last_used_at")
        })
    if json_out:
        print(json.dumps(rows, indent=2))
    else:
        # Pretty table
        print(f"{'ACTIVE':<6} {'NAME':<20} {'STATE':<10} {'EMAIL':<30} {'COOLDOWN'}")
        for r in rows:
            star = "*" if r["active"] else " "
            cool = r["cooldown_until"] or ""
            print(f"{star:<6} {r['name']:<20} {r['state']:<10} {r['email'] or '':<30} {cool}")
    return 0

# ... other cmd_* functions follow same pattern

if __name__ == "__main__":
    sys.exit(main())
```

---

## 3. Error Handling Strategy

| Layer | Strategy |
|-------|----------|
| **Lock acquisition** | Timeout after 10s, raise `TimeoutError` |
| **HTTP calls** | Timeout 30s, retry once on 5xx, propagate 4xx |
| **File ops** | Atomic writes via `.tmp` + `os.replace`; never partial state |
| **JSON parsing** | Validate schema on load, migrate if needed |
| **Log tailing** | Never crash on log rotation; reset offset to 0 |
| **CLI** | Exit code 0=success, 1=error, 2=usage error |

---

## 4. Testing Plan (per SPEC §8)

| Test Module | Coverage |
|-------------|----------|
| `test_lock.py` | Mutex acquisition, contention, cross-process |
| `test_storage.py` | Save/switch/remove, atomic writes, concurrent access |
| `test_quota.py` | Mock HTTP responses, parse short/weekly buckets, error cases |
| `test_failover.py` | Rotation logic, cooldown transitions, no-eligible case |
| `test_watch.py` | Pattern matching, offset tracking, rotation handling |
| `test_cli.py` | End-to-end subprocess tests against temp dirs |

**Fixtures** (`conftest.py`):
- `tmp_promux_home` — isolated `~/.promux` per test
- `mock_http_post` — patches `urllib.request.urlopen`
- `sample_token` — valid JWT-like string for email extraction

---

## 5. Packaging (`pyproject.toml`)

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "promux"
version = "0.1.0"
description = "Antigravity CLI profile multiplexer & quota failover daemon"
readme = "README.md"
license = {text = "MIT"}
authors = [{name = "Alireza Opmc"}]
requires-python = ">=3.10"
classifiers = [
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
]
dependencies = []

[project.optional-dependencies]
dev = ["pytest", "pytest-mock"]

[project.scripts]
promux = "promux.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
```

---

## 6. Implementation Order

1. **constants.py + models.py** — Foundation types
2. **lock.py** — Concurrency primitive
3. **storage.py** — Vault + state persistence
4. **quota.py** — API client (mockable)
5. **failover.py** — Rotation logic
6. **watch.py** — Log tailing
7. **cli.py** — Wire everything together
8. **tests/** — Full coverage per module
9. **README.md + pyproject.toml** — Packaging

---

## 7. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Windows `msvcrt.locking` differs from `fcntl` | Test on Windows; fallback to portalocker if needed (but adds dep) |
| Token expiration not handled | SPEC assumes `agy` handles refresh; document limitation |
| Log path changes in `agy` updates | Configurable via env var `PROMUX_GEMINI_PATH` |
| Concurrent `agy` + `promux` runs | File lock guards all state transitions |

---

## 8. Acceptance Criteria (from SPEC §8)

- [ ] `promux save <name>` copies `~/.gemini/antigravity-cli/antigravity-oauth-token`
- [ ] `promux switch <name>` hot-swaps active profile
- [ ] `promux quota` parses live Cloud Code responses accurately
- [ ] `promux watch` reacts to simulated log appends of `Individual quota reached` within 1 second
- [ ] All unit tests pass with mocked network and sandboxed dirs
- [ ] Package installs via `pip install -e .` and `promux --help` works

---

*Design approved. Ready for implementation planning via `writing-plans` skill.*