"""Zero-dependency ANSI color styling and terminal detection engine."""

from __future__ import annotations

import os
import sys
from typing import TextIO

# ANSI escape codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"

__all__ = [
    "BOLD",
    "CYAN",
    "DIM",
    "GREEN",
    "RED",
    "RESET",
    "YELLOW",
    "bold",
    "cyan",
    "dim",
    "green",
    "is_color_enabled",
    "red",
    "style",
    "yellow",
]


def is_color_enabled(stream: TextIO | None = None, force_no_color: bool = False) -> bool:
    """Determine whether color output should be enabled for a given stream.

    Follows standard terminal detection rules:
    1. Returns False if force_no_color is True.
    2. Returns False if NO_COLOR environment variable is set and non-empty.
    3. Returns False if TERM environment variable is set to "dumb".
    4. Returns True if stream is a TTY (hasattr(stream, "isatty") and stream.isatty()), else False.
    """
    if force_no_color:
        return False

    if bool(os.environ.get("NO_COLOR")):
        return False

    if os.environ.get("TERM") == "dumb":
        return False

    target_stream = sys.stdout if stream is None else stream
    try:
        return bool(hasattr(target_stream, "isatty") and target_stream.isatty())
    except Exception:
        return False


def style(text: str, code: str, enabled: bool = True) -> str:
    """Wrap text in an ANSI escape sequence with a reset code, if enabled."""
    if not enabled:
        return text
    return f"{code}{text}{RESET}"


def bold(text: str, enabled: bool = True) -> str:
    """Format text with bold ANSI styling."""
    return style(text, BOLD, enabled=enabled)


def dim(text: str, enabled: bool = True) -> str:
    """Format text with dim ANSI styling."""
    return style(text, DIM, enabled=enabled)


def cyan(text: str, enabled: bool = True) -> str:
    """Format text with cyan ANSI styling."""
    return style(text, CYAN, enabled=enabled)


def green(text: str, enabled: bool = True) -> str:
    """Format text with green ANSI styling."""
    return style(text, GREEN, enabled=enabled)


def yellow(text: str, enabled: bool = True) -> str:
    """Format text with yellow ANSI styling."""
    return style(text, YELLOW, enabled=enabled)


def red(text: str, enabled: bool = True) -> str:
    """Format text with red ANSI styling."""
    return style(text, RED, enabled=enabled)
