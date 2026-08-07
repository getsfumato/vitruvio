"""The command groups, and the one function that attaches them to the app.

Registration is explicit rather than discovered by walking the package. A missing group should be a failing
import at startup, not a subcommand that silently does not exist -- and the order here is the order the
groups appear in ``--help``, which is editorial and worth controlling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vitruvio.cli.commands import (
    brain,
    config,
    dist,
    index,
    ingest,
    inspect,
    query,
    registry,
    source,
    task,
)

if TYPE_CHECKING:
    from cyclopts import App

GROUPS = (brain, source, task, ingest, index, query, dist, registry, inspect, config)
"""Every command-group module, in the order they are shown.

Ordered by the order a user meets them: create a brain, put evidence in it, interpret that evidence, ask it
something, publish it, look at how it is put together, and configure the whole thing.
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
