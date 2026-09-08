from pathlib import Path

from ..constants import PROMUX_HOME
from ..storage import StorageEngine
from .base import BaseToolAdapter


class ClaudeAdapter(BaseToolAdapter):
    name = "claude"
    display_name = "Claude Code"

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        home = (promux_home or PROMUX_HOME) / "tools" / self.name
        return StorageEngine(promux_home=home)


class CodexAdapter(BaseToolAdapter):
    name = "codex"
    display_name = "Codex CLI"

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        home = (promux_home or PROMUX_HOME) / "tools" / self.name
        return StorageEngine(promux_home=home)


class CursorAdapter(BaseToolAdapter):
    name = "cursor"
    display_name = "Cursor CLI"

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        home = (promux_home or PROMUX_HOME) / "tools" / self.name
        return StorageEngine(promux_home=home)
