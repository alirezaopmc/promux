from promux.completion import (
    SUPPORTED_SHELLS,
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


def test_generate_zsh_completion() -> None:
    script = generate_zsh_completion()
    assert "#compdef promux" in script
    assert "_promux()" in script
    assert "switch" in script
    assert "quota" in script
    assert "completion" in script
