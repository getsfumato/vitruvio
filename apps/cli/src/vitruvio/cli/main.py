"""The ``vitruvio`` entry point: parse, delegate, render.

This module owns three things and deliberately nothing else: the command tree, the global options, and the
translation of an error into an exit code. Every command body calls into ``vitruvio.runtime`` and renders
what comes back. Business logic that lands here instead is logic the MCP server and the HTTP API will not
have.

The global options live on cyclopts' *meta* app, which runs before dispatch: it installs the
:class:`~vitruvio.cli.context.Context` and then hands the remaining tokens to the real app. That is what
keeps ``--brain`` and ``--json`` out of the signature -- and out of the ``--help`` -- of all forty commands.

``--project`` and ``--brain`` together are why they are *global*. One invocation states its whole context, so
three terminals -- or three agents -- can hold three projects and three brains at once, none of them
depending on the working directory and none of them sharing a saved pointer with the others.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError

from vitruvio.cli import commands
from vitruvio.cli.context import Context, install
from vitruvio.cli.output import Console
from vitruvio.kernel import ExitCode, UsageError, VitruvioError, __version__

app = App(
    name="vitruvio",
    version=__version__,
    help=(
        "Run a Boltzmann brain: portable, verifiable, model-agnostic knowledge.\n\n"
        "A brain conserves, validates and retrieves knowledge; an external model interprets it. Every "
        "command returns data, never prose -- pass --json when something other than a human is reading."
    ),
    # cyclopts' default is `print_non_int_sys_exit`, which calls sys.exit() on an int return. Commands here
    # return an ExitCode, so the default would make `main()` unable to return at all -- every in-process
    # call would raise SystemExit, and the exit-code contract would be untestable. Exiting is the entry
    # point's job, in exactly one place, at the bottom of this module.
    result_action="return_value",
    exit_on_error=False,
)

commands.register(app)

# The meta app only owns global context options. Let the command app decide whether ``--version`` means the
# program version or a command-local option: otherwise Cyclopts eagerly consumes ``update --version 1.2.3``
# before the launcher can dispatch it. Plain ``vitruvio --version`` still reaches the root app below.
app.meta.version_flags = []


@app.meta.default
def launcher(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    brain: Annotated[
        Path | None,
        Parameter(name=["--brain"], help="The brain to operate on: a name the project declares, or a path."),
    ] = None,
    project: Annotated[
        str | None,
        Parameter(
            name=["--project"],
            help="The project to operate in, by name. Works from any directory once `project register` knows it.",
        ),
    ] = None,
    config: Annotated[Path | None, Parameter(name=["--config"], help="A vitruvio.toml to use verbatim.")] = None,
    actor: Annotated[str | None, Parameter(name=["--actor"], help="Who to attribute writes to.")] = None,
    actor_kind: Annotated[
        # A plain string, coerced by vitruvio.kernel.parse_actor_kind. Taking the SDK's enum here would make
        # this app import boltzmann, which is the one thing the service-layer boundary forbids.
        str | None,
        Parameter(name=["--actor-kind"], help="human, agent, service or pipeline. Set agent when a model drives."),
    ] = None,
    assisted_by: Annotated[
        list[str] | None,
        Parameter(
            name=["--assisted-by"],
            help="Actor id that assisted this invocation. Repeat for several; each flag denotes an agent.",
        ),
    ] = None,
    json: Annotated[
        bool, Parameter(name=["--json"], help="Emit one JSON envelope on stdout. Implies --quiet.")
    ] = False,
    quiet: Annotated[bool, Parameter(name=["--quiet", "-q"], help="Suppress notes on stderr.")] = False,
    no_color: Annotated[bool, Parameter(name=["--no-color"], help="Disable colour in human output.")] = False,
    verbose: Annotated[int, Parameter(name=["--verbose", "-v"], help="Repeat for more detail on stderr.")] = 0,
) -> Any:
    """Install the global options, then dispatch."""
    install(
        Context(
            brain=brain,
            project=project,
            config=config,
            actor_id=actor,
            actor_kind=actor_kind,
            assisted_by=assisted_by,
            console=Console(json_mode=json, quiet=quiet, color=not no_color),
            verbosity=verbose,
        )
    )
    return app(tokens)


ISSUES_URL = "https://github.com/getsfumato/vitruvio/issues"
"""Where a bug goes. Named once, because it is printed from two paths that must not drift."""


def _console(tokens: list[str]) -> Console:
    """
    The console to report a launcher-level failure through.

    Built from the tokens rather than read from the installed context, and that is not a shortcut. The context
    lives in a ``ContextVar`` that outlives one call, so an in-process caller -- the test suite, and anything that
    embeds ``main`` -- would otherwise inherit the previous run's mode: a plain invocation after a ``--json`` one
    would print an envelope nobody asked for. It also has to work when the failure happened *before* the meta app
    installed anything, which is exactly the ``vitruvio --json --typo`` case this exists for.

    ``--json`` is the only spelling of the flag (`main.py` declares it with no alias and no negative form), so
    membership is the whole test.

    Args:
        tokens (list[str]): The argument list, as the launcher received it.

    Returns:
        Console: A console matching what this invocation asked for.
    """
    return Console(json_mode="--json" in tokens)


def _notify_of_update(tokens: list[str]) -> None:
    """
    Say once a day that a newer vitruvio exists, on stderr, after the command has already done its work.

    Told rather than done: it prints a line naming `vitruvio update` and never installs anything and never
    asks anything. A prompt here would appear after an unrelated command, and would hang whatever was driving
    the CLI the first time it ran unattended.

    Silent in every case where a line would be wrong rather than merely unwanted:

    * ``--json`` -- a caller is parsing one envelope, and prose on stderr beside it is at best noise;
    * ``--quiet`` -- that is what it asks for;
    * no terminal on stderr -- a log file or a pipe is nobody reading;
    * the ``update`` command itself, which reports this better and was asked to;
    * ``VITRUVIO_NO_UPDATE_CHECK``, honoured inside :mod:`vitruvio.kernel.updates`.

    The request is made at most once per TTL and never more than :data:`~vitruvio.kernel.updates.TIMEOUT`
    seconds, and every failure resolves to "nothing known". Wrapped in a bare ``except`` besides: this runs
    after a command has *succeeded*, and there is no failure here worth converting that into a non-zero exit.

    Args:
        tokens (list[str]): The command line, to tell which command ran.
    """
    try:
        from vitruvio.cli.context import current
        from vitruvio.kernel import updates

        if any(token == "update" for token in tokens):
            return
        console = current().console
        if console.json_mode or console.quiet or not sys.stderr.isatty():
            return

        found = updates.check() if updates.is_due() else updates.cached_update()
        if found.available:
            console.note(f"vitruvio {found.latest} is available (you have {found.current}) -- run `vitruvio update`")
    except Exception:
        return


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0911
    """
    Run the CLI and return a process exit status.

    Errors are caught here rather than in each command so that the exit-code contract is stated in exactly
    one place. Three cases, and the distinction is what a caller actually needs:

    * a :class:`~vitruvio.kernel.VitruvioError` carries its own code and hint -- it is an expected failure,
      reported through whichever output mode is active;
    * ``KeyboardInterrupt`` is the conventional 130, not a crash report;
    * anything else is a bug in vitruvio, and says so rather than pretending the user did something wrong.

    Args:
        argv (list[str] | None): Arguments, excluding the program name. Defaults to ``sys.argv[1:]``.

    Returns:
        int: The exit status.
    """
    tokens = sys.argv[1:] if argv is None else argv
    try:
        result = app.meta(tokens)
        _notify_of_update(tokens)
    except VitruvioError as error:
        from vitruvio.cli.context import current

        return int(current().console.fail("cli", error))
    except CycloptsError:
        # cyclopts has already rendered the error to stderr -- its formatting is better than anything worth
        # reimplementing here. What it would do next is exit 1, and exit 1 in this CLI means "a bug in
        # vitruvio". A mistyped flag is not that, so it is remapped onto the code that says "you asked wrong,
        # rephrase" and nothing is printed twice.
        #
        # In --json mode the envelope still has to appear. A caller is told to branch on `ok` and then on
        # `error.code`, and it was getting empty stdout to parse -- the one output shape the contract in
        # output.py rules out. Human mode stays silent, because cyclopts already wrote the better message.
        console = _console(tokens)
        if console.json_mode:
            return int(console.fail("cli", UsageError("the command line could not be parsed")))
        return int(ExitCode.USAGE)
    except SystemExit as exit_request:
        # --help and --version are implemented by cyclopts as an exit. In-process callers, including the test
        # suite, need a return value rather than an unwinding exception.
        return int(exit_request.code or 0)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        # The last line of defence. Catching broadly is the point: an unhandled traceback tells a user
        # nothing actionable, and in --json mode it would corrupt the stream a caller is parsing.
        #
        # It was corrupting it by omission instead: nothing reached stdout, so a caller parsing the envelope
        # found an empty stream and could not tell a crash from a command that printed nothing.
        console = _console(tokens)
        if console.json_mode:
            wrapped = VitruvioError(
                f"internal error: {type(error).__name__}: {error}",
                hint=f"this is a bug in vitruvio -- please report it at {ISSUES_URL}",
            )
            return int(console.fail("cli", wrapped))
        print(f"internal error: {type(error).__name__}: {error}", file=sys.stderr)
        print(f"hint: this is a bug in vitruvio -- please report it at {ISSUES_URL}", file=sys.stderr)
        return int(ExitCode.INTERNAL)

    if isinstance(result, int):
        return result
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
