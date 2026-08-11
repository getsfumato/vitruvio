"""Human rendering. Nothing here is reached in ``--json`` mode.

Kept separate from the command bodies so that a command reads as "resolve, call the service, hand the result
to a renderer" -- and so that rendering churn, which is expected and frequent, never touches the code that
decides what an operation does.

Every renderer returns a Rich renderable, or a list of them, and prints nothing itself. Where it goes and
whether colour is permitted are :class:`~vitruvio.cli.output.Console`'s decisions, and a renderer holding its
own console is a renderer that can write into a JSON stream.
"""

from __future__ import annotations

from vitruvio.cli.render import media
from vitruvio.cli.render.evidence import bundle, modules, payload, records, rows, short, snapshot
from vitruvio.cli.render.theme import THEME, count, digest, empty, fields, kind, lines, stack, table, verdict

__all__ = [
    "THEME",
    "bundle",
    "count",
    "digest",
    "empty",
    "fields",
    "kind",
    "lines",
    "media",
    "modules",
    "payload",
    "records",
    "rows",
    "short",
    "snapshot",
    "stack",
    "table",
    "verdict",
]
