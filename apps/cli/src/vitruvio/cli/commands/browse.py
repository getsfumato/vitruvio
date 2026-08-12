"""``vitruvio browse`` -- open the brain in a terminal interface.

One command, and one import: nothing under :mod:`vitruvio.cli.tui` is imported until this body runs. Textual is
a larger import than the whole rest of the CLI, and ``config show`` starting in tens of milliseconds is a
property the kernel exists to protect.

Two refusals here rather than in the interface. There is nothing to browse without a terminal, so a piped stdout
is a usage error rather than an application that draws control codes into a file; and ``--json`` names an output
mode this command has no output in, which is worth saying instead of silently opening an interface whose result
no caller can read.

The first refusal has one exemption, and it is the reason ``textual serve 'vitruvio browse'`` works: a served app
is a subprocess whose stdout *is* the display protocol, so it is a pipe by construction and there is a terminal --
a browser emulating one -- at the other end of it.
"""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from vitruvio.cli.context import current
from vitruvio.kernel import ExitCode, UsageError

app = App(
    name="browse",
    help="Open the brain in a terminal interface: modules, blocks, and the content each one names.",
    result_action="return_value",
    exit_on_error=False,
)


@app.default
def browse(
    *,
    memory_type: Annotated[str | None, Parameter(name=["--memory-type", "-m"])] = None,
) -> ExitCode:
    """Read the brain: modules on the left, their blocks in the middle, the selected block on the right.

    The preview draws what a terminal can draw of the bytes a block names — text and Markdown as themselves, an
    image or a PDF page as graphics with `vitruvio[vision]` installed — and `o` hands anything else to whatever
    the desktop opens it with. Beside it are the block's payload, the provenance records that name it, and its
    Merkle inclusion proof, checked.

    The filter box narrows the rows on screen and consults no index. `s` opens the search screen, which is the
    planner's answer; the two are deliberately not the same thing.

    `?` lists every key.

    Parameters
    ----------
    memory_type
        Which module to open on. Defaults to canonical, where the evidence is.
    """
    import os
    import sys

    console = current().console
    if console.json_mode:
        raise UsageError(
            "browse is an interactive interface and has no JSON output",
            hint="`vitruvio inspect blocks <memory-type> --json` is the same data, as an envelope",
        )
    # `textual serve` runs this command as a subprocess and speaks its display protocol over the subprocess'
    # stdout, so stdout is a pipe by construction and the tty check would refuse the one case where a pipe is
    # exactly right. It announces itself by setting TEXTUAL_DRIVER to the web driver, which is what the condition
    # below actually wants to know: not "is stdout a terminal" but "is there a terminal at the other end of it".
    served = "web_driver" in os.environ.get("TEXTUAL_DRIVER", "")
    if not served and not sys.stdout.isatty():
        raise UsageError(
            "browse needs a terminal, and stdout is not one",
            hint="run it directly, or use `vitruvio inspect blocks` when the output is being read by something",
        )

    from vitruvio.cli.tui import MODULES, BrainBrowser

    if memory_type is not None and memory_type not in MODULES:
        raise UsageError(
            f"{memory_type!r} is not a memory type",
            hint=f"one of: {', '.join(MODULES)}",
        )

    from vitruvio.kernel import BrainNotSelectedError

    context = current()
    # A brain that could not be selected opens the picker rather than failing. `browse` is the one command where
    # that is the better answer: it is interactive by definition, the question "which brain did you mean" has a
    # list for an answer, and printing five flags at somebody who is trying to *look* at something is a worse
    # version of the same conversation. Every other command still refuses, because a non-interactive caller
    # cannot answer a question.
    try:
        config = context.resolve()
        # The origin is carried into the interface rather than dropped. Several layers can select a brain and only
        # `--brain` is visible in the command that was typed, so a bare `vitruvio browse` opens *something*, and an
        # interface that showed only the path left "which brain is this and why" unanswerable from inside it.
        browser = BrainBrowser(
            context.service(),
            brain=str(config.brain),
            origin=config.brain_origin.value,
            name=config.brain_name,
            project=config.project_name,
            config_file=str(config.config_file) if config.config_file else None,
        )
    except BrainNotSelectedError:
        # The project is still known even when the brain is not -- that is the whole point of separating the two
        # questions -- so the picker opens on this project rather than on whatever sorts first.
        found = context.config_file()
        browser = BrainBrowser(None, brain="", config_file=str(found) if found else None)

    if memory_type is not None:
        browser.kind = memory_type
    browser.run()
    return ExitCode.OK


__all__ = ["app"]
