"""``vitruvio completion`` -- shell completion scripts.

Static scripts rather than a runtime completion protocol, and that is the whole decision. A completion hook that
shells back into `vitruvio` on every Tab would open a brain to answer -- which reads a snapshot, and with a vector
index registered would construct an embedder. Nobody expects pressing Tab to load a model, so completion here knows
the command names and nothing about any brain's contents.
"""

from __future__ import annotations

from cyclopts import App

from vitruvio.cli.context import current
from vitruvio.kernel import ExitCode, VitruvioError

app = App(
    name="completion",
    help="Print a shell completion script.",
    result_action="return_value",
    exit_on_error=False,
)

SHELLS = ("bash", "zsh", "fish")
"""The three worth shipping. Anything else is better served by that shell's own generator."""


def _tree() -> dict[str, list[str]]:
    """
    Group names to subcommand names, read from the app.

    From the app rather than a hand-written list: a completion script that offers a command which no longer exists
    is a worse experience than no completion, and it fails silently.

    Returns:
        dict[str, list[str]]: Subcommands per group, plus ``""`` for the top level.
    """
    from vitruvio.cli.main import app as root

    tree: dict[str, list[str]] = {}
    top: list[str] = []
    for name, target in root._commands.items():  # cyclopts exposes no public iteration over registered commands
        if name.startswith("-"):
            continue
        top.append(name)
        children = getattr(target, "_commands", None)
        if children:
            tree[name] = [child for child in children if not child.startswith("-")]
    tree[""] = top
    return tree


GLOBALS = ("--brain", "--config", "--actor", "--actor-kind", "--json", "--quiet", "--no-color", "--verbose")


def _bash() -> str:
    """The bash script."""
    tree = _tree()
    groups = " ".join(sorted(tree[""]))
    cases = "\n".join(
        f'        {name}) COMPREPLY=($(compgen -W "{" ".join(sorted(subs))}" -- "$current")); return;;'
        for name, subs in sorted(tree.items())
        if name
    )
    return f"""# vitruvio bash completion. Install with:
#   vitruvio completion bash > /usr/local/etc/bash_completion.d/vitruvio
_vitruvio() {{
    local current="${{COMP_WORDS[COMP_CWORD]}}"
    local previous_index=1
    local group=""

    while [ $previous_index -lt $COMP_CWORD ]; do
        case "${{COMP_WORDS[$previous_index]}}" in
            -*) ;;
            *) group="${{COMP_WORDS[$previous_index]}}"; break;;
        esac
        previous_index=$((previous_index + 1))
    done

    if [[ "$current" == -* ]]; then
        COMPREPLY=($(compgen -W "{" ".join(GLOBALS)} --help --version" -- "$current"))
        return
    fi

    case "$group" in
{cases}
    esac

    COMPREPLY=($(compgen -W "{groups}" -- "$current"))
}}
complete -F _vitruvio vitruvio
"""


def _zsh() -> str:
    """The zsh script."""
    tree = _tree()
    lines = [f"        {name}) subcommands=({' '.join(sorted(subs))});;" for name, subs in sorted(tree.items()) if name]
    return f"""#compdef vitruvio
# vitruvio zsh completion. Install with:
#   vitruvio completion zsh > "${{fpath[1]}}/_vitruvio"
_vitruvio() {{
    local -a groups subcommands
    groups=({" ".join(sorted(tree[""]))})

    if (( CURRENT == 2 )); then
        _describe 'command' groups
        return
    fi

    case "${{words[2]}}" in
{chr(10).join(lines)}
    esac

    if (( ${{#subcommands}} )); then
        _describe 'subcommand' subcommands
    fi
    _arguments '--brain[the brain to operate on]:brain:_files -/' '--json[emit one JSON envelope]' \\
        '--config[a vitruvio.toml to use verbatim]:config:_files' '--quiet' '--no-color' '--verbose'
}}
_vitruvio "$@"
"""


def _fish() -> str:
    """The fish script."""
    tree = _tree()
    lines = [f"complete -c vitruvio -n __fish_use_subcommand -a '{name}'" for name in sorted(tree[""])]
    for name, subs in sorted(tree.items()):
        if not name:
            continue
        lines += [
            f"complete -c vitruvio -n '__fish_seen_subcommand_from {name}' -a '{' '.join(sorted(subs))}'",
        ]
    lines += [
        "complete -c vitruvio -l json -d 'emit one JSON envelope'",
        "complete -c vitruvio -l brain -r -d 'the brain to operate on'",
        "complete -c vitruvio -l config -r -d 'a vitruvio.toml to use verbatim'",
    ]
    header = "# vitruvio fish completion. Install with:\n#   vitruvio completion fish > ~/.config/fish/completions/vitruvio.fish"
    return header + "\n" + "\n".join(lines) + "\n"


GENERATORS = {"bash": _bash, "zsh": _zsh, "fish": _fish}


@app.default
def completion(shell: str) -> ExitCode:
    """Print a completion script for bash, zsh or fish.

    The script knows the command names and nothing about any brain's contents. That is deliberate: a completion hook
    that called back into `vitruvio` would open a brain to answer, and with a vector index registered that means
    loading a model. Pressing Tab should not do that.

    Parameters
    ----------
    shell
        bash, zsh or fish.
    """
    console = current().console
    if shell not in GENERATORS:
        raise VitruvioError(f"{shell!r} is not supported", hint=f"one of: {', '.join(SHELLS)}")

    script = GENERATORS[shell]()
    # The script *is* the result, so it goes to stdout in both modes -- the point of this command is to be redirected
    # into a file.
    return console.emit("completion", {"shell": shell, "script": script}, lines=[script])
