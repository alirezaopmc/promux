"""Logging configuration for promux CLI and daemon modes."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_cli_logging(verbose: int = 0, quiet: bool = False) -> None:
    """Configure interactive CLI logging. Call once from main(). Logs go to stderr.

    Args:
        verbose: 0=WARNING (default), 1=INFO (-v), 2+=DEBUG (-vv)
        quiet: If True, only ERROR and above are shown.
    """
    if quiet:
        level = logging.ERROR
    elif verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    else:
        level = logging.WARNING

    pkg = logging.getLogger("promux")
    pkg.handlers.clear()
    pkg.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    if level == logging.DEBUG:
        fmt = "%(levelname)s [%(name)s:%(lineno)d] %(message)s"
    else:
        fmt = "%(levelname)s: %(message)s"

    handler.setFormatter(logging.Formatter(fmt))
    pkg.addHandler(handler)
    pkg.propagate = False


def configure_daemon_logging(
    log_file: Path | None = None,
    debug: bool = False,
) -> None:
    """Configure daemon (watch) mode logging with timestamps and PID.

    Args:
        log_file: If given, log to rotating file. Otherwise log to stderr.
        debug: If True, use DEBUG level. Otherwise INFO.
    """
    level = logging.DEBUG if debug else logging.INFO

    pkg = logging.getLogger("promux")
    pkg.handlers.clear()
    pkg.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [pid:%(process)d] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    if log_file:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            log_file, maxBytes=10_000_000, backupCount=5, encoding="utf-8"
        )
    else:
        handler = logging.StreamHandler(sys.stderr)

    handler.setLevel(level)
    handler.setFormatter(formatter)
    pkg.addHandler(handler)
    pkg.propagate = False
