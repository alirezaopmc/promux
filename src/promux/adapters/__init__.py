from .agy import AgyAdapter
from .base import BaseToolAdapter
from .registry import ToolRegistry
from .stubs import ClaudeAdapter, CodexAdapter, CursorAdapter


def get_default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(AgyAdapter(), default=True)
    reg.register(ClaudeAdapter())
    reg.register(CodexAdapter())
    reg.register(CursorAdapter())
    return reg


__all__ = [
    "AgyAdapter",
    "BaseToolAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "ToolRegistry",
    "get_default_registry",
]
