from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Type annotations for lazily evaluated module attributes (PEP 562)
PROMUX_HOME: Path
ACCOUNTS_DIR: Path
STATE_FILE: Path
LOCK_FILE: Path
CONFIG_FILE: Path
CACHE_DIR: Path
QUOTA_CACHE_FILE: Path
GEMINI_CLI_HOME: Path
LIVE_TOKEN: Path
CLI_LOG: Path
LOG_DIR: Path
DEFAULT_HTTP_TIMEOUT: float

_LAZY_PATH_NAMES: frozenset[str] = frozenset({
    "PROMUX_HOME",
    "ACCOUNTS_DIR",
    "STATE_FILE",
    "LOCK_FILE",
    "CONFIG_FILE",
    "CACHE_DIR",
    "QUOTA_CACHE_FILE",
    "GEMINI_CLI_HOME",
    "LIVE_TOKEN",
    "CLI_LOG",
    "LOG_DIR",
})
_ALL_LAZY_NAMES: frozenset[str] = _LAZY_PATH_NAMES | frozenset({"DEFAULT_HTTP_TIMEOUT"})

# Clean up any cached lazy attributes on module reload
for _name in _ALL_LAZY_NAMES:
    globals().pop(_name, None)

_LAZY_PATHS: dict[str, Path] = {}


def _build_lazy_paths() -> dict[str, Path]:
    if "PROMUX_HOME" in globals() and isinstance(globals()["PROMUX_HOME"], Path):
        home = globals()["PROMUX_HOME"]
    else:
        home = Path(os.environ.get("PROMUX_HOME", Path.home() / ".promux"))

    if "GEMINI_CLI_HOME" in globals() and isinstance(globals()["GEMINI_CLI_HOME"], Path):
        gemini_home = globals()["GEMINI_CLI_HOME"]
    else:
        gemini_home = Path(
            os.environ.get("PROMUX_GEMINI_HOME", Path.home() / ".gemini" / "antigravity-cli")
        )

    cache_dir = home / "cache"
    return {
        "PROMUX_HOME": home,
        "ACCOUNTS_DIR": home / "accounts",
        "STATE_FILE": home / "state.json",
        "LOCK_FILE": home / "manager.lock",
        "CONFIG_FILE": home / "config.json",
        "CACHE_DIR": cache_dir,
        "QUOTA_CACHE_FILE": cache_dir / "quota.json",
        "GEMINI_CLI_HOME": gemini_home,
        "LIVE_TOKEN": gemini_home / "antigravity-oauth-token",
        "CLI_LOG": gemini_home / "cli.log",
        "LOG_DIR": gemini_home / "log",
    }


def _get_lazy_path(name: str) -> Path:
    val = globals().get(name)
    if isinstance(val, Path):
        return val
    if not _LAZY_PATHS:
        _LAZY_PATHS.update(_build_lazy_paths())
        for k, v in _LAZY_PATHS.items():
            globals().setdefault(k, v)
    return _LAZY_PATHS[name]


def __getattr__(name: str) -> Any:
    if name in _LAZY_PATH_NAMES:
        return _get_lazy_path(name)
    if name == "DEFAULT_HTTP_TIMEOUT":
        timeout = get_default_http_timeout()
        globals()[name] = timeout
        return timeout
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_ALL_LAZY_NAMES))


# API Endpoints
CODE_ASSIST_BASE_URL = os.environ.get(
    "PROMUX_CODE_ASSIST_URL", "https://cloudcode-pa.googleapis.com"
)
FALLBACK_CODE_ASSIST_BASE_URL = "https://daily-cloudcode-pa.googleapis.com"
LOAD_ENDPOINT = "/v1internal:loadCodeAssist"
QUOTA_ENDPOINT = "/v1internal:retrieveUserQuotaSummary"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
DEFAULT_OAUTH_CLIENT_ID = "REDACTED_OAUTH_CLIENT_ID"
DEFAULT_OAUTH_CLIENT_SECRET = "REDACTED_OAUTH_CLIENT_SECRET"

OAUTH_TOKEN_URL = os.environ.get("PROMUX_OAUTH_TOKEN_URL", "https://oauth2.googleapis.com/token")
OAUTH_CLIENT_ID = os.environ.get("PROMUX_OAUTH_CLIENT_ID", DEFAULT_OAUTH_CLIENT_ID)
OAUTH_CLIENT_SECRET = os.environ.get("PROMUX_OAUTH_CLIENT_SECRET", DEFAULT_OAUTH_CLIENT_SECRET)
DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS = 60
DEFAULT_PROACTIVE_REFRESH_INTERVAL_SECONDS = 900  # 15 minutes
USER_AGENT = "antigravity"


def detect_code_assist_url(gemini_home: Path | None = None) -> str:
    """Detect whether Antigravity CLI uses daily-cloudcode or prod endpoint."""
    env_url = os.environ.get("PROMUX_CODE_ASSIST_URL")
    if env_url:
        return env_url
    gh = gemini_home or _get_lazy_path("GEMINI_CLI_HOME")
    cli_log = gh / "cli.log"
    if cli_log.exists():
        try:
            with open(cli_log, encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-100:]
                for line in reversed(lines):
                    m = re.search(r"https://([a-zA-Z0-9.-]*cloudcode-pa\.googleapis\.com)", line)
                    if m:
                        return f"https://{m.group(1)}"
        except OSError:
            logger.debug("Failed to read cli.log for code assist URL", exc_info=True)
    return CODE_ASSIST_BASE_URL


# Regex Signatures (SPEC §5.1)
INDIVIDUAL_QUOTA_RE = re.compile(r"(?i)Individual quota reached")
RESOURCE_EXHAUSTED_RE = re.compile(
    r"(?i)RESOURCE_EXHAUSTED\s*\(\s*code\s*429\s*\)|RESOURCE_EXHAUSTED"
)
WEEKLY_QUOTA_RE = re.compile(r"(?i)weekly quota reached")
RESET_HINT_RE = re.compile(r"(?i)Resets in\s+(?P<reset>~?[^.)\n]+)")

# Defaults
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_COOLDOWN_MINUTES = 60
DEFAULT_LOCK_TIMEOUT = 10.0


def get_default_http_timeout(config_file: Path | None = None) -> float:
    """Resolve default HTTP timeout from env var, config.json, or 5.0s default."""
    env_val = os.environ.get("PROMUX_HTTP_TIMEOUT")
    if env_val:
        try:
            val = float(env_val)
            if val > 0:
                return val
        except ValueError:
            logger.debug("Invalid float in PROMUX_HTTP_TIMEOUT: %r", env_val, exc_info=True)

    cfg = config_file or _get_lazy_path("CONFIG_FILE")
    if cfg.exists():
        try:
            with open(cfg, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    t = data.get("http_timeout_seconds")
                    if isinstance(t, (int, float)) and t > 0:
                        return float(t)
        except Exception:
            logger.debug("Failed to read http_timeout_seconds from config: %s", cfg, exc_info=True)

    return 5.0


DEFAULT_QUOTA_CACHE_TTL_SECONDS = 300

__all__ = [
    "ACCOUNTS_DIR",
    "CACHE_DIR",
    "CLI_LOG",
    "CODE_ASSIST_BASE_URL",
    "CONFIG_FILE",
    "DEFAULT_COOLDOWN_MINUTES",
    "DEFAULT_HTTP_TIMEOUT",
    "DEFAULT_LOCK_TIMEOUT",
    "DEFAULT_OAUTH_CLIENT_ID",
    "DEFAULT_OAUTH_CLIENT_SECRET",
    "DEFAULT_POLL_SECONDS",
    "DEFAULT_PROACTIVE_REFRESH_INTERVAL_SECONDS",
    "DEFAULT_QUOTA_CACHE_TTL_SECONDS",
    "DEFAULT_TOKEN_EXPIRY_BUFFER_SECONDS",
    "FALLBACK_CODE_ASSIST_BASE_URL",
    "GEMINI_CLI_HOME",
    "INDIVIDUAL_QUOTA_RE",
    "LIVE_TOKEN",
    "LOAD_ENDPOINT",
    "LOCK_FILE",
    "LOG_DIR",
    "OAUTH_CLIENT_ID",
    "OAUTH_CLIENT_SECRET",
    "OAUTH_TOKEN_URL",
    "PROMUX_HOME",
    "QUOTA_CACHE_FILE",
    "QUOTA_ENDPOINT",
    "RESET_HINT_RE",
    "RESOURCE_EXHAUSTED_RE",
    "STATE_FILE",
    "USER_AGENT",
    "USERINFO_URL",
    "WEEKLY_QUOTA_RE",
    "detect_code_assist_url",
    "get_default_http_timeout",
]
