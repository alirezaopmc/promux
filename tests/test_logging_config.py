"""Tests for logging configuration."""

from __future__ import annotations

import logging

from promux import logging_config


def _clear_promux_logger():
    logger = logging.getLogger("promux")
    for handler in list(logger.handlers):
        handler.close()
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)


def test_cli_logging_default_is_warning(capsys):
    _clear_promux_logger()
    logging_config.configure_cli_logging(verbose=0)
    logger = logging.getLogger("promux.test")
    logger.info("should not appear")
    logger.warning("should appear")
    captured = capsys.readouterr()
    assert "should not appear" not in captured.err
    assert "should appear" in captured.err


def test_cli_logging_verbose_1_is_info(capsys):
    _clear_promux_logger()
    logging_config.configure_cli_logging(verbose=1)
    logger = logging.getLogger("promux.test")
    logger.info("info message")
    captured = capsys.readouterr()
    assert "info message" in captured.err


def test_cli_logging_verbose_2_is_debug(capsys):
    _clear_promux_logger()
    logging_config.configure_cli_logging(verbose=2)
    logger = logging.getLogger("promux.test")
    logger.debug("debug message")
    captured = capsys.readouterr()
    assert "debug message" in captured.err
    assert "test_logging_config" in captured.err or "promux.test" in captured.err


def test_cli_logging_quiet_is_error_only(capsys):
    _clear_promux_logger()
    logging_config.configure_cli_logging(quiet=True)
    logger = logging.getLogger("promux.test")
    logger.warning("should not appear")
    logger.error("should appear")
    captured = capsys.readouterr()
    assert "should not appear" not in captured.err
    assert "should appear" in captured.err


def test_cli_logging_writes_to_stderr_not_stdout(capsys):
    _clear_promux_logger()
    logging_config.configure_cli_logging(verbose=1)
    logging.getLogger("promux.test").info("stderr only")
    captured = capsys.readouterr()
    assert "stderr only" in captured.err
    assert "stderr only" not in captured.out


def test_daemon_logging_stderr(capsys):
    _clear_promux_logger()
    logging_config.configure_daemon_logging(debug=False)
    logger = logging.getLogger("promux.daemon")
    logger.info("daemon info message")
    captured = capsys.readouterr()
    assert "[INFO]" in captured.err
    assert "daemon info message" in captured.err
    assert "promux.daemon:" in captured.err


def test_daemon_logging_file(tmp_path):
    _clear_promux_logger()
    log_file = tmp_path / "daemon.log"
    logging_config.configure_daemon_logging(log_file=log_file, debug=True)
    logger = logging.getLogger("promux.daemon")
    logger.debug("daemon debug message")
    for handler in logging.getLogger("promux").handlers:
        handler.flush()
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "[DEBUG]" in content
    assert "daemon debug message" in content
    _clear_promux_logger()
