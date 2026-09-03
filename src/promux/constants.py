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
