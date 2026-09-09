"""Tests for the categorized help system (Task 4)."""

from promux.cli import main


def test_cli_help_subcommand(capsys):
    ret = main(["help"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Profile Management" in out
    assert "Quota & Failover" in out
    assert "Tools & Utilities" in out
    assert "switch" in out
    assert "quota" in out


def test_cli_help_flag(capsys):
    ret = main(["--help"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Profile Management" in out
    assert "switch" in out


def test_cli_help_specific_command(capsys):
    ret = main(["help", "switch"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "switch" in out
    assert "Hot-swap" in out or "profile" in out


def test_cli_help_tool_specific(capsys):
    ret = main(["claude", "help"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "claude" in out
