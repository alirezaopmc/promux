import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Callable, Dict, Any

from .constants import (
    CLI_LOG,
    LOG_DIR,
    DEFAULT_POLL_SECONDS,
    DEFAULT_COOLDOWN_MINUTES,
    INDIVIDUAL_QUOTA_RE,
    RESOURCE_EXHAUSTED_RE,
    WEEKLY_QUOTA_RE,
    RESET_HINT_RE,
)
from .failover import FailoverEngine


@dataclass
class LogMatch:
    pattern: str
    line: str
    reset_hint: Optional[str] = None
    file_path: Optional[str] = None


class LogWatcher:
    PATTERNS = [
        ("INDIVIDUAL_QUOTA", INDIVIDUAL_QUOTA_RE),
        ("RESOURCE_EXHAUSTED", RESOURCE_EXHAUSTED_RE),
        ("WEEKLY_QUOTA", WEEKLY_QUOTA_RE),
    ]

    def __init__(
        self,
        failover: Any,
        log_files: Optional[List[Path]] = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ):
        self.failover = failover
        self._custom_log_files = log_files
        self.poll_seconds = poll_seconds
        self.offsets: Dict[Path, int] = {}
        self.inodes: Dict[Path, int] = {}
        self.running = False

    def get_log_files(self) -> List[Path]:
        if self._custom_log_files is not None:
            return [p for p in self._custom_log_files if p.exists()]
        files: List[Path] = []
        if CLI_LOG.exists():
            files.append(CLI_LOG)
        if LOG_DIR.exists():
            def _mtime(p: Path) -> float:
                try:
                    return p.stat().st_mtime
                except OSError:
                    return 0.0

            log_files = [p for p in LOG_DIR.glob("*.log") if p.is_file()]
            files.extend(sorted(log_files, key=_mtime, reverse=True)[:5])
        return list(dict.fromkeys(files))

    def init_offsets(self):
        for f in self.get_log_files():
            try:
                st = f.stat()
                self.offsets[f] = st.st_size
                self.inodes[f] = st.st_ino
            except OSError:
                self.offsets[f] = 0

    def parse_reset_minutes(self, hint: Optional[str]) -> int:
        if not hint:
            return DEFAULT_COOLDOWN_MINUTES
        hint = hint.strip().lower().lstrip("~").strip()
        m_day = re.search(r"(\d+)\s*d(?:ay)?", hint)
        days = int(m_day.group(1)) if m_day else 0
        m = re.search(r"(\d+)\s*h(?:our)?", hint)
        hours = int(m.group(1)) if m else 0
        m_min = re.search(r"(\d+)\s*m(?:in)?", hint)
        mins = int(m_min.group(1)) if m_min else 0
        total = days * 1440 + hours * 60 + mins
        return total if total > 0 else DEFAULT_COOLDOWN_MINUTES

    def stop(self):
        self.running = False

    def run_once(self) -> List[LogMatch]:
        matches: List[LogMatch] = []
        for path in self.get_log_files():
            prev_offset = self.offsets.get(path, 0)
            try:
                st = path.stat()
                size = st.st_size
                curr_ino = st.st_ino

                prev_ino = self.inodes.get(path)
                if prev_ino is not None and curr_ino != prev_ino:
                    prev_offset = 0
                    self.offsets[path] = 0
                elif size < prev_offset:  # File was rotated/truncated
                    prev_offset = 0
                    self.offsets[path] = 0

                self.inodes[path] = curr_ino

                if size == prev_offset:
                    continue

                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(prev_offset)
                    new_lines = f.readlines()
                    self.offsets[path] = f.tell()

                for line in new_lines:
                    for name, pat in self.PATTERNS:
                        if pat.search(line):
                            hint_match = RESET_HINT_RE.search(line)
                            hint = hint_match.group("reset") if hint_match else None
                            matches.append(
                                LogMatch(
                                    pattern=name,
                                    line=line.strip(),
                                    reset_hint=hint,
                                    file_path=str(path),
                                )
                            )
                            break
            except OSError:
                continue
        return matches

    def run_forever(
        self,
        on_match: Optional[Callable[[LogMatch], None]] = None,
        cooldown_minutes: Optional[int] = None,
        max_iterations: Optional[int] = None,
    ):
        self.running = True
        if not self.offsets:
            self.init_offsets()

        iterations = 0
        while self.running:
            if max_iterations is not None and iterations >= max_iterations:
                break
            iterations += 1
            try:
                matches = self.run_once()
                for m in matches:
                    if on_match:
                        on_match(m)
                    cool = cooldown_minutes if cooldown_minutes is not None else self.parse_reset_minutes(m.reset_hint)
                    self.failover.rotate_next(reason=f"reactive: {m.pattern}", cooldown_minutes=cool)
                    if not self.running:
                        break
                if not self.running:
                    break
                time.sleep(self.poll_seconds)
            except KeyboardInterrupt:
                self.running = False
                break
