from .base import BaseToolAdapter


class ToolRegistry:
    """Central registry of supported developer CLI tool adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, BaseToolAdapter] = {}
        self._default_tool: str | None = None

    def register(self, adapter: BaseToolAdapter, default: bool = False) -> None:
        self._adapters[adapter.name] = adapter
        if default or self._default_tool is None:
            self._default_tool = adapter.name

    def get(self, name: str) -> BaseToolAdapter:
        if name not in self._adapters:
            available = ", ".join(sorted(self._adapters.keys()))
            raise KeyError(f"Unknown tool '{name}'. Available tools: {available}")
        return self._adapters[name]

    def has_tool(self, name: str) -> bool:
        return name in self._adapters

    def list_all(self) -> list[BaseToolAdapter]:
        return list(self._adapters.values())

    def default_tool(self) -> BaseToolAdapter:
        if not self._default_tool or self._default_tool not in self._adapters:
            raise RuntimeError("No default tool registered.")
        return self._adapters[self._default_tool]
