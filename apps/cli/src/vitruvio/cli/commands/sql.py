"""``vitruvio sql`` -- count, group and join the brain's knowledge with SQL.

The instrument for questions ``search`` cannot answer honestly. Search ranks and cuts at a limit, so "how many facts
are there about X" asked of it returns how many made the top ten. This command answers over every accessible
member, exactly, and says which roots it read.

Nothing about the query lives here. Parsing, refusing, rewriting and running it are :mod:`vitruvio.sql`'s, reached
through the service like every other operation; this module reads the query off the command line and prints what
came back.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter

from vitruvio.cli.context import current
from vitruvio.cli.render import sql as view
from vitruvio.kernel import ExitCode, UsageError

app = App(
    name="sql",
    help="Count, group and join the brain's knowledge with SQL.",
    result_action="return_value",
    exit_on_error=False,
)


def text_of(query: str | None, file: str | None) -> str:
    """The query, from the command line or from a file -- one of the two, and not both."""
    if query is not None and file is not None:
        raise UsageError("give the query as an argument or with --file, not both")
    if file == "-":
        return sys.stdin.read()
    if file is not None:
        path = Path(file)
        if not path.is_file():
            raise UsageError(f"{file} is not a file")
        return path.read_text(encoding="utf-8")
    if query is None:
        raise UsageError(
            "no query given",
            hint='vitruvio sql "SELECT count(*) FROM semantic"; `vitruvio sql --schema` lists the tables',
        )
    return query


@app.default
def sql(
    query: str | None = None,
    *,
    file: Annotated[str | None, Parameter(name=["--file", "-f"], allow_leading_hyphen=True)] = None,
    schema: bool = False,
    explain: bool = False,
    include_superseded: bool = False,
    limit: int = 1000,
    verify: bool = False,
) -> ExitCode:
    """Answer a read-only SQL query over the brain, exactly.

    Every module is a table named for its memory type -- semantic, episodic, procedural, canonical, provenance --
    and `blocks` holds every member of every module. List fields such as `tags`, `participants`, `evidence` and
    `steps` are SQL lists: group over one with `UNNEST`. Superseded and demoted blocks are hidden, as search hides
    them, unless `--include-superseded` is given.

    A registered CSV, TSV or Parquet file is a table too: `data."ventas.csv"`, named for the file it was registered
    from or by its id. `datasets` lists them without reading any.

    Only a single SELECT is accepted. Nothing that writes, and nothing that reads outside the brain, is run.

    `about(id, 'text', min_score)` is true for a block whose similarity to the text is at least `min_score`, scored
    by the brain's vector indices over every block; `similarity(id, 'text')` is that score. A query using either is
    reported as approximate, with the model that scored it.

    Examples:

        vitruvio sql "SELECT subject, count(*) FROM semantic WHERE kind = 'fact' GROUP BY subject"
        vitruvio sql "SELECT tag, count(*) FROM episodic, UNNEST(tags) AS u(tag) GROUP BY tag"
        vitruvio sql "SELECT count(*) FROM semantic WHERE about(id, 'ethics', 0.35)"

    Parameters
    ----------
    query
        One SELECT over the brain's tables.
    file
        Read the query from this file instead, or from stdin with `-`.
    schema
        List every table and column instead of running a query.
    explain
        Show the query as it will run, and the engine's plan, without running it.
    include_superseded
        Read superseded and demoted blocks too. They stay members and stay verifiable; what changed is
        accessibility.
    limit
        The most rows to print. A larger result is reported as truncated, never cut silently.
    verify
        When the result has an `id` column, prove each block it names into its module's root.
    """
    console = current().console
    service = current().service()
    if schema:
        if query is not None or file is not None:
            raise UsageError("--schema lists the tables and takes no query")
        described = service.sql_schema()
        return console.emit("sql", described, view=view.schema(described))

    text = text_of(query, file)
    if explain:
        explained = service.sql_explain(text, include_superseded=include_superseded)
        return console.emit("sql", explained, view=view.explanation(explained))

    result = service.sql(text, include_superseded=include_superseded, limit=limit, verify=verify)
    report(console, result, limit)
    return console.emit("sql", result, view=view.result(result))


def report(console: Any, result: Mapping[str, Any], limit: int) -> None:
    """Warn about everything that makes a result less than it looks: truncation, absent modules, degradations.

    Shared with `compound sql`, whose result says the same things about several brains at once.
    """
    if result["truncated"]:
        console.warn(f"the result is truncated at {limit} rows; raise --limit or aggregate further")
    for name in result["not_installed"]:
        console.warn(f"the {name} module is not installed here, so its table was empty")
    for degradation in result["degradations"]:
        console.warn(f"{degradation['kind']}: {degradation['reason']}")


__all__ = ["app", "report", "text_of"]
