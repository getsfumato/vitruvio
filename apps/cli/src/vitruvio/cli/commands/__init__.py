"""The command groups, and the one function that attaches them to the app.

Registration is explicit rather than discovered by walking the package. A missing group should be a failing
import at startup, not a subcommand that silently does not exist -- and the order here is the order the
groups appear in ``--help``, which is editorial and worth controlling.
"""

from __future__ import annotations

from cyclopts import App

from vitruvio.cli.commands import (
    bench,
    brain,
    browse,
    completion,
    config,
    dist,
    index,
    ingest,
    inspect,
    project,
    query,
    reconcile,
    registry,
    retain,
    skills,
    source,
    task,
    update,
)

GROUPS = (
    project,
    brain,
    source,
    task,
    ingest,
    index,
    query,
    browse,
    retain,
    dist,
    reconcile,
    registry,
    inspect,
    config,
    skills,
    completion,
    bench,
    update,
)
"""Every command-group module, in the order they are shown.

Ordered by the order a user meets them: create a brain, put evidence in it, interpret that evidence, ask it
something, read it, publish it, look at how it is put together, and configure the whole thing.

``update`` comes last because it is about the tool rather than about a brain -- the only group here that
never opens one.

``reconcile`` follows ``dist`` because that is the order somebody meets it: you publish, the push is refused
because somebody else published first, and reconciling is what you do about it.

``browse`` sits beside ``query`` rather than under ``inspect``, where its data comes from, because the order
here is editorial: it is what somebody reaches for right after their first search, and a reader looking for
"show me what is in there" does not look inside a group called inspect.
"""


def register(app: App) -> None:
    """
    Attach every command group to the root app.

    Args:
        app (App): The root cyclopts app.
    """
    for group in GROUPS:
        app.command(group.app)
    # `vitruvio search TEXT` as a top-level alias for `vitruvio query search`. It is the single most-typed
    # command, and an agent driving this through a skill should not spend a token on the group name.
    app.command(query.search, name="search")
