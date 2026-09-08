import os
from pathlib import Path

from ..constants import PROMUX_HOME
from ..storage import StorageEngine
from .base import BaseToolAdapter


class ClaudeAdapter(BaseToolAdapter):
    name = "claude"
    display_name = "Claude Code"
    is_scaffolded = True

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        env_home = Path(os.environ["PROMUX_HOME"]) if "PROMUX_HOME" in os.environ else None
        home = Path(promux_home or env_home or PROMUX_HOME) / "tools" / self.name
        return StorageEngine(promux_home=home)


class CodexAdapter(BaseToolAdapter):
    name = "codex"
    display_name = "Codex CLI"
    is_scaffolded = True

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        env_home = Path(os.environ["PROMUX_HOME"]) if "PROMUX_HOME" in os.environ else None
        home = Path(promux_home or env_home or PROMUX_HOME) / "tools" / self.name
        return StorageEngine(promux_home=home)


class CursorAdapter(BaseToolAdapter):
    name = "cursor"
    display_name = "Cursor CLI"
    is_scaffolded = True

    def get_storage(self, promux_home: Path | None = None) -> StorageEngine:
        env_home = Path(os.environ["PROMUX_HOME"]) if "PROMUX_HOME" in os.environ else None
        home = Path(promux_home or env_home or PROMUX_HOME) / "tools" / self.name
        return StorageEngine(promux_home=home)
