"""Watch daemon command."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..failover import FailoverEngine
from ..logging_config import configure_daemon_logging
from ..watch import LogMatch, LogWatcher

logger = logging.getLogger(__name__)


def cmd_watch(failover: FailoverEngine, poll_seconds: float, cooldown: int | None) -> int:
    configure_daemon_logging()

    gemini_home = None
    if "PROMUX_GEMINI_HOME" in os.environ:
        gemini_home = Path(os.environ["PROMUX_GEMINI_HOME"])
    elif hasattr(failover, "storage") and hasattr(failover.storage, "gemini_home"):
        gemini_home = failover.storage.gemini_home

    watcher = LogWatcher(
        failover=failover,
        poll_seconds=poll_seconds,
        gemini_home=gemini_home,
    )

    def on_match(match: LogMatch) -> None:
        logger.warning("Quota exhausted: %s in %s", match.pattern, match.file_path)
        print(f"[promux-watch] Quota exhausted: {match.pattern} in {match.file_path}")
        if match.reset_hint:
            logger.info("Reset hint detected: %s", match.reset_hint)
            print(f"[promux-watch] Reset hint detected: {match.reset_hint}")

    cool_str = f"{cooldown}m" if cooldown else "auto"
    logger.info("Starting log watcher (poll: %ss, cooldown: %s)", poll_seconds, cool_str)
    print(f"[promux-watch] Starting log watcher (poll: {poll_seconds}s, cooldown: {cool_str})...")
    print("[promux-watch] Press Ctrl+C to stop.")

    try:
        watcher.run_forever(on_match=on_match, cooldown_minutes=cooldown)
    except KeyboardInterrupt:
        pass
    logger.info("Watcher stopped.")
    print("[promux-watch] Watcher stopped.")
    return 0
