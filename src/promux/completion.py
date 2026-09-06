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
]


def generate_bash_completion() -> str:
    """Generate Bash completion script for promux."""
    return r"""# bash completion for promux
_promux_completion() {
    local cur prev words cword
    _init_completion || return

    local commands="list save switch next quota whoami remove watch completion"
    local common_opts="--json --help -h"

    # Accounts helper
    _promux_accounts() {
        local promux_home="${PROMUX_HOME:-$HOME/.promux}"
        if [[ -d "$promux_home/accounts" ]]; then
            command ls -1 "$promux_home/accounts" 2>/dev/null
        fi
    }

    if [[ $cword -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "$commands $common_opts" -- "$cur") )
        return 0
    fi

    case "${words[1]}" in
        switch|remove|quota)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "$common_opts" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "$(_promux_accounts)" -- "$cur") )
            fi
            return 0
            ;;
        save)
            if [[ "$prev" == "--email" ]]; then
                return 0
            fi
            COMPREPLY=( $(compgen -W "--email $common_opts" -- "$cur") )
            return 0
            ;;
        next)
            if [[ "$prev" == "--reason" || "$prev" == "--cooldown" ]]; then
                return 0
            fi
            COMPREPLY=( $(compgen -W "--reason --cooldown $common_opts" -- "$cur") )
            return 0
            ;;
        watch)
            if [[ "$prev" == "--poll-seconds" || "$prev" == "--cooldown" ]]; then
                return 0
            fi
            COMPREPLY=( $(compgen -W "--poll-seconds --cooldown $common_opts" -- "$cur") )
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
    )

    _arguments -C \
        '--json[Output structured JSON]' \
        '(-h --help)'{-h,--help}'[Show help]' \
        '1: :->command' \
        '*:: :->args'

    case $state in
        command)
            _describe 'promux command' subcommands
            ;;
        args)
            case $words[1] in
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
                completion)
                    _arguments \
                        '1:shell:(bash zsh)'
                    ;;
            esac
            ;;
    esac
}

_promux "$@"
"""
