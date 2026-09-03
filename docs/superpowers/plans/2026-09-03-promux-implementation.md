# Promux Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and package `promux`, a zero-dependency Python 3.10+ CLI and background daemon that provides isolated multi-account profile switching and automated quota failover (`429 RESOURCE_EXHAUSTED`) for the Antigravity CLI (`agy`).

**Architecture:** A zero-intrusion filesystem overlay treating `~/.gemini/antigravity-cli/antigravity-oauth-token` as a hot-swappable interface. State and accounts are vaulted under `~/.promux/` with POSIX `flock` concurrency guarantees and atomic file renames. Dual-loop quota awareness combines proactive Google Cloud Code Assist REST API queries with a reactive log-tailing daemon on `cli.log`.

**Tech Stack:** Python 3.10+ Standard Library exclusively (`urllib`, `json`, `fcntl`, `subprocess`, `argparse`, `dataclasses`, `re`, `shutil`, `pathlib`), packaged via PEP 517/621 (`setuptools`), tested with `pytest`.

## Global Constraints

- **Zero External Runtime Dependencies:** Only Python standard library allowed in runtime code (`src/promux`).
- **File Permissions:** Any OAuth token stored or restored must strictly enforce `0600` (`chmod 600`) POSIX permissions.
- **Atomic State Mutations:** State file (`state.json`) must always be written to `.tmp` and swapped via `os.replace` under `manager.lock`.
- **Live Schema Accuracy:** Quota responses must support both `"Gemini Models"` and `"Claude and GPT models"` groups, with `"5h"` and `"weekly"` window buckets.
- **Conversation Continuity & Context Sharing:** Only the OAuth token (`antigravity-oauth-token`) is swapped. Conversation sessions (`conversations/`, `conversation_summaries.db`, `brain/`, `history.jsonl`) remain strictly untouched and shared across all accounts, guaranteeing that users can seamlessly resume chats (`agy -c` / `--conversation=<id>`) across accounts.
- **Testing:** 100% network isolation in unit tests (all HTTP calls mocked using monkeypatching `urllib.request.urlopen`). All filesystem operations sandboxed via `tmp_path`.

---

## File Structure

```text
/home/opmc/Dev/promux/
├── pyproject.toml
├── README.md
├── LICENSE
├── .gitignore
├── src/
│   └── promux/
│       ├── __init__.py         # Package version and root exports
│       ├── constants.py        # Default paths, endpoints, regexes, intervals
│       ├── models.py           # AccountMeta, AccountState, QuotaSummary, RotationResult
│       ├── lock.py             # Cross-platform advisory file locking (fcntl)
│       ├── storage.py          # StorageEngine: state persistence, vault copy, atomic swap
│       ├── quota.py            # QuotaClient: Cloud Code Assist & UserInfo REST client
│       ├── failover.py         # FailoverEngine: rotation logic, cooldowns, candidate selection
│       ├── watch.py            # LogWatcher: reactive log tailing, rotation recovery, regex detection
│       └── cli.py              # CLI entry point: subcommands, table formatting, JSON mode
└── tests/
    ├── conftest.py             # Test fixtures: mock tokens, temp homes, mock responses
    ├── test_lock.py            # Concurrency and timeout unit tests
    ├── test_models.py          # State transitions and dataclass serialization tests
    ├── test_storage.py         # Vault saving, switching, atomic updates, permissions
    ├── test_quota.py           # REST client response parsing with real captured payloads
    ├── test_failover.py        # LRU standby candidate selection, cooldown enforcement
    ├── test_watch.py           # Stream processing, log rotation, pattern detection
    └── test_cli.py             # End-to-end argparse subcommand executions
```

---

### Task 1: Scaffolding, Packaging & Environment Setup

**Files:**
- Create: `pyproject.toml`
- Create: `LICENSE`
- Create: `.gitignore`
- Create: `README.md`
- Create: `src/promux/__init__.py`
- Test: `tests/conftest.py`

**Interfaces:**
- Produces: Package `promux` installable via `pip install -e .` exposing console script `promux = promux.cli:main`.

- [ ] **Step 1: Create `.gitignore` and `LICENSE`**

`LICENSE`: Standard MIT license.  
`.gitignore`: Python cache, `__pycache__`, `.pytest_cache/`, `dist/`, `build/`, `*.egg-info/`, `.promux/`.

- [ ] **Step 2: Create `pyproject.toml`**

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
    "Programming Language :: Python :: 3.14",
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

- [ ] **Step 3: Create initial package and `tests/conftest.py`**

Create `src/promux/__init__.py`:
```python
"""Promux: Antigravity CLI profile multiplexer & quota failover daemon."""

__version__ = "0.1.0"
```

Create initial `tests/conftest.py`:
```python
import pytest
import json
from pathlib import Path

@pytest.fixture
def sample_token_dict():
    return {
        "token": {
            "access_token": "ya29.mock_access_token",
            "token_type": "Bearer",
            "refresh_token": "mock_refresh_token",
            "expiry": "2026-09-03T22:00:00Z"
        },
        "auth_method": "consumer"
    }

@pytest.fixture
def fake_promux_home(tmp_path):
    promux_home = tmp_path / ".promux"
    promux_home.mkdir()
    return promux_home
```

- [ ] **Step 4: Verify package install and test runner**

Run: `pytest tests/conftest.py`
Expected: 0 errors / collection passed.
Run: `pip install -e . --break-system-packages`
Expected: Successfully installed promux-0.1.0.

- [ ] **Step 5: Git commit**

```bash
git add pyproject.toml LICENSE README.md .gitignore src/ tests/
git commit -m "chore: scaffold promux package structure and test configuration"
```

---

### Task 2: Constants and Domain Models

**Files:**
- Create: `src/promux/constants.py`
- Create: `src/promux/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `AccountState`, `AccountMeta`, `QuotaBucket`, `QuotaSummary`, `RotationResult`, and constant paths/regexes.

- [ ] **Step 1: Write failing unit test for models**

`tests/test_models.py`:
```python
from datetime import datetime, timedelta
from promux.models import AccountMeta, AccountState, QuotaSummary, RotationResult

def test_account_state_computation():
    now = datetime.utcnow()
    # Disabled
    disabled_acct = AccountMeta(name="work", enabled=False)
    assert disabled_acct.state == AccountState.DISABLED

    # Cooldown
    cool_acct = AccountMeta(name="work", enabled=True, cooldown_until=now + timedelta(minutes=30))
    assert cool_acct.state == AccountState.COOLDOWN

    # Standby
    standby_acct = AccountMeta(name="work", enabled=True, cooldown_until=now - timedelta(minutes=10))
    assert standby_acct.state == AccountState.STANDBY

def test_quota_summary_dataclass():
    qs = QuotaSummary(
        gemini_5h_remaining=0.85,
        gemini_weekly_remaining=0.57,
        third_party_5h_remaining=0.25,
        third_party_weekly_remaining=0.43,
        gemini_5h_reset="2026-09-03T22:55:18Z",
        third_party_5h_reset="2026-09-04T02:36:43Z"
    )
    assert qs.min_short_window == 0.25
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'promux.models'`).

- [ ] **Step 3: Implement `src/promux/constants.py`**

```python
import os
import re
from pathlib import Path

# Paths
PROMUX_HOME = Path(os.environ.get("PROMUX_HOME", Path.home() / ".promux"))
ACCOUNTS_DIR = PROMUX_HOME / "accounts"
STATE_FILE = PROMUX_HOME / "state.json"
LOCK_FILE = PROMUX_HOME / "manager.lock"

# Gemini / Antigravity CLI Paths
GEMINI_CLI_HOME = Path(os.environ.get("PROMUX_GEMINI_HOME", Path.home() / ".gemini" / "antigravity-cli"))
LIVE_TOKEN = GEMINI_CLI_HOME / "antigravity-oauth-token"
CLI_LOG = GEMINI_CLI_HOME / "cli.log"
LOG_DIR = GEMINI_CLI_HOME / "log"

# API Endpoints
CODE_ASSIST_BASE_URL = "https://cloudcode-pa.googleapis.com"
LOAD_ENDPOINT = "/v1internal:loadCodeAssist"
QUOTA_ENDPOINT = "/v1internal:retrieveUserQuotaSummary"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
USER_AGENT = "antigravity"

# Regex Signatures (SPEC §5.1)
INDIVIDUAL_QUOTA_RE = re.compile(r"(?i)Individual quota reached")
RESOURCE_EXHAUSTED_RE = re.compile(r"(?i)RESOURCE_EXHAUSTED\s*\(\s*code\s*429\s*\)|RESOURCE_EXHAUSTED")
WEEKLY_QUOTA_RE = re.compile(r"(?i)weekly quota reached")
RESET_HINT_RE = re.compile(r"(?i)Resets in\s+(?P<reset>~?[^.)\n]+)")

# Defaults
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_COOLDOWN_MINUTES = 60
DEFAULT_LOCK_TIMEOUT = 10.0
DEFAULT_HTTP_TIMEOUT = 15.0
```

- [ ] **Step 4: Implement `src/promux/models.py`**

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any, Dict

class AccountState(str, Enum):
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "saved_at": self.saved_at.isoformat() if self.saved_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "email": self.email,
            "project_id": self.project_id,
            "plan_type": self.plan_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AccountMeta":
        return cls(
            name=data["name"],
            enabled=data.get("enabled", True),
            cooldown_until=datetime.fromisoformat(data["cooldown_until"]) if data.get("cooldown_until") else None,
            saved_at=datetime.fromisoformat(data["saved_at"]) if data.get("saved_at") else datetime.utcnow(),
            last_used_at=datetime.fromisoformat(data["last_used_at"]) if data.get("last_used_at") else None,
            email=data.get("email"),
            project_id=data.get("project_id"),
            plan_type=data.get("plan_type", "STANDARD"),
        )

@dataclass
class QuotaBucket:
    bucket_id: str
    display_name: str
    window: str
    remaining_fraction: float
    reset_time: Optional[str] = None

@dataclass
class QuotaSummary:
    gemini_5h_remaining: float = 1.0
    gemini_weekly_remaining: float = 1.0
    third_party_5h_remaining: float = 1.0
    third_party_weekly_remaining: float = 1.0
    gemini_5h_reset: Optional[str] = None
    gemini_weekly_reset: Optional[str] = None
    third_party_5h_reset: Optional[str] = None
    third_party_weekly_reset: Optional[str] = None

    @property
    def min_short_window(self) -> float:
        return min(self.gemini_5h_remaining, self.third_party_5h_remaining)

    @property
    def min_weekly(self) -> float:
        return min(self.gemini_weekly_remaining, self.third_party_weekly_remaining)

@dataclass
class RotationResult:
    success: bool
    from_account: Optional[str]
    to_account: Optional[str]
    reason: str
    cooldown_until: Optional[datetime] = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_models.py`
Expected: PASS.

- [ ] **Step 6: Git commit**

```bash
git add src/promux/constants.py src/promux/models.py tests/test_models.py
git commit -m "feat(models): add domain models, state enums and system constants"
```

---

### Task 3: Advisory Concurrency Locking (`lock.py`)

**Files:**
- Create: `src/promux/lock.py`
- Test: `tests/test_lock.py`

**Interfaces:**
- Produces: `@contextmanager def file_lock(lock_path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT)`

- [ ] **Step 1: Write failing unit test for file locking and contention**

`tests/test_lock.py`:
```python
import time
import pytest
from pathlib import Path
from multiprocessing import Process, Queue
from promux.lock import file_lock

def _worker_acquire(lock_path: Path, hold_seconds: float, queue: Queue):
    try:
        with file_lock(lock_path, timeout=2.0):
            queue.put("acquired")
            time.sleep(hold_seconds)
            queue.put("released")
    except Exception as e:
        queue.put(f"error: {e}")

def test_file_lock_mutual_exclusion(tmp_path):
    lock_file = tmp_path / "test.lock"
    q1 = Queue()
    q2 = Queue()

    p1 = Process(target=_worker_acquire, args=(lock_file, 0.4, q1))
    p2 = Process(target=_worker_acquire, args=(lock_file, 0.1, q2))

    p1.start()
    time.sleep(0.05)  # ensure p1 gets it first
    p2.start()

    p1.join()
    p2.join()

    events = [q1.get(), q1.get(), q2.get(), q2.get()]
    assert events == ["acquired", "released", "acquired", "released"]

def test_file_lock_timeout(tmp_path):
    lock_file = tmp_path / "timeout.lock"
    with file_lock(lock_file, timeout=1.0):
        with pytest.raises(TimeoutError):
            with file_lock(lock_file, timeout=0.2):
                pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_lock.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'promux.lock'`).

- [ ] **Step 3: Implement `src/promux/lock.py`**

```python
import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from .constants import DEFAULT_LOCK_TIMEOUT

@contextmanager
def file_lock(lock_path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT):
    """Advisory POSIX flock context manager with polling timeout."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
    start_time = time.monotonic()
    acquired = False

    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() - start_time >= timeout:
                    raise TimeoutError(f"Timed out after {timeout}s waiting for lock: {lock_path}")
                time.sleep(0.05)

        yield lock_path
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_lock.py`
Expected: PASS.

- [ ] **Step 5: Git commit**

```bash
git add src/promux/lock.py tests/test_lock.py
git commit -m "feat(lock): implement POSIX file locking with retry and timeout"
```

---

### Task 4: Storage Engine & Vault Operations (`storage.py`)

**Files:**
- Create: `src/promux/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `lock.py`, `models.py`, `constants.py`
- Produces: `StorageEngine` class with `save_profile()`, `switch_profile()`, `remove_profile()`, `load_state()`, `save_state()`, `get_active_profile()`, `get_account()`.

- [ ] **Step 1: Write failing tests for storage operations**

`tests/test_storage.py`:
```python
import os
import json
import stat
from pathlib import Path
import pytest
from promux.storage import StorageEngine
from promux.models import AccountMeta, AccountState

def test_save_and_switch_profile(tmp_path, sample_token_dict):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    live_token = gemini_home / "antigravity-oauth-token"
    live_token.write_text(json.dumps(sample_token_dict))
    live_token.chmod(0o600)

    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    
    # Save work profile
    acct = storage.save_profile("work", email="work@company.com", project_id="aicode-work")
    assert acct.name == "work"
    assert acct.email == "work@company.com"
    assert storage.get_active_profile() == "work"

    # Verify vault copy has 0600 permissions
    vault_token = promux_home / "accounts" / "work" / "antigravity-oauth-token"
    assert vault_token.exists()
    assert oct(vault_token.stat().st_mode & 0o777) == "0o600"

    # Save personal profile with different token
    personal_token_data = dict(sample_token_dict)
    personal_token_data["token"]["access_token"] = "personal_token"
    live_token.write_text(json.dumps(personal_token_data))
    storage.save_profile("personal", email="personal@home.org")

    # Switch back to work
    assert storage.switch_profile("work") is True
    assert storage.get_active_profile() == "work"
    active_data = json.loads(live_token.read_text())
    assert active_data["token"]["access_token"] == "ya29.mock_access_token"
    assert oct(live_token.stat().st_mode & 0o777) == "0o600"

def test_remove_profile(tmp_path, sample_token_dict):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    live_token = gemini_home / "antigravity-oauth-token"
    live_token.write_text(json.dumps(sample_token_dict))

    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    storage.save_profile("temp")
    assert storage.get_account("temp") is not None

    storage.remove_profile("temp")
    assert storage.get_account("temp") is None
    assert not (promux_home / "accounts" / "temp").exists()

def test_conversations_and_history_preserved_across_switches(tmp_path, sample_token_dict):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    live_token = gemini_home / "antigravity-oauth-token"
    live_token.write_text(json.dumps(sample_token_dict))

    # Mock existing chat files
    conv_dir = gemini_home / "conversations"
    conv_dir.mkdir()
    conv_file = conv_dir / "chat-1234.json"
    conv_file.write_text('{"id": "chat-1234", "messages": []}')
    db_file = gemini_home / "conversation_summaries.db"
    db_file.write_text("sqlite dummy data")
    history_file = gemini_home / "history.jsonl"
    history_file.write_text('{"display": "hello"}\n')

    storage = StorageEngine(promux_home=promux_home, gemini_home=gemini_home)
    storage.save_profile("acc1")
    storage.save_profile("acc2")
    storage.switch_profile("acc2")

    # Assert conversation files are untouched
    assert conv_file.exists()
    assert conv_file.read_text() == '{"id": "chat-1234", "messages": []}'
    assert db_file.exists()
    assert db_file.read_text() == "sqlite dummy data"
    assert history_file.exists()
    assert history_file.read_text() == '{"display": "hello"}\n'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'promux.storage'`).

- [ ] **Step 3: Implement `src/promux/storage.py`**

```python
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any, List

from .constants import PROMUX_HOME, GEMINI_CLI_HOME, LIVE_TOKEN, ACCOUNTS_DIR, STATE_FILE, LOCK_FILE
from .lock import file_lock
from .models import AccountMeta, AccountState

class StorageEngine:
    def __init__(self, promux_home: Optional[Path] = None, gemini_home: Optional[Path] = None):
        self.home = Path(promux_home or PROMUX_HOME)
        self.gemini_home = Path(gemini_home or GEMINI_CLI_HOME)
        self.accounts_dir = self.home / "accounts"
        self.state_file = self.home / "state.json"
        self.lock_file = self.home / "manager.lock"
        self.live_token = self.gemini_home / "antigravity-oauth-token"

        self.accounts_dir.mkdir(parents=True, exist_ok=True)

    def _atomic_write_json(self, target_path: Path, data: Dict[str, Any]):
        tmp_path = target_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target_path)

    def load_state(self) -> Dict[str, Any]:
        if not self.state_file.exists():
            return {"active": None, "accounts": {}}
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"active": None, "accounts": {}}

    def save_state(self, state: Dict[str, Any]):
        with file_lock(self.lock_file):
            self._atomic_write_json(self.state_file, state)

    def save_profile(
        self,
        name: str,
        email: Optional[str] = None,
        project_id: Optional[str] = None,
        plan_type: str = "STANDARD"
    ) -> AccountMeta:
        if not self.live_token.exists():
            raise FileNotFoundError(f"Active token file not found at {self.live_token}")

        with file_lock(self.lock_file):
            acct_dir = self.accounts_dir / name
            acct_dir.mkdir(parents=True, exist_ok=True)
            dest_token = acct_dir / "antigravity-oauth-token"
            shutil.copy2(self.live_token, dest_token)
            dest_token.chmod(0o600)

            state = self.load_state()
            acct = AccountMeta(
                name=name,
                enabled=True,
                saved_at=datetime.utcnow(),
                last_used_at=datetime.utcnow(),
                email=email,
                project_id=project_id,
                plan_type=plan_type,
            )
            state["accounts"][name] = acct.to_dict()
            if not state.get("active"):
                state["active"] = name
            self._atomic_write_json(self.state_file, state)
            return acct

    def switch_profile(self, name: str) -> bool:
        with file_lock(self.lock_file):
            state = self.load_state()
            if name not in state.get("accounts", {}):
                return False

            src_token = self.accounts_dir / name / "antigravity-oauth-token"
            if not src_token.exists():
                return False

            self.live_token.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_token, self.live_token)
            self.live_token.chmod(0o600)

            state["active"] = name
            state["accounts"][name]["last_used_at"] = datetime.utcnow().isoformat()
            self._atomic_write_json(self.state_file, state)
            return True

    def remove_profile(self, name: str) -> bool:
        with file_lock(self.lock_file):
            state = self.load_state()
            if name not in state.get("accounts", {}):
                return False

            acct_dir = self.accounts_dir / name
            if acct_dir.exists():
                shutil.rmtree(acct_dir, ignore_errors=True)

            del state["accounts"][name]
            if state.get("active") == name:
                state["active"] = None
            self._atomic_write_json(self.state_file, state)
            return True

    def get_active_profile(self) -> Optional[str]:
        state = self.load_state()
        return state.get("active")

    def get_account(self, name: str) -> Optional[AccountMeta]:
        state = self.load_state()
        data = state.get("accounts", {}).get(name)
        return AccountMeta.from_dict(data) if data else None

    def list_accounts(self) -> List[AccountMeta]:
        state = self.load_state()
        return [AccountMeta.from_dict(d) for d in state.get("accounts", {}).values()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_storage.py`
Expected: PASS.

- [ ] **Step 5: Git commit**

```bash
git add src/promux/storage.py tests/test_storage.py
git commit -m "feat(storage): implement profile vaulting, atomic state management and permissions"
```

---

### Task 5: Cloud Code Assist & UserInfo REST Client (`quota.py`)

**Files:**
- Create: `src/promux/quota.py`
- Test: `tests/test_quota.py`

**Interfaces:**
- Consumes: `constants.py`, `models.py`
- Produces: `QuotaClient` class with `load_metadata()`, `get_quota()`, and `fetch_email()`.

- [ ] **Step 1: Write failing unit test for quota client with mocked API responses**

`tests/test_quota.py`:
```python
import json
import pytest
from promux.quota import QuotaClient
from promux.models import QuotaSummary

MOCK_METADATA_RESP = {
    "cloudaicompanionProject": "aicode-consumers",
    "currentTier": {"id": "free-tier", "name": "Antigravity"}
}

MOCK_QUOTA_RESP = {
    "groups": [
        {
            "displayName": "Gemini Models",
            "buckets": [
                {
                    "bucketId": "gemini-weekly",
                    "window": "weekly",
                    "remainingFraction": 0.57,
                    "resetTime": "2026-09-08T17:46:10Z"
                },
                {
                    "bucketId": "gemini-5h",
                    "window": "5h",
                    "remainingFraction": 0.85,
                    "resetTime": "2026-09-03T22:55:18Z"
                }
            ]
        },
        {
            "displayName": "Claude and GPT models",
            "buckets": [
                {
                    "bucketId": "3p-weekly",
                    "window": "weekly",
                    "remainingFraction": 0.43,
                    "resetTime": "2026-09-09T01:11:22Z"
                },
                {
                    "bucketId": "3p-5h",
                    "window": "5h",
                    "remainingFraction": 0.25,
                    "resetTime": "2026-09-04T02:36:43Z"
                }
            ]
        }
    ]
}

MOCK_USERINFO_RESP = {
    "email": "developer@manova.space",
    "email_verified": True
}

def test_quota_client_parsing(monkeypatch):
    client = QuotaClient(token="mock_token")

    def mock_post(endpoint, payload):
        if "loadCodeAssist" in endpoint:
            return MOCK_METADATA_RESP
        if "retrieveUserQuotaSummary" in endpoint:
            return MOCK_QUOTA_RESP
        return {}

    def mock_get(url):
        return MOCK_USERINFO_RESP

    monkeypatch.setattr(client, "_post", mock_post)
    monkeypatch.setattr(client, "_get", mock_get)

    meta = client.load_metadata()
    assert meta["project_id"] == "aicode-consumers"

    quota = client.get_quota("aicode-consumers")
    assert quota.gemini_5h_remaining == 0.85
    assert quota.gemini_weekly_remaining == 0.57
    assert quota.third_party_5h_remaining == 0.25
    assert quota.third_party_weekly_remaining == 0.43
    assert quota.min_short_window == 0.25

    email = client.fetch_email()
    assert email == "developer@manova.space"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_quota.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'promux.quota'`).

- [ ] **Step 3: Implement `src/promux/quota.py`**

```python
import json
import urllib.error
import urllib.request
from typing import Dict, Any, Optional
from .constants import (
    CODE_ASSIST_BASE_URL,
    LOAD_ENDPOINT,
    QUOTA_ENDPOINT,
    USERINFO_URL,
    USER_AGENT,
    DEFAULT_HTTP_TIMEOUT
)
from .models import QuotaSummary

class QuotaClient:
    def __init__(self, token: str, timeout: float = DEFAULT_HTTP_TIMEOUT):
        self.token = token
        self.timeout = timeout

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{CODE_ASSIST_BASE_URL}{endpoint}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, url: str) -> Dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "User-Agent": USER_AGENT,
            },
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def fetch_email(self) -> Optional[str]:
        try:
            data = self._get(USERINFO_URL)
            return data.get("email")
        except Exception:
            return None

    def load_metadata(self) -> Dict[str, Any]:
        payload = {
            "metadata": {
                "ideType": "ANTIGRAVITY",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI"
            }
        }
        resp = self._post(LOAD_ENDPOINT, payload)
        project = resp.get("cloudaicompanionProject")
        if isinstance(project, dict):
            project_id = project.get("id", "")
        else:
            project_id = str(project) if project else ""

        tier_info = resp.get("currentTier", {})
        return {
            "project_id": project_id,
            "tier": tier_info.get("id", "free-tier"),
            "plan_name": tier_info.get("name", "Antigravity")
        }

    def get_quota(self, project_id: str) -> QuotaSummary:
        resp = self._post(QUOTA_ENDPOINT, {"project": project_id})
        qs = QuotaSummary()

        for group in resp.get("groups", []):
            group_name = group.get("displayName", "").lower()
            for bucket in group.get("buckets", []):
                window = bucket.get("window", "").lower()
                rem = bucket.get("remainingFraction", 1.0)
                reset = bucket.get("resetTime")

                if "gemini" in group_name:
                    if window == "5h" or "short" in window:
                        qs.gemini_5h_remaining = rem
                        qs.gemini_5h_reset = reset
                    elif window == "weekly":
                        qs.gemini_weekly_remaining = rem
                        qs.gemini_weekly_reset = reset
                else:  # 3p / Claude / GPT
                    if window == "5h" or "short" in window:
                        qs.third_party_5h_remaining = rem
                        qs.third_party_5h_reset = reset
                    elif window == "weekly":
                        qs.third_party_weekly_remaining = rem
                        qs.third_party_weekly_reset = reset

        return qs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_quota.py`
Expected: PASS.

- [ ] **Step 5: Git commit**

```bash
git add src/promux/quota.py tests/test_quota.py
git commit -m "feat(quota): implement Cloud Code Assist and UserInfo REST client"
```

---

### Task 6: Failover Coordinator & Candidate Selection (`failover.py`)

**Files:**
- Create: `src/promux/failover.py`
- Test: `tests/test_failover.py`

**Interfaces:**
- Consumes: `storage.py`, `models.py`, `constants.py`
- Produces: `FailoverEngine` with `get_eligible_standby()`, `rotate_next()`, `apply_cooldown()`.

- [ ] **Step 1: Write failing unit tests for rotation and cooldown**

`tests/test_failover.py`:
```python
from datetime import datetime, timedelta
import pytest
from promux.failover import FailoverEngine
from promux.models import AccountMeta, AccountState

class FakeStorage:
    def __init__(self):
        self.state = {"active": "acc1", "accounts": {}}
        self.switched = []

    def load_state(self):
        return self.state

    def save_state(self, state):
        self.state = state

    def switch_profile(self, name):
        if name in self.state["accounts"]:
            self.state["active"] = name
            self.switched.append(name)
            return True
        return False

def test_failover_rotates_to_lru_standby():
    storage = FakeStorage()
    now = datetime.utcnow()
    storage.state["accounts"] = {
        "acc1": AccountMeta(name="acc1", enabled=True, last_used_at=now).to_dict(),
        "acc2": AccountMeta(name="acc2", enabled=True, last_used_at=now - timedelta(hours=2)).to_dict(),
        "acc3": AccountMeta(name="acc3", enabled=True, last_used_at=now - timedelta(hours=5)).to_dict(),
    }

    engine = FailoverEngine(storage)
    candidates = engine.get_eligible_standby(exclude="acc1")
    assert [c.name for c in candidates] == ["acc3", "acc2"]

    res = engine.rotate_next(reason="quota_exhausted", cooldown_minutes=30)
    assert res.success is True
    assert res.from_account == "acc1"
    assert res.to_account == "acc3"
    assert storage.state["active"] == "acc3"

    # acc1 should be in cooldown
    acc1_meta = AccountMeta.from_dict(storage.state["accounts"]["acc1"])
    assert acc1_meta.state == AccountState.COOLDOWN

def test_failover_no_candidates():
    storage = FakeStorage()
    storage.state["accounts"] = {
        "acc1": AccountMeta(name="acc1", enabled=True).to_dict()
    }
    engine = FailoverEngine(storage)
    res = engine.rotate_next(reason="quota_exhausted")
    assert res.success is False
    assert "No eligible standby" in res.reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_failover.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'promux.failover'`).

- [ ] **Step 3: Implement `src/promux/failover.py`**

```python
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from .constants import DEFAULT_COOLDOWN_MINUTES
from .models import AccountMeta, AccountState, RotationResult
from .storage import StorageEngine

class FailoverEngine:
    def __init__(self, storage: StorageEngine):
        self.storage = storage

    def get_eligible_standby(self, exclude: Optional[str] = None) -> List[AccountMeta]:
        state = self.storage.load_state()
        eligible = []
        for name, data in state.get("accounts", {}).items():
            if name == exclude:
                continue
            acct = AccountMeta.from_dict(data)
            if acct.state == AccountState.STANDBY:
                eligible.append(acct)

        # Sort LRU: least recently used first (None values first)
        eligible.sort(key=lambda a: a.last_used_at or datetime.min)
        return eligible

    def apply_cooldown(self, name: str, minutes: int = DEFAULT_COOLDOWN_MINUTES):
        state = self.storage.load_state()
        if name in state.get("accounts", {}):
            until = datetime.utcnow() + timedelta(minutes=minutes)
            state["accounts"][name]["cooldown_until"] = until.isoformat()
            self.storage.save_state(state)

    def rotate_next(
        self,
        reason: str = "manual",
        cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES
    ) -> RotationResult:
        state = self.storage.load_state()
        active_name = state.get("active")

        # Put active into cooldown
        cooldown_until = None
        if active_name and active_name in state.get("accounts", {}):
            cooldown_until = datetime.utcnow() + timedelta(minutes=cooldown_minutes)
            state["accounts"][active_name]["cooldown_until"] = cooldown_until.isoformat()
            self.storage.save_state(state)

        candidates = self.get_eligible_standby(exclude=active_name)
        if not candidates:
            return RotationResult(
                success=False,
                from_account=active_name,
                to_account=None,
                reason=f"No eligible standby accounts ({reason})",
                cooldown_until=cooldown_until
            )

        next_target = candidates[0].name
        if not self.storage.switch_profile(next_target):
            return RotationResult(
                success=False,
                from_account=active_name,
                to_account=next_target,
                reason=f"Failed to switch token to {next_target}",
                cooldown_until=cooldown_until
            )

        return RotationResult(
            success=True,
            from_account=active_name,
            to_account=next_target,
            reason=reason,
            cooldown_until=cooldown_until
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_failover.py`
Expected: PASS.

- [ ] **Step 5: Git commit**

```bash
git add src/promux/failover.py tests/test_failover.py
git commit -m "feat(failover): implement LRU candidate selection and cooldown rotation"
```

---

### Task 7: Reactive Log Watcher Daemon (`watch.py`)

**Files:**
- Create: `src/promux/watch.py`
- Test: `tests/test_watch.py`

**Interfaces:**
- Consumes: `failover.py`, `constants.py`
- Produces: `LogWatcher` class with `run_once()`, `run_forever()`, and `parse_reset_minutes()`.

- [ ] **Step 1: Write failing unit tests for log pattern detection and file rotation**

`tests/test_watch.py`:
```python
import time
from pathlib import Path
from promux.watch import LogWatcher, LogMatch
from promux.models import RotationResult

class FakeFailover:
    def __init__(self):
        self.rotations = []

    def rotate_next(self, reason, cooldown_minutes=60):
        self.rotations.append((reason, cooldown_minutes))
        return RotationResult(success=True, from_account="a", to_account="b", reason=reason)

def test_log_watcher_detects_individual_quota(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Starting session\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    # Seek to end
    watcher.init_offsets()

    # Append quota error
    with open(log_file, "a") as f:
        f.write("Error: Individual quota reached. Resets in 45m.\n")

    matches = watcher.run_once()
    assert len(matches) == 1
    assert matches[0].pattern == "INDIVIDUAL_QUOTA"
    assert matches[0].reset_hint == "45m"

def test_log_watcher_rotation_resilience(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Existing line 1\nExisting line 2\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    watcher.init_offsets()

    # Truncate / rewrite file (log rotation)
    log_file.write_text("RESOURCE_EXHAUSTED (code 429)\n")

    matches = watcher.run_once()
    assert len(matches) == 1
    assert matches[0].pattern == "RESOURCE_EXHAUSTED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_watch.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'promux.watch'`).

- [ ] **Step 3: Implement `src/promux/watch.py`**

```python
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Callable, Dict

from .constants import (
    CLI_LOG,
    LOG_DIR,
    DEFAULT_POLL_SECONDS,
    DEFAULT_COOLDOWN_MINUTES,
    INDIVIDUAL_QUOTA_RE,
    RESOURCE_EXHAUSTED_RE,
    WEEKLY_QUOTA_RE,
    RESET_HINT_RE
)
from .failover import FailoverEngine

@dataclass
class LogMatch:
    pattern: str
    line: str
    reset_hint: Optional[str] = None
    file_path: Optional[str] = None

class LogWatcher:
    PATTERNS = [
        ("INDIVIDUAL_QUOTA", INDIVIDUAL_QUOTA_RE),
        ("RESOURCE_EXHAUSTED", RESOURCE_EXHAUSTED_RE),
        ("WEEKLY_QUOTA", WEEKLY_QUOTA_RE),
    ]

    def __init__(
        self,
        failover: FailoverEngine,
        log_files: Optional[List[Path]] = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS
    ):
        self.failover = failover
        self._custom_log_files = log_files
        self.poll_seconds = poll_seconds
        self.offsets: Dict[Path, int] = {}
        self.running = False

    def get_log_files(self) -> List[Path]:
        if self._custom_log_files is not None:
            return [p for p in self._custom_log_files if p.exists()]
        files = []
        if CLI_LOG.exists():
            files.append(CLI_LOG)
        if LOG_DIR.exists():
            files.extend(sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:5])
        return files

    def init_offsets(self):
        for f in self.get_log_files():
            try:
                self.offsets[f] = f.stat().st_size
            except OSError:
                self.offsets[f] = 0

    def parse_reset_minutes(self, hint: Optional[str]) -> int:
        if not hint:
            return DEFAULT_COOLDOWN_MINUTES
        hint = hint.strip().lower().lstrip("~")
        m = re.search(r"(\d+)\s*h(?:our)?", hint)
        hours = int(m.group(1)) if m else 0
        m_min = re.search(r"(\d+)\s*m(?:in)?", hint)
        mins = int(m_min.group(1)) if m_min else 0
        total = hours * 60 + mins
        return total if total > 0 else DEFAULT_COOLDOWN_MINUTES

    def run_once(self) -> List[LogMatch]:
        matches = []
        for path in self.get_log_files():
            prev_offset = self.offsets.get(path, 0)
            try:
                size = path.stat().st_size
                if size < prev_offset:  # File was rotated/truncated
                    prev_offset = 0

                if size == prev_offset:
                    continue

                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(prev_offset)
                    new_lines = f.readlines()
                    self.offsets[path] = f.tell()

                for line in new_lines:
                    for name, pat in self.PATTERNS:
                        if pat.search(line):
                            hint_match = RESET_HINT_RE.search(line)
                            hint = hint_match.group("reset") if hint_match else None
                            matches.append(LogMatch(
                                pattern=name,
                                line=line.strip(),
                                reset_hint=hint,
                                file_path=str(path)
                            ))
                            break
            except OSError:
                continue
        return matches

    def run_forever(
        self,
        on_match: Optional[Callable[[LogMatch], None]] = None,
        cooldown_minutes: Optional[int] = None
    ):
        self.running = True
        self.init_offsets()

        while self.running:
            try:
                matches = self.run_once()
                for m in matches:
                    if on_match:
                        on_match(m)
                    cool = cooldown_minutes or self.parse_reset_minutes(m.reset_hint)
                    self.failover.rotate_next(reason=f"reactive: {m.pattern}", cooldown_minutes=cool)
                time.sleep(self.poll_seconds)
            except KeyboardInterrupt:
                self.running = False
                break
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_watch.py`
Expected: PASS.

- [ ] **Step 5: Git commit**

```bash
git add src/promux/watch.py tests/test_watch.py
git commit -m "feat(watch): implement reactive log tailing and quota match triggers"
```

---

### Task 8: CLI Implementation & Output Formatters (`cli.py`)

**Files:**
- Create: `src/promux/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: All modules
- Produces: `promux` executable CLI supporting `list`, `save`, `switch`, `next`, `quota`, `whoami`, `remove`, `watch` with `--json`.

- [ ] **Step 1: Write CLI execution tests**

`tests/test_cli.py`:
```python
import json
import pytest
from promux.cli import main

def test_cli_list_json(tmp_path, monkeypatch, capsys):
    promux_home = tmp_path / ".promux"
    monkeypatch.setenv("PROMUX_HOME", str(promux_home))

    rc = main(["list", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert data == []

def test_cli_save_and_whoami(tmp_path, sample_token_dict, monkeypatch, capsys):
    promux_home = tmp_path / ".promux"
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir(parents=True)
    live_token = gemini_home / "antigravity-oauth-token"
    live_token.write_text(json.dumps(sample_token_dict))
    live_token.chmod(0o600)

    monkeypatch.setenv("PROMUX_HOME", str(promux_home))
    monkeypatch.setenv("PROMUX_GEMINI_HOME", str(gemini_home))

    # Save
    rc = main(["save", "demo", "--email", "demo@test.com"])
    assert rc == 0

    # Whoami
    rc = main(["whoami", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["active"] == "demo"
    assert res["email"] == "demo@test.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'promux.cli'`).

- [ ] **Step 3: Implement `src/promux/cli.py`**

Implement complete `argparse` CLI with table formatting and JSON output for all subcommands:
- `list`: Shows table with active marker (`*`), name, state, email, cooldown, last used.
- `save <name> [--email]`: Reads live token, fetches email via `QuotaClient.fetch_email()` if unsupplied, persists profile.
- `switch <name>`: Hot-swaps to `<name>`.
- `next [--reason] [--cooldown]`: Triggers `failover.rotate_next()`.
- `quota [name]`: Checks quota via Cloud Code Assist API.
- `whoami`: Displays active profile name, email, token expiry.
- `remove <name>`: Removes account from vault.
- `watch [--poll-seconds] [--cooldown]`: Starts the foreground watcher daemon with pretty console output.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py`
Expected: PASS.

- [ ] **Step 5: Git commit**

```bash
git add src/promux/cli.py tests/test_cli.py
git commit -m "feat(cli): implement complete promux CLI and ASCII/JSON formatters"
```

---

### Task 9: End-to-End Integration & Real CLI Verification

**Files:**
- Modify: `README.md`
- Test: Live CLI invocation on host

**Interfaces:**
- Produces: Installed and verified `promux` in system PATH (`~/.local/bin/promux`).

- [ ] **Step 1: Install `promux` in editable mode**

Run: `pip install -e . --break-system-packages`
Expected: Successfully installed.

- [ ] **Step 2: Verify CLI commands work against real live token**

Run: `promux whoami`
Expected: Outputs details of active token (`manovaplatform@gmail.com`).
Run: `promux list`
Expected: Outputs table.
Run: `promux save main`
Expected: Saves active token snapshot as `main`.
Run: `promux quota`
Expected: Queries and displays real Cloud Code quota fractions for Gemini and Claude/GPT models.

- [ ] **Step 3: Run full test suite with coverage check**

Run: `pytest -v`
Expected: All tests pass.

- [ ] **Step 4: Final commit**

```bash
git add README.md
git commit -m "docs: finalize promux documentation and verification"
```
