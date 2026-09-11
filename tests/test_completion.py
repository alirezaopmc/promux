import shutil
import subprocess

from promux.completion import (
    SUBCOMMANDS,
    SUPPORTED_SHELLS,
    TOOLS,
    generate_bash_completion,
    generate_zsh_completion,
)


def test_supported_shells() -> None:
    assert "bash" in SUPPORTED_SHELLS
    assert "zsh" in SUPPORTED_SHELLS


def test_generate_bash_completion() -> None:
    script = generate_bash_completion()
    assert "_promux_completion()" in script
    assert "complete -F _promux_completion promux" in script
    assert "list save switch next quota whoami remove watch completion" in script
    # Dynamic account lookup check
    assert "accounts" in script

    # Bash fallback without bash-completion package
    assert "declare -F _init_completion" in script
    assert "COMP_WORDS" in script

    # All subcommands included
    assert "tools" in SUBCOMMANDS
    assert "version" in SUBCOMMANDS
    for cmd in SUBCOMMANDS:
        assert cmd in script

    # All tools included as first-word options
    for tool in TOOLS:
        assert tool in script

    # Check flags for switch
    assert "--smart" in script
    assert "--model" in script

    # Syntax check via bash -n if available
    bash_path = shutil.which("bash")
    if bash_path:
        proc = subprocess.run(
            [bash_path, "-n"],
            input=script,
            text=True,
            capture_output=True,
        )
        assert proc.returncode == 0, proc.stderr


def test_generate_zsh_completion() -> None:
    script = generate_zsh_completion()
    assert "#compdef promux" in script
    assert "_promux()" in script
    assert "compdef _promux promux" in script
    assert "funcstack[1]" in script

    # All subcommands included
    assert "tools" in SUBCOMMANDS
    assert "version" in SUBCOMMANDS
    for cmd in SUBCOMMANDS:
        assert cmd in script

    # All tools included
    for tool in TOOLS:
        assert tool in script

    # Check flags for switch
    assert "--smart" in script
    assert "--model" in script
