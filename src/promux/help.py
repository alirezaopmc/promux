"""Dev-friendly categorized help renderer for Promux CLI."""

from __future__ import annotations

from . import __version__
from .colors import bold, cyan, dim, green, is_color_enabled, yellow


# ---------------------------------------------------------------------------
# Help content catalogue
# ---------------------------------------------------------------------------

# Section → list of (command_name, short_description)
HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Profile Management",
        [
            ("list", "List all saved profiles"),
            ("switch", "Hot-swap the active profile"),
            ("save", "Save current credentials as a named profile"),
            ("next", "Rotate to the next profile in round-robin order"),
            ("remove", "Delete a saved profile"),
            ("whoami", "Print the currently active profile"),
        ],
    ),
    (
        "Quota & Failover",
        [
            ("quota", "Show quota status for all profiles"),
            ("refresh", "Force-refresh quota data"),
            ("watch", "Live-update quota table at a fixed interval"),
        ],
    ),
    (
        "Tools & Utilities",
        [
            ("tools", "List registered tools and their storage paths"),
            ("version", "Print version and runtime info"),
            ("completion", "Generate shell completion script"),
            ("help", "Show this help or help for a specific command"),
        ],
    ),
]

# Per-subcommand descriptions (used by print_subcommand_help)
COMMAND_DETAILS: dict[str, str] = {
    "list": "List all accounts saved in the promux vault.",
    "switch": "Hot-swap the active profile by copying its token to the live token slot.",
    "save": "Save the current active credentials as a named profile in the vault.",
    "next": "Rotate to the next eligible standby profile in round-robin order.",
    "remove": "Delete a saved profile and its associated token from the vault.",
    "whoami": "Print the currently active profile name, email, and token expiry.",
    "quota": "Show Cloud Code Assist quota status for all (or a specific) profile.",
    "refresh": "Force-refresh OAuth access tokens for vault accounts.",
    "watch": "Start the reactive log-tailer daemon; auto-rotates on quota exhaustion.",
    "tools": "List registered tool adapters and their storage paths.",
    "version": "Print version and runtime info (Python version, platform, tools).",
    "completion": "Generate a shell auto-completion script for bash or zsh.",
    "help": "Show this help message or detailed help for a specific command.",
}

# Options table for main help
OPTIONS: list[tuple[str, str]] = [
    ("--json", "Output in JSON format"),
    ("--no-color", "Disable ANSI color output"),
    ("-h, --help", "Show help"),
    ("-V, --version", "Show version"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cmd_help(
    subcommand: str | None = None,
    tool: str | None = None,
    no_color: bool = False,
) -> int:
    """Entry point for the help command.

    Args:
        subcommand: If provided, print detailed help for this subcommand.
        tool: If provided, print tool-specific help (e.g. ``promux claude help``).
        no_color: Suppress ANSI color output.

    Returns:
        Exit code 0.
    """
    if tool is not None:
        print_tool_help(tool, no_color=no_color)
        return 0
    if subcommand is not None:
        print_subcommand_help(subcommand, no_color=no_color)
        return 0
    print_main_help(no_color=no_color)
    return 0


def print_main_help(no_color: bool = False) -> None:
    """Print the categorized main help page."""
    use_color = is_color_enabled(force_no_color=no_color)

    # Header
    version_str = bold(f"promux {__version__}", enabled=use_color)
    print(version_str)
    print()

    # Usage
    usage_heading = bold("Usage:", enabled=use_color)
    print(usage_heading)
    print(f"  {dim('promux <tool> <command> [args]', enabled=use_color)}")
    print(f"  {dim('promux <command> [args]', enabled=use_color)}")
    print()

    # Sections
    for section_name, commands in HELP_SECTIONS:
        section_heading = bold(cyan(f"{section_name}:", enabled=use_color), enabled=use_color)
        print(section_heading)
        for cmd_name, description in commands:
            cmd_str = green(f"  {cmd_name:<11}", enabled=use_color)
            desc_str = dim(description, enabled=use_color)
            print(f"{cmd_str}{desc_str}")
        print()

    # Options
    options_heading = bold("Options:", enabled=use_color)
    print(options_heading)
    for opt_name, description in OPTIONS:
        opt_str = yellow(f"  {opt_name:<14}", enabled=use_color)
        desc_str = dim(description, enabled=use_color)
        print(f"{opt_str}{desc_str}")


def print_subcommand_help(command_name: str, no_color: bool = False) -> None:
    """Print detailed help for a specific subcommand."""
    use_color = is_color_enabled(force_no_color=no_color)

    description = COMMAND_DETAILS.get(command_name)
    if description is None:
        print(f"No help available for '{command_name}'.")
        return

    cmd_header = bold(f"promux {command_name}", enabled=use_color)
    print(cmd_header)
    print()
    print(f"  {description}")
    print()
    print(dim("Run 'promux --help' to see all available commands.", enabled=use_color))


def print_tool_help(tool_name: str, no_color: bool = False) -> None:
    """Print tool-specific help (e.g. ``promux claude help``)."""
    use_color = is_color_enabled(force_no_color=no_color)

    header = bold(f"promux {tool_name}", enabled=use_color)
    print(header)
    print()
    print(
        f"  Manage profiles and quota for the {cyan(tool_name, enabled=use_color)} tool adapter."
    )
    print()
    print(dim("Run 'promux --help' to see all available commands.", enabled=use_color))
