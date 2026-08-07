"""The ``vitruvio`` entry point: parse, delegate, render.

This module owns three things and deliberately nothing else: the command tree, the global options, and the
translation of an error into an exit code. Every command body calls into ``vitruvio.runtime`` and renders
what comes back. Business logic that lands here instead is logic the MCP server and the HTTP API will not
have.

The global options live on cyclopts' *meta* app, which runs before dispatch: it installs the
:class:`~vitruvio.cli.context.Context` and then hands the remaining tokens to the real app. That is what
keeps ``--brain`` and ``--json`` out of the signature -- and out of the ``--help`` -- of all forty commands.
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
from vitruvio.kernel import ExitCode, VitruvioError, __version__

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


@app.meta.default
def launcher(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    brain: Annotated[Path | None, Parameter(name=["--brain"], help="The brain to operate on.")] = None,
    config: Annotated[Path | None, Parameter(name=["--config"], help="A vitruvio.toml to use verbatim.")] = None,
    actor: Annotated[str | None, Parameter(name=["--actor"], help="Who to attribute writes to.")] = None,
    actor_kind: Annotated[
        # A plain string, coerced by vitruvio.kernel.parse_actor_kind. Taking the SDK's enum here would make
        # this app import boltzmann, which is the one thing the service-layer boundary forbids.
        str | None,
        Parameter(name=["--actor-kind"], help="human, agent, service or pipeline. Set agent when a model drives."),
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
            config=config,
            actor_id=actor,
            actor_kind=actor_kind,
            console=Console(json_mode=json, quiet=quiet, color=not no_color),
            verbosity=verbose,
        )
    )
    return app(tokens)


def main(argv: list[str] | None = None) -> int:
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
    except VitruvioError as error:
        from vitruvio.cli.context import current

        return int(current().console.fail("cli", error))
    except CycloptsError:
        # cyclopts has already rendered the error to stderr -- its formatting is better than anything worth
        # reimplementing here. What it would do next is exit 1, and exit 1 in this CLI means "a bug in
        # vitruvio". A mistyped flag is not that, so it is remapped onto the code that says "you asked wrong,
        # rephrase" and nothing is printed twice.
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
        print(f"internal error: {type(error).__name__}: {error}", file=sys.stderr)
        print(
            "hint: this is a bug in vitruvio -- please report it at https://github.com/getsfumato/vitruvio/issues",
            file=sys.stderr,
        )
        return int(ExitCode.INTERNAL)

    if isinstance(result, int):
        return result
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
