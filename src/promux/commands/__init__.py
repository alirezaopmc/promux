"""Command handlers for promux CLI. One module per command group."""

from .profile import (
    cmd_list,
    cmd_next,
    cmd_remove,
    cmd_save,
    cmd_switch,
    cmd_whoami,
)
from .quota import (
    cmd_quota,
    cmd_refresh,
)
from .tools import (
    cmd_completion,
    cmd_tools,
)
from .watch import (
    cmd_watch,
)

__all__ = [
    "cmd_completion",
    "cmd_list",
    "cmd_next",
    "cmd_quota",
    "cmd_refresh",
    "cmd_remove",
    "cmd_save",
    "cmd_switch",
    "cmd_tools",
    "cmd_watch",
    "cmd_whoami",
]
