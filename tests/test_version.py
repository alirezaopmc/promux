"""Tests for promux version command, flags, and version formatting."""

import json

from promux import __version__
from promux.cli import build_parser, main
from promux.colors import RESET
from promux.version import cmd_version, get_version_info


def test_get_version_info() -> None:
    info = get_version_info()
    assert info["version"] == __version__
    assert "python" in info
    assert "platform" in info
    assert isinstance(info["tools"], list)
    assert "agy" in info["tools"]
    assert "claude" in info["tools"]
    assert info["default_tool"] == "agy"


def test_cmd_version_human_readable_no_color(capsys) -> None:
    ret = cmd_version(json_out=False, no_color=True)
    assert ret == 0
    out = capsys.readouterr().out
    assert f"promux {__version__}" in out
    assert "Python" in out
    assert "Supported tools:" in out
    assert "agy (default)" in out
    assert RESET not in out


def test_cmd_version_human_readable_colored(capsys, monkeypatch) -> None:
    monkeypatch.setattr("promux.version.is_color_enabled", lambda force_no_color=False: True)
    ret = cmd_version(json_out=False, no_color=False)
    assert ret == 0
    out = capsys.readouterr().out
    assert f"promux {__version__}" in out
    assert RESET in out


def test_cmd_version_json(capsys) -> None:
    ret = cmd_version(json_out=True)
    assert ret == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["version"] == __version__
    assert data["default_tool"] == "agy"
    assert "tools" in data


def test_cli_version_subcommand(capsys) -> None:
    ret = main(["version"])
    assert ret == 0
    out = capsys.readouterr().out
    assert f"promux {__version__}" in out
    assert "Python" in out
    assert "Supported tools:" in out


def test_cli_version_flag_long(capsys) -> None:
    ret = main(["--version"])
    assert ret == 0
    out = capsys.readouterr().out
    assert f"promux {__version__}" in out


def test_cli_version_flag_short(capsys) -> None:
    ret = main(["-V"])
    assert ret == 0
    out = capsys.readouterr().out
    assert f"promux {__version__}" in out


def test_cli_version_json(capsys) -> None:
    ret = main(["version", "--json"])
    assert ret == 0
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == __version__
    assert data["default_tool"] == "agy"
    assert "tools" in data


def test_cli_version_flag_json(capsys) -> None:
    ret = main(["--version", "--json"])
    assert ret == 0
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == __version__


def test_cli_version_short_flag_json(capsys) -> None:
    ret = main(["-V", "--json"])
    assert ret == 0
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == __version__


def test_cli_version_with_tool_prefix(capsys) -> None:
    ret = main(["agy", "version"])
    assert ret == 0
    out = capsys.readouterr().out
    assert f"promux {__version__}" in out


def test_cli_version_flag_with_tool_prefix(capsys) -> None:
    ret = main(["claude", "-V"])
    assert ret == 0
    out = capsys.readouterr().out
    assert f"promux {__version__}" in out


def test_cli_version_no_color(capsys) -> None:
    ret = main(["version", "--no-color"])
    assert ret == 0
    out = capsys.readouterr().out
    assert RESET not in out


def test_parser_contains_version_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["version"])
    assert args.command == "version"
