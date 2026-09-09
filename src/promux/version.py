"""Version discovery and formatted CLI presentation for Promux."""

from __future__ import annotations

import json
import platform
from typing import Any

from . import __version__
from .adapters import get_default_registry
from .colors import bold, cyan, dim, is_color_enabled


def get_version_info() -> dict[str, Any]:
    """Return dictionary with runtime and tooling version details."""
    registry = get_default_registry()
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()}-{platform.machine()}",
        "tools": registry.list_names(),
        "default_tool": registry.default_tool().name,
    }


def cmd_version(json_out: bool = False, no_color: bool = False) -> int:
    """Print version information in JSON or formatted human-readable text."""
    info = get_version_info()
    if json_out:
        print(json.dumps(info, indent=2))
        return 0

    use_color = is_color_enabled(force_no_color=no_color)
    prog_str = bold(f"promux {info['version']}", enabled=use_color)
    runtime_str = dim(f"(Python {info['python']}, {info['platform']})", enabled=use_color)
    tools_list = []
    for tool in info["tools"]:
        if tool == info["default_tool"]:
            tools_list.append(f"{cyan(tool, enabled=use_color)} (default)")
        else:
            tools_list.append(cyan(tool, enabled=use_color))
    tools_str = ", ".join(tools_list)

    print(f"{prog_str} {runtime_str}")
    print(f"Supported tools: {tools_str}")
    return 0
