"""``vitruvio browse`` -- open the brain in a terminal interface.

One command, and one import: nothing under :mod:`vitruvio.cli.tui` is imported until this body runs. Textual is
a larger import than the whole rest of the CLI, and ``config show`` starting in tens of milliseconds is a
property the kernel exists to protect.

Two refusals here rather than in the interface. There is nothing to browse without a terminal, so a piped stdout
is a usage error rather than an application that draws control codes into a file; and ``--json`` names an output
mode this command has no output in, which is worth saying instead of silently opening an interface whose result
no caller can read.
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
    import sys

    console = current().console
    if console.json_mode:
        raise UsageError(
            "browse is an interactive interface and has no JSON output",
            hint="`vitruvio inspect blocks <memory-type> --json` is the same data, as an envelope",
        )
    if not sys.stdout.isatty():
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

    context = current()
    config = context.resolve()
    # The origin is carried into the interface rather than dropped. Four layers can select a brain -- `--brain`,
    # `$VITRUVIO_BRAIN`, a vitruvio.toml on the walk up, and whatever `brain use` last recorded -- and only the
    # first is visible in the command that was typed. A bare `vitruvio browse` therefore opens *something*, and an
    # interface that showed only the path left "which brain is this and why" unanswerable from inside it.
    browser = BrainBrowser(
        context.service(),
        brain=str(config.brain),
        origin=config.brain_origin.value,
        name=config.brain_name,
    )
    if memory_type is not None:
        browser.kind = memory_type
    browser.run()
    return ExitCode.OK


__all__ = ["app"]
