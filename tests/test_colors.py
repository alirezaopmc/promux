import sys
from io import StringIO
from unittest.mock import MagicMock

from promux.colors import (
    bold,
    cyan,
    dim,
    green,
    is_color_enabled,
    red,
    style,
    yellow,
)


def test_is_color_enabled_force_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    mock_stream = MagicMock()
    mock_stream.isatty.return_value = True

    assert is_color_enabled(stream=mock_stream, force_no_color=True) is False


def test_is_color_enabled_no_color_env(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    mock_stream = MagicMock()
    mock_stream.isatty.return_value = True

    # Non-empty NO_COLOR disables color
    monkeypatch.setenv("NO_COLOR", "1")
    assert is_color_enabled(stream=mock_stream) is False

    monkeypatch.setenv("NO_COLOR", "true")
    assert is_color_enabled(stream=mock_stream) is False

    monkeypatch.setenv("NO_COLOR", "0")
    assert is_color_enabled(stream=mock_stream) is False

    # Empty NO_COLOR does NOT disable color
    monkeypatch.setenv("NO_COLOR", "")
    assert is_color_enabled(stream=mock_stream) is True

    # Unset NO_COLOR does NOT disable color
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert is_color_enabled(stream=mock_stream) is True


def test_is_color_enabled_term_dumb(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    mock_stream = MagicMock()
    mock_stream.isatty.return_value = True

    monkeypatch.setenv("TERM", "dumb")
    assert is_color_enabled(stream=mock_stream) is False

    monkeypatch.setenv("TERM", "xterm-256color")
    assert is_color_enabled(stream=mock_stream) is True

    # TERM unset does not disable color if stream is tty
    monkeypatch.delenv("TERM", raising=False)
    assert is_color_enabled(stream=mock_stream) is True


def test_is_color_enabled_stream_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    mock_tty = MagicMock()
    mock_tty.isatty.return_value = True
    assert is_color_enabled(stream=mock_tty) is True

    mock_not_tty = MagicMock()
    mock_not_tty.isatty.return_value = False
    assert is_color_enabled(stream=mock_not_tty) is False

    # Object without isatty
    class DummyStream:
        pass

    assert is_color_enabled(stream=DummyStream()) is False  # type: ignore[arg-type]

    # StringIO does not have isatty returning True
    string_io = StringIO()
    assert is_color_enabled(stream=string_io) is False

    # Stream whose isatty raises an exception
    mock_broken = MagicMock()
    mock_broken.isatty.side_effect = ValueError("I/O operation on closed file")
    assert is_color_enabled(stream=mock_broken) is False


def test_is_color_enabled_default_stream(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    mock_stdout = MagicMock()
    mock_stdout.isatty.return_value = True
    monkeypatch.setattr(sys, "stdout", mock_stdout)

    assert is_color_enabled() is True

    mock_stdout.isatty.return_value = False
    assert is_color_enabled() is False


def test_style():
    # Enabled
    assert style("hello", "\033[1m", enabled=True) == "\033[1mhello\033[0m"
    # Disabled
    assert style("hello", "\033[1m", enabled=False) == "hello"
    # Default is enabled=True
    assert style("hello", "\033[1m") == "\033[1mhello\033[0m"
    # Empty string
    assert style("", "\033[32m", enabled=True) == "\033[32m\033[0m"
    assert style("", "\033[32m", enabled=False) == ""


def test_semantic_helpers():
    # Enabled
    assert bold("bold text") == "\033[1mbold text\033[0m"
    assert dim("dim text") == "\033[2mdim text\033[0m"
    assert cyan("cyan text") == "\033[36mcyan text\033[0m"
    assert green("green text") == "\033[32mgreen text\033[0m"
    assert yellow("yellow text") == "\033[33myellow text\033[0m"
    assert red("red text") == "\033[31mred text\033[0m"

    # Disabled
    assert bold("bold text", enabled=False) == "bold text"
    assert dim("dim text", enabled=False) == "dim text"
    assert cyan("cyan text", enabled=False) == "cyan text"
    assert green("green text", enabled=False) == "green text"
    assert yellow("yellow text", enabled=False) == "yellow text"
    assert red("red text", enabled=False) == "red text"
