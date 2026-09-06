import os
import re
from pathlib import Path

# Paths
PROMUX_HOME = Path(os.environ.get("PROMUX_HOME", Path.home() / ".promux"))
ACCOUNTS_DIR = PROMUX_HOME / "accounts"
STATE_FILE = PROMUX_HOME / "state.json"
LOCK_FILE = PROMUX_HOME / "manager.lock"

# Gemini / Antigravity CLI Paths
GEMINI_CLI_HOME = Path(
    os.environ.get("PROMUX_GEMINI_HOME", Path.home() / ".gemini" / "antigravity-cli")
)
LIVE_TOKEN = GEMINI_CLI_HOME / "antigravity-oauth-token"
CLI_LOG = GEMINI_CLI_HOME / "cli.log"
LOG_DIR = GEMINI_CLI_HOME / "log"

# API Endpoints
CODE_ASSIST_BASE_URL = os.environ.get(
    "PROMUX_CODE_ASSIST_URL", "https://cloudcode-pa.googleapis.com"
)
FALLBACK_CODE_ASSIST_BASE_URL = "https://daily-cloudcode-pa.googleapis.com"
LOAD_ENDPOINT = "/v1internal:loadCodeAssist"
QUOTA_ENDPOINT = "/v1internal:retrieveUserQuotaSummary"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
OAUTH_TOKEN_URL = os.environ.get("PROMUX_OAUTH_TOKEN_URL", "https://oauth2.googleapis.com/token")
OAUTH_CLIENT_ID = os.environ.get("PROMUX_OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.environ.get("PROMUX_OAUTH_CLIENT_SECRET", "")
USER_AGENT = "antigravity"


def detect_code_assist_url(gemini_home: Path | None = None) -> str:
    """Detect whether Antigravity CLI uses daily-cloudcode or prod endpoint."""
    env_url = os.environ.get("PROMUX_CODE_ASSIST_URL")
    if env_url:
        return env_url
    gh = gemini_home or GEMINI_CLI_HOME
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
            pass
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
DEFAULT_HTTP_TIMEOUT = 15.0
