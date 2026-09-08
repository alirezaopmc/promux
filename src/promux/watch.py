import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import (
    CLI_LOG,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_POLL_SECONDS,
    INDIVIDUAL_QUOTA_RE,
    LOG_DIR,
    RESET_HINT_RE,
    RESOURCE_EXHAUSTED_RE,
    WEEKLY_QUOTA_RE,
)


@dataclass
class LogMatch:
    pattern: str
    line: str
    reset_hint: str | None = None
    file_path: str | None = None


class LogWatcher:
    PATTERNS = [
        ("INDIVIDUAL_QUOTA", INDIVIDUAL_QUOTA_RE),
        ("RESOURCE_EXHAUSTED", RESOURCE_EXHAUSTED_RE),
        ("WEEKLY_QUOTA", WEEKLY_QUOTA_RE),
    ]

    def __init__(
        self,
        failover: Any = None,
        log_files: list[Path] | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        gemini_home: Path | None = None,
        token_check_interval: float = 900.0,
        storage: Any = None,
    ):
        if failover is None and storage is not None:
            from .failover import FailoverEngine

            failover = FailoverEngine(storage=storage)
        self.storage = storage or getattr(failover, "storage", None)
        self.failover = failover
        self._custom_log_files = log_files
        self.poll_seconds = poll_seconds
        self.gemini_home = Path(gemini_home) if gemini_home else None
        self.token_check_interval = token_check_interval
        self.offsets: dict[Path, int] = {}
        self.inodes: dict[Path, int] = {}
        self.running = False

    def get_log_files(self) -> list[Path]:
        if self._custom_log_files is not None:
            return [p for p in self._custom_log_files if p.exists()]
        files: list[Path] = []
        cli_log = (self.gemini_home / "cli.log") if self.gemini_home else CLI_LOG
        log_dir = (self.gemini_home / "log") if self.gemini_home else LOG_DIR

        if cli_log.exists():
            files.append(cli_log)
        if log_dir.exists():

            def _mtime(p: Path) -> float:
                try:
                    return p.stat().st_mtime
                except OSError:
                    return 0.0

            log_files = [p for p in log_dir.glob("*.log") if p.is_file()]
            files.extend(sorted(log_files, key=_mtime, reverse=True)[:5])
        return list(dict.fromkeys(files))

    def init_offsets(self) -> None:
        for f in self.get_log_files():
            try:
                st = f.stat()
                self.offsets[f] = st.st_size
                self.inodes[f] = st.st_ino
            except OSError:
                self.offsets[f] = 0

    def parse_reset_minutes(self, hint: str | None) -> int:
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

    def stop(self) -> None:
        self.running = False

    def run_once(self) -> list[LogMatch]:
        matches: list[LogMatch] = []
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

                with open(path, encoding="utf-8", errors="replace") as f:
                    f.seek(prev_offset)
                    new_lines = f.readlines()
                    self.offsets[path] = f.tell()

                if new_lines and not new_lines[-1].endswith("\n"):
                    partial_line = new_lines.pop()
                    self.offsets[path] -= len(partial_line.encode("utf-8"))

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

    def check_and_renew_tokens(self, expiry_threshold_seconds: int = 600) -> list[str]:
        storage = getattr(self.failover, "storage", None)
        if storage is None:
            return []

        from . import cli

        active_profile = storage.get_active_profile()
        renewed_accounts: list[str] = []

        for acct in storage.list_accounts():
            name = acct.name if hasattr(acct, "name") else str(acct)
            vault_token_path = storage.accounts_dir / name / "antigravity-oauth-token"
            is_active = name == active_profile

            if is_active and storage.live_token.exists():
                token_path = storage.live_token
                token_data = cli._read_token_data(token_path)
                if not token_data and vault_token_path.exists():
                    token_path = vault_token_path
                    token_data = cli._read_token_data(token_path)
            else:
                token_path = vault_token_path
                token_data = cli._read_token_data(token_path)

            if not token_data:
                continue

            if cli._is_token_expired(token_data, buffer_seconds=expiry_threshold_seconds):
                refreshed = cli._refresh_token_file(token_path, token_data, storage=storage)
                if refreshed:
                    if is_active:
                        if token_path == storage.live_token:
                            cli._sync_active_tokens(storage.live_token, vault_token_path)
                        else:
                            cli._sync_active_tokens(vault_token_path, storage.live_token)
                    renewed_accounts.append(name)
            elif is_active and token_path == storage.live_token and vault_token_path.exists():
                vault_data = cli._read_token_data(vault_token_path)
                if vault_data and cli._is_token_expired(
                    vault_data, buffer_seconds=expiry_threshold_seconds
                ):
                    cli._sync_active_tokens(storage.live_token, vault_token_path)

        return renewed_accounts

    def run_forever(
        self,
        on_match: Callable[[LogMatch], None] | None = None,
        cooldown_minutes: int | None = None,
        max_iterations: int | None = None,
    ) -> None:
        self.running = True
        if not self.offsets:
            self.init_offsets()

        last_token_check = 0.0
        iterations = 0
        while self.running:
            if max_iterations is not None and iterations >= max_iterations:
                break
            iterations += 1

            now = time.time()
            if now - last_token_check >= self.token_check_interval:
                try:
                    self.check_and_renew_tokens()
                except Exception:
                    pass
                last_token_check = now

            try:
                matches = self.run_once()
                for m in matches:
                    if on_match:
                        on_match(m)
                    cool = (
                        cooldown_minutes
                        if cooldown_minutes is not None
                        else self.parse_reset_minutes(m.reset_hint)
                    )
                    self.failover.rotate_next(
                        reason=f"reactive: {m.pattern}", cooldown_minutes=cool
                    )
                    break
                if not self.running:
                    break
                time.sleep(self.poll_seconds)
            except KeyboardInterrupt:
                self.running = False
                break
