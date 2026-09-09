"""Native zero-dependency shell completion script generators for Promux."""

SUPPORTED_SHELLS: list[str] = ["bash", "zsh"]

SUBCOMMANDS: list[str] = [
    "list",
    "save",
    "switch",
    "next",
    "quota",
    "whoami",
    "remove",
    "watch",
    "completion",
    "tools",
    "version",
]

TOOLS: list[str] = [
    "agy",
    "claude",
    "codex",
    "cursor",
]


def generate_bash_completion() -> str:
    """Generate Bash completion script for promux."""
    return r"""# bash completion for promux
_promux_completion() {
    local cur prev words cword
    if declare -F _init_completion >/dev/null 2>&1; then
        _init_completion || return
    else
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev=""
        if [[ $COMP_CWORD -gt 0 ]]; then
            prev="${COMP_WORDS[COMP_CWORD-1]}"
        fi
        words=("${COMP_WORDS[@]}")
        cword=$COMP_CWORD
    fi

    local commands="list save switch next quota whoami remove watch completion tools version"
    local tools="agy claude codex cursor"
    local common_opts="--json --no-color -V --version --help -h"

    # Accounts helper
    _promux_accounts() {
        local promux_home="${PROMUX_HOME:-$HOME/.promux}"
        if [[ -d "$promux_home/accounts" ]]; then
            command ls -1 "$promux_home/accounts" 2>/dev/null
        fi
    }

    if [[ $cword -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "$commands $tools $common_opts" -- "$cur") )
        return 0
    fi

    local cmd="${words[1]}"
    local prev_word="$prev"
    case "${words[1]}" in
        agy|claude|codex|cursor)
            if [[ $cword -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "$commands $common_opts" -- "$cur") )
                return 0
            fi
            cmd="${words[2]}"
            ;;
        *)
            cmd="${words[1]}"
            ;;
    esac

    case "$cmd" in
        switch|remove|quota)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "$common_opts" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "$(_promux_accounts)" -- "$cur") )
            fi
            return 0
            ;;
        save)
            if [[ "$prev_word" == "--email" ]]; then
                return 0
            fi
            COMPREPLY=( $(compgen -W "--email $common_opts" -- "$cur") )
            return 0
            ;;
        next)
            if [[ "$prev_word" == "--reason" || "$prev_word" == "--cooldown" ]]; then
                return 0
            fi
            COMPREPLY=( $(compgen -W "--reason --cooldown $common_opts" -- "$cur") )
            return 0
            ;;
        watch)
            if [[ "$prev_word" == "--poll-seconds" || "$prev_word" == "--cooldown" ]]; then
                return 0
            fi
            COMPREPLY=( $(compgen -W "--poll-seconds --cooldown $common_opts" -- "$cur") )
            return 0
            ;;
        tools)
            COMPREPLY=( $(compgen -W "list $common_opts" -- "$cur") )
            return 0
            ;;
        completion)
            COMPREPLY=( $(compgen -W "bash zsh" -- "$cur") )
            return 0
            ;;
        *)
            COMPREPLY=( $(compgen -W "$common_opts" -- "$cur") )
            return 0
            ;;
    esac
}

complete -F _promux_completion promux
"""


def generate_zsh_completion() -> str:
    """Generate Zsh completion script for promux."""
    return r"""#compdef promux

_promux_accounts() {
    local promux_home="${PROMUX_HOME:-$HOME/.promux}"
    local -a accounts
    if [[ -d "$promux_home/accounts" ]]; then
        accounts=(${(f)"$(command ls -1 "$promux_home/accounts" 2>/dev/null)"})
        _describe 'account' accounts
    fi
}

_promux() {
    local context state line
    typeset -A opt_args

    local -a subcommands
    subcommands=(
        'list:List all accounts in the vault'
        'save:Save current active token as a named account'
        'switch:Hot-swap active profile to specified account'
        'next:Rotate to next eligible standby account'
        'quota:Query live Cloud Code Assist quota'
        'whoami:Display details of active profile'
        'remove:Remove an account from vault'
        'watch:Start reactive quota failover daemon'
        'completion:Generate shell completion script'
        'tools:List supported developer CLI tools'
        'version:Show version information'
    )

    local -a tools
    tools=(
        'agy:Google Antigravity CLI'
        'claude:Claude Code'
        'codex:Codex CLI'
        'cursor:Cursor CLI'
    )

    _arguments -C \
        '--json[Output structured JSON]' \
        '--no-color[Disable ANSI color output]' \
        '(-V --version)'{-V,--version}'[Show version information and exit]' \
        '(-h --help)'{-h,--help}'[Show help]' \
        '1: :->command' \
        '*:: :->args'

    case $state in
        command)
            _describe 'promux command' subcommands
            _describe 'promux tool' tools
            ;;
        args)
            case $words[1] in
                agy|claude|codex|cursor)
                    if [[ $CURRENT -eq 2 ]]; then
                        _describe 'promux command' subcommands
                    else
                        case $words[2] in
                            switch|remove|quota)
                                _arguments \
                                    '--json[Output structured JSON]' \
                                    '1:account:_promux_accounts'
                                ;;
                            save)
                                _arguments \
                                    '--email[Account email address]:email:_message "email address"' \
                                    '--json[Output structured JSON]' \
                                    '1:name:_message "account name"'
                                ;;
                            next)
                                _arguments \
                                    '--reason[Reason for rotation]:reason:_message "reason"' \
                                    '--cooldown[Cooldown minutes]:cooldown:_message "minutes"' \
                                    '--json[Output structured JSON]'
                                ;;
                            watch)
                                _arguments \
                                    '--poll-seconds[Log polling interval]:seconds:_message "seconds"' \
                                    '--cooldown[Fallback cooldown minutes]:cooldown:_message "minutes"'
                                ;;
                            tools)
                                _arguments \
                                    '--json[Output structured JSON]' \
                                    '1:action:(list)'
                                ;;
                            version)
                                _arguments \
                                    '--json[Output structured JSON]' \
                                    '--no-color[Disable ANSI color output]'
                                ;;
                        esac
                    fi
                    ;;
                switch|remove|quota)
                    _arguments \
                        '--json[Output structured JSON]' \
                        '1:account:_promux_accounts'
                    ;;
                save)
                    _arguments \
                        '--email[Account email address]:email:_message "email address"' \
                        '--json[Output structured JSON]' \
                        '1:name:_message "account name"'
                    ;;
                next)
                    _arguments \
                        '--reason[Reason for rotation]:reason:_message "reason"' \
                        '--cooldown[Cooldown minutes]:cooldown:_message "minutes"' \
                        '--json[Output structured JSON]'
                    ;;
                watch)
                    _arguments \
                        '--poll-seconds[Log polling interval]:seconds:_message "seconds"' \
                        '--cooldown[Fallback cooldown minutes]:cooldown:_message "minutes"'
                    ;;
                tools)
                    _arguments \
                        '--json[Output structured JSON]' \
                        '1:action:(list)'
                    ;;
                version)
                    _arguments \
                        '--json[Output structured JSON]' \
                        '--no-color[Disable ANSI color output]'
                    ;;
                completion)
                    _arguments \
                        '1:shell:(bash zsh)'
                    ;;
            esac
            ;;
    esac
}

if [[ -n "$funcstack[1]" && "$funcstack[1]" == "_promux" ]]; then
    _promux "$@"
else
    compdef _promux promux 2>/dev/null || true
fi
"""
