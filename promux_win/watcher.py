"""
Promux Windows Watcher Daemon
Monitors Antigravity logs for quota exhaustion events (429 RESOURCE_EXHAUSTED, Individual quota reached)
and triggers automatic seamless failover to eligible standby accounts.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .core import (
    DEFAULT_COOLDOWN_MINUTES,
    SmartSwitchEngine,
    WindowsStorageEngine,
)

INDIVIDUAL_QUOTA_RE = re.compile(r"(?i)Individual quota reached")
RESOURCE_EXHAUSTED_RE = re.compile(r"(?i)RESOURCE_EXHAUSTED\s*\(\s*code\s*429\s*\)|RESOURCE_EXHAUSTED")
WEEKLY_QUOTA_RE = re.compile(r"(?i)weekly quota reached")
RESET_HINT_RE = re.compile(r"(?i)Resets in\s+(?P<reset>~?[^.)\n]+)")


class AntigravityLogWatcher:
    def __init__(
        self,
        storage: WindowsStorageEngine,
        on_event: Callable[[str, str], None] | None = None,
        poll_interval: float = 2.0,
    ):
        self.storage = storage
        self.on_event = on_event
        self.poll_interval = poll_interval
        self.running = False
        self._thread: threading.Thread | None = None
        self._file_offsets: dict[str, int] = {}
        self.events_history: list[dict[str, str]] = []

    def _get_target_log_paths(self) -> list[Path]:
        paths = [
            Path.home() / ".gemini" / "antigravity-cli" / "cli.log",
            Path(os.environ.get("APPDATA", "")) / "Antigravity" / "logs" / "language_server.log",
            Path(os.environ.get("APPDATA", "")) / "Antigravity" / "logs" / "main.log",
        ]
        return [p for p in paths if p.exists()]

    def log_event(self, level: str, message: str) -> None:
        entry = {
            "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        self.events_history.append(entry)
        if len(self.events_history) > 100:
            self.events_history.pop(0)
        if self.on_event:
            self.on_event(level, message)

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        # Initialize file offsets to current end of files so we only tail new lines
        for p in self._get_target_log_paths():
            try:
                self._file_offsets[str(p)] = p.stat().st_size
            except Exception:
                self._file_offsets[str(p)] = 0

        self.log_event("INFO", "Watcher daemon started. Monitoring active Antigravity session.")
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        self.log_event("INFO", "Watcher daemon stopped.")

    def _run_loop(self) -> None:
        while self.running:
            time.sleep(self.poll_interval)
            try:
                self._check_logs()
            except Exception as e:
                self.log_event("ERROR", f"Watcher loop error: {e}")

    def _check_logs(self) -> None:
        log_paths = self._get_target_log_paths()
        for p in log_paths:
            path_str = str(p)
            try:
                curr_size = p.stat().st_size
                last_offset = self._file_offsets.get(path_str, 0)
                if curr_size < last_offset:
                    # Log file was truncated/rotated
                    last_offset = 0

                if curr_size > last_offset:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(last_offset)
                        new_content = f.read()
                        self._file_offsets[path_str] = f.tell()
                        self._parse_content(new_content)
            except Exception:
                pass

    def _parse_content(self, text: str) -> None:
        lines = text.splitlines()
        for line in lines:
            if INDIVIDUAL_QUOTA_RE.search(line) or RESOURCE_EXHAUSTED_RE.search(line) or WEEKLY_QUOTA_RE.search(line):
                active_acct = self.storage.get_active_account_name()
                self.log_event("WARN", f"Quota exhaustion detected on active account '{active_acct}'!")

                # Extract reset hint if available
                cooldown_minutes = DEFAULT_COOLDOWN_MINUTES
                hint = RESET_HINT_RE.search(line)
                if hint:
                    self.log_event("INFO", f"Reset hint detected: {hint.group('reset')}")

                if active_acct:
                    self.storage.apply_cooldown(active_acct, minutes=cooldown_minutes)

                # Trigger Smart Failover
                engine = SmartSwitchEngine(self.storage)
                ok, msg, target = engine.smart_switch()
                if ok and target:
                    self.log_event("SUCCESS", f"Auto-failover successful! Rotated to '{target}'")
                else:
                    self.log_event("ERROR", f"Auto-failover failed: {msg}")
                break
