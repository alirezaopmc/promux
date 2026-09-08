from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..models import QuotaSummary
from ..storage import StorageEngine


class BaseToolAdapter(ABC):
    """Abstract base class for developer CLI tool adapters."""

    name: str
    display_name: str

    @abstractmethod
    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        """Return the scoped StorageEngine configured for this tool."""
        raise NotImplementedError

    @property
    def supports_quota(self) -> bool:
        """Whether this tool supports proactive quota retrieval."""
        return False

    def fetch_quota(
        self, storage: StorageEngine, account_name: str
    ) -> tuple[QuotaSummary | None, str | None, str | None]:
        """Fetch quota summary for a named account (qs, project_id, error)."""
        return None, None, f"Quota inspection not supported for {self.display_name}."

    @property
    def supports_watch(self) -> bool:
        """Whether this tool supports reactive log failover watching."""
        return False

    def create_watcher(self, storage: StorageEngine) -> Any:
        """Create log watcher daemon instance for this tool."""
        raise NotImplementedError(f"Log watching not supported for {self.display_name}.")

    @property
    def supports_refresh(self) -> bool:
        """Whether this tool supports automated token refresh."""
        return False

    def refresh_account(
        self, storage: StorageEngine, account_name: str, force: bool = False
    ) -> tuple[bool, str]:
        """Execute token refresh for a named account."""
        return False, f"Token refresh not supported for {self.display_name}."

    def list_capabilities(self) -> list[str]:
        """Return list of supported capabilities for status display."""
        caps = ["vault"]
        if self.supports_quota:
            caps.append("quota")
        if self.supports_watch:
            caps.append("watch")
        if self.supports_refresh:
            caps.append("refresh")
        return caps
