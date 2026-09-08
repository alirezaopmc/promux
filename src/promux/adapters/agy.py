from pathlib import Path
from typing import Any

from ..constants import GEMINI_CLI_HOME, PROMUX_HOME
from ..models import QuotaSummary
from ..quota import QuotaClient
from ..storage import StorageEngine
from .base import BaseToolAdapter


class AgyAdapter(BaseToolAdapter):
    """Adapter for Google Antigravity CLI (`agy`)."""

    name = "agy"
    display_name = "Antigravity CLI"

    def __init__(self, gemini_home: Path | None = None) -> None:
        self.gemini_home = Path(gemini_home or GEMINI_CLI_HOME)

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        """Return StorageEngine scoped for Antigravity, preserving backward compatibility."""
        home = Path(promux_home or PROMUX_HOME)
        return StorageEngine(promux_home=home, gemini_home=self.gemini_home)

    @property
    def supports_quota(self) -> bool:
        return True

    def fetch_quota(
        self, storage: StorageEngine, account_name: str
    ) -> tuple[QuotaSummary | None, str | None, str | None]:
        from ..cli import _fetch_account_quota

        return _fetch_account_quota(storage, account_name)

    @property
    def supports_watch(self) -> bool:
        return True

    def create_watcher(self, storage: StorageEngine) -> Any:
        from ..watch import LogWatcher

        return LogWatcher(storage=storage, gemini_home=self.gemini_home)

    @property
    def supports_refresh(self) -> bool:
        return True

    def refresh_account(
        self, storage: StorageEngine, account_name: str, force: bool = False
    ) -> tuple[bool, str]:
        from ..cli import _refresh_account_token

        return _refresh_account_token(storage, account_name, force=force)
