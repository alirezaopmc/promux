import time
from pathlib import Path
import pytest
from promux.watch import LogWatcher, LogMatch
from promux.models import RotationResult


class FakeFailover:
    def __init__(self):
        self.rotations = []

    def rotate_next(self, reason, cooldown_minutes=60):
        self.rotations.append((reason, cooldown_minutes))
        return RotationResult(success=True, from_account="a", to_account="b", reason=reason)


def test_log_watcher_detects_individual_quota(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Starting session\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    watcher.init_offsets()

    # Append quota error
    with open(log_file, "a") as f:
        f.write("Error: Individual quota reached. Resets in 45m.\n")

    matches = watcher.run_once()
    assert len(matches) == 1
    assert matches[0].pattern == "INDIVIDUAL_QUOTA"
    assert matches[0].reset_hint == "45m"
    assert matches[0].file_path == str(log_file)


def test_log_watcher_rotation_resilience(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Existing line 1\nExisting line 2\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    watcher.init_offsets()

    # Truncate / rewrite file (log rotation)
    log_file.write_text("RESOURCE_EXHAUSTED (code 429)\n")

    matches = watcher.run_once()
    assert len(matches) == 1
    assert matches[0].pattern == "RESOURCE_EXHAUSTED"


def test_log_watcher_detects_weekly_quota(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Existing line\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    watcher.init_offsets()

    with open(log_file, "a") as f:
        f.write("Failed: weekly quota reached. Resets in ~2h 15m.\n")

    matches = watcher.run_once()
    assert len(matches) == 1
    assert matches[0].pattern == "WEEKLY_QUOTA"
    assert matches[0].reset_hint == "~2h 15m"


def test_log_watcher_parse_reset_minutes():
    failover = FakeFailover()
    watcher = LogWatcher(failover=failover)

    assert watcher.parse_reset_minutes(None) == 60
    assert watcher.parse_reset_minutes("") == 60
    assert watcher.parse_reset_minutes("invalid text") == 60
    assert watcher.parse_reset_minutes("45m") == 45
    assert watcher.parse_reset_minutes("45 mins") == 45
    assert watcher.parse_reset_minutes("2 hours") == 120
    assert watcher.parse_reset_minutes("2h") == 120
    assert watcher.parse_reset_minutes("~1h 30m") == 90
    assert watcher.parse_reset_minutes("~ 3 hours 10 mins") == 190
    assert watcher.parse_reset_minutes("1d 2h") == 1560


def test_log_watcher_rotation_inode_change(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Line 1\nLine 2\nLine 3\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    watcher.init_offsets()

    # Rotate file by unlinking and recreating
    log_file.unlink()
    log_file.write_text("RESOURCE_EXHAUSTED\n")

    matches = watcher.run_once()
    assert len(matches) == 1
    assert matches[0].pattern == "RESOURCE_EXHAUSTED"


def test_log_watcher_ignores_non_matching_lines(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Hello world\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    watcher.init_offsets()

    with open(log_file, "a") as f:
        f.write("Info: Task running smoothly\n")
        f.write("Warning: Network retry attempt 1\n")

    matches = watcher.run_once()
    assert len(matches) == 0


def test_log_watcher_run_forever_triggers_failover(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Init\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    watcher.init_offsets()

    matched_events = []

    def on_match(match: LogMatch):
        matched_events.append(match)
        watcher.stop()

    with open(log_file, "a") as f:
        f.write("Error: Individual quota reached. Resets in 30m.\n")

    watcher.run_forever(on_match=on_match, max_iterations=5)

    assert len(matched_events) == 1
    assert matched_events[0].pattern == "INDIVIDUAL_QUOTA"
    assert len(failover.rotations) == 1
    assert failover.rotations[0][0] == "reactive: INDIVIDUAL_QUOTA"
    assert failover.rotations[0][1] == 30


def test_log_watcher_run_forever_with_override_cooldown(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Init\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    watcher.init_offsets()

    def on_match(match: LogMatch):
        watcher.stop()

    with open(log_file, "a") as f:
        f.write("Error: Individual quota reached. Resets in 30m.\n")

    watcher.run_forever(on_match=on_match, cooldown_minutes=15, max_iterations=5)

    assert len(failover.rotations) == 1
    assert failover.rotations[0][1] == 15


def test_log_watcher_get_default_log_files(tmp_path, monkeypatch):
    gemini_home = tmp_path / "gemini"
    gemini_home.mkdir()
    cli_log = gemini_home / "cli.log"
    cli_log.write_text("cli log\n")

    log_dir = gemini_home / "log"
    log_dir.mkdir()
    log1 = log_dir / "a.log"
    log1.write_text("log 1\n")
    log2 = log_dir / "b.log"
    log2.write_text("log 2\n")

    monkeypatch.setattr("promux.watch.CLI_LOG", cli_log)
    monkeypatch.setattr("promux.watch.LOG_DIR", log_dir)

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover)
    files = watcher.get_log_files()

    assert cli_log in files
    assert log1 in files
    assert log2 in files


def test_log_watcher_keyboard_interrupt(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Init\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)

    def raise_interrupt():
        raise KeyboardInterrupt()

    watcher.run_once = raise_interrupt
    watcher.run_forever()
    assert watcher.running is False


def test_log_watcher_handles_deleted_file(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Init\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    watcher.init_offsets()

    log_file.unlink()
    matches = watcher.run_once()
    assert matches == []


def test_log_watcher_multiple_matches_in_batch(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Init\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    watcher.init_offsets()

    with open(log_file, "a") as f:
        f.write("Line 1: Individual quota reached. Resets in 10m.\n")
        f.write("Normal line in between\n")
        f.write("Line 3: RESOURCE_EXHAUSTED (code 429)\n")

    matches = watcher.run_once()
    assert len(matches) == 2
    assert matches[0].pattern == "INDIVIDUAL_QUOTA"
    assert matches[0].reset_hint == "10m"
    assert matches[1].pattern == "RESOURCE_EXHAUSTED"


def test_log_watcher_multiple_matches_in_batch_only_one_rotation(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Init\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    watcher.init_offsets()

    with open(log_file, "a") as f:
        f.write("Line 1: Individual quota reached. Resets in 10m.\n")
        f.write("Line 2: RESOURCE_EXHAUSTED (code 429)\n")
        f.write("Line 3: Weekly quota limit reached\n")

    watcher.run_forever(max_iterations=1)

    # Must only trigger ONE rotation for the entire batch, not burn through all standby accounts
    assert len(failover.rotations) == 1
    assert failover.rotations[0][0] == "reactive: INDIVIDUAL_QUOTA"
    assert failover.rotations[0][1] == 10


def test_log_watcher_partial_line_splits(tmp_path):
    log_file = tmp_path / "cli.log"
    log_file.write_text("Init\n")

    failover = FakeFailover()
    watcher = LogWatcher(failover=failover, log_files=[log_file], poll_seconds=0.01)
    watcher.init_offsets()

    # Write a partial line without trailing newline
    with open(log_file, "a") as f:
        f.write("Normal line\n")
        f.write("Error: Individual quota reached. Res")

    matches1 = watcher.run_once()
    assert matches1 == []

    # Complete the line in next write
    with open(log_file, "a") as f:
        f.write("ets in 25m.\n")

    matches2 = watcher.run_once()
    assert len(matches2) == 1
    assert matches2[0].pattern == "INDIVIDUAL_QUOTA"
    assert matches2[0].reset_hint == "25m"


def test_log_watcher_dynamic_log_discovery(tmp_path):
    gemini_home = tmp_path / "gemini"
    gemini_home.mkdir()

    failover = FakeFailover()
    # At start, cli.log does NOT exist
    watcher = LogWatcher(failover=failover, gemini_home=gemini_home, poll_seconds=0.01)
    assert watcher.get_log_files() == []

    # Later, agy creates cli.log
    cli_log = gemini_home / "cli.log"
    cli_log.write_text("Init\n")

    # On next poll loop, it is discovered dynamically!
    assert cli_log in watcher.get_log_files()

