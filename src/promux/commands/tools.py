"""Tool registry and shell completion commands."""

from __future__ import annotations

import json
import logging

from ..adapters import ToolRegistry
from ..completion import generate_bash_completion, generate_zsh_completion

logger = logging.getLogger(__name__)


def cmd_tools(registry: ToolRegistry, json_out: bool = False) -> int:
    if json_out:
        out = []
        for tool in registry.list_all():
            storage = tool.get_storage()
            out.append(
                {
                    "tool": tool.name,
                    "name": tool.display_name,
                    "active_profile": storage.get_active_profile(),
                    "capabilities": tool.list_capabilities(),
                    "default": tool.name == registry.default_tool().name,
                }
            )
        print(json.dumps(out, indent=2))
        return 0

    print(f"{'TOOL':<10}{'NAME':<22}{'ACTIVE PROFILE':<16}{'CAPABILITIES'}")
    print("-" * 73)
    for tool in registry.list_all():
        storage = tool.get_storage()
        active = storage.get_active_profile() or "-"
        caps = tool.list_capabilities()
        if getattr(tool, "is_scaffolded", False):
            caps_str = f"{', '.join(caps)} (scaffolded)"
        else:
            caps_str = ", ".join(caps)
        print(f"{tool.name:<10}{tool.display_name:<22}{active:<16}{caps_str}")
    return 0


def cmd_completion(shell: str) -> int:
    if shell == "bash":
        print(generate_bash_completion(), end="")
        return 0
    elif shell == "zsh":
        print(generate_zsh_completion(), end="")
        return 0
    return 1
