import os
from pathlib import Path
from typing import Any

from ..constants import GEMINI_CLI_HOME, PROMUX_HOME
from ..models import QuotaSummary
from ..storage import StorageEngine
from .base import BaseToolAdapter


class AgyAdapter(BaseToolAdapter):
    """Adapter for Google Antigravity CLI (`agy`)."""

    name = "agy"
    display_name = "Antigravity CLI"

    def __init__(self, gemini_home: Path | None = None) -> None:
        self._gemini_home = Path(gemini_home) if gemini_home else None

    @property
    def gemini_home(self) -> Path:
        if self._gemini_home is not None:
            return self._gemini_home
        if "PROMUX_GEMINI_HOME" in os.environ:
            return Path(os.environ["PROMUX_GEMINI_HOME"])
        return GEMINI_CLI_HOME

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        """Return StorageEngine scoped for Antigravity under tools/agy."""
        env_home = Path(os.environ["PROMUX_HOME"]) if "PROMUX_HOME" in os.environ else None
        base = Path(promux_home or env_home or PROMUX_HOME)
        if base.name == self.name and base.parent.name == "tools":
            home = base
        else:
            home = base / "tools" / self.name
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
