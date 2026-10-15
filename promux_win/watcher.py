"""
Promux Windows Watcher Daemon
Monitors Antigravity logs for quota exhaustion events (429 RESOURCE_EXHAUSTED, Individual quota reached)
and triggers user confirmation popup or automatic seamless failover to eligible standby accounts.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .core import (
    DEFAULT_COOLDOWN_MINUTES,
    AntigravityManager,
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
        self.prompt_on_exhaustion: bool = True
        self.auto_restart_antigravity: bool = True
        self.pending_alert: dict[str, Any] | None = None

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

                # Evaluate best candidate standby account
                engine = SmartSwitchEngine(self.storage)
                candidates = engine.evaluate_candidates()
                eligible = [c for c in candidates if c["is_eligible"]]
                best_cand = eligible[0] if eligible else None
                cand_name = best_cand["account"].name if best_cand else None
                cand_alias = getattr(best_cand["account"], "alias", None) if best_cand else None

                if self.prompt_on_exhaustion:
                    self.pending_alert = {
                        "id": str(int(time.time())),
                        "exhausted_account": active_acct,
                        "recommended_account": cand_name,
                        "recommended_alias": cand_alias or cand_name,
                        "reason": "Quota reached (429 RESOURCE_EXHAUSTED)",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    self.log_event("WARN", f"Popup confirmation queued: Switch to '{cand_alias or cand_name}'?")
                else:
                    ok, msg, target = engine.smart_switch(restart_ide=self.auto_restart_antigravity)
                    if ok and target:
                        self.log_event("SUCCESS", f"Auto-failover successful! Rotated to '{target}'" + (" & restarted IDE" if self.auto_restart_antigravity else ""))
                    else:
                        self.log_event("ERROR", f"Auto-failover failed: {msg}")
                break

    def dismiss_alert(self) -> None:
        self.pending_alert = None

    def confirm_alert(self, target_account: str | None = None, restart_ide: bool = True) -> tuple[bool, str]:
        alert = self.pending_alert
        self.pending_alert = None
        target = target_account or (alert.get("recommended_account") if alert else None)
        if not target:
            return False, "No eligible target account found for failover."
        ok, msg = self.storage.switch_profile(target, restart_ide=restart_ide)
        if ok:
            self.log_event("SUCCESS", f"Failover switch confirmed: Rotated to '{target}'" + (" & restarted IDE" if restart_ide else ""))
        else:
            self.log_event("ERROR", f"Failover switch failed: {msg}")
        return ok, msg
