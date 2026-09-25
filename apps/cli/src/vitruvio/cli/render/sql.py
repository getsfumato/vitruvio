"""How a SQL result and the SQL schema look in a terminal.

A result is a table followed by the lines that say what it can be trusted for: how many rows, whether they are all
of them, which roots they were computed over, and what was hidden. The table without those lines would look exactly
like an answer from a different brain, or from this one before its last commit.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from rich.console import RenderableType
from rich.text import Text

from vitruvio.cli.render.theme import digest, empty, fields, kind, stack, table

_NUMERIC = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UBIGINT", "FLOAT", "DOUBLE", "DECIMAL")


def _cell(value: Any) -> Text:
    """One value, as a cell: ``null`` dimmed so it cannot be read as the string, nested values as compact JSON."""
    if value is None:
        return Text("null", style="muted")
    if isinstance(value, bool):
        return Text(str(value).lower(), style="info")
    if isinstance(value, list | dict):
        return Text(json.dumps(value, ensure_ascii=False, separators=(", ", ": ")))
    if isinstance(value, str) and value.startswith("sha256:"):
        return digest(value, full=True)
    return Text(str(value))


def _approximation(entry: Mapping[str, Any]) -> Text:
    """One reason an answer is approximate, as a line: which text, at which threshold, by which model."""
    if entry["kind"] != "similarity":
        return Text(entry.get("reason", entry["kind"]), style="warn")
    line = Text(f"similarity to {entry['text']!r}", style="warn")
    if entry.get("min_score"):
        line.append(" at or above " + ", ".join(f"{value:g}" for value in entry["min_score"]))
    models = sorted(set(entry.get("models", {}).values()))
    if models:
        line.append(", scored by " + "; ".join(models), style="muted")
    unscored = sorted(entry.get("unscored", {}))
    if unscored:
        line.append(f"; unscored: {', '.join(unscored)}", style="warn")
    return line


def result(payload: Mapping[str, Any]) -> list[RenderableType]:
    """
    A query's rows, then what they were computed over.

    Args:
        payload (Mapping[str, Any]): A ``SqlResult``.

    Returns:
        list[RenderableType]: The view.
    """
    columns = payload["columns"]
    if payload["rows"]:
        grid = table(
            *(
                (column["name"], "right") if column["type"].startswith(_NUMERIC) else column["name"]
                for column in columns
            )
        )
        for row in payload["rows"]:
            grid.add_row(*(_cell(value) for value in row))
        body: RenderableType = grid
    else:
        body = empty("no rows")

    count = payload["row_count"]
    shown = f"{count} row{'s' if count != 1 else ''}"
    if payload["truncated"]:
        shown += f", truncated at {payload['limit']}"
    over = Text()
    for index, (module, root) in enumerate(sorted(payload["verified_against"].items())):
        if index:
            over.append("  ")
        # In a compound the key is `brain.module`: the brain as plain text, the module in its colour.
        brain, _, memory = module.rpartition(".")
        if brain:
            over.append(f"{brain}.")
        over.append_text(kind(memory))
        over.append(" ")
        over.append_text(digest(root))
    facts: list[tuple[str, Any]] = [("rows", shown)]
    if payload.get("brains"):
        facts.append(("brains", ", ".join(payload["brains"])))
    facts.append(("over", over if over.plain else None))
    hidden = {table: n for table, n in payload["hidden"].items() if n}
    if hidden:
        facts.append(
            ("hidden", ", ".join(f"{n} superseded or demoted in {name}" for name, n in sorted(hidden.items())))
        )
    if payload["verified_rows"] is not None:
        facts.append(("proved", f"{payload['verified_rows']} of the returned blocks"))
    facts.append(("exact", Text("yes", style="ok") if payload["exact"] else Text("approximate", style="warn")))
    for entry in payload["approximate"]:
        facts.append(("because", _approximation(entry)))
    return stack(body, "", fields(facts))


def explanation(payload: Mapping[str, Any]) -> list[RenderableType]:
    """
    What a query becomes, and the engine's plan for it.

    Args:
        payload (Mapping[str, Any]): A ``SqlResult`` from an explain.

    Returns:
        list[RenderableType]: The view.
    """
    head = fields(
        [
            ("query", payload["canonical_sql"]),
            ("executed", payload["executed_sql"]),
            ("tables", ", ".join(payload["tables"]) or None),
            ("signature", digest(payload["signature"], full=True)),
        ]
    )
    return stack(head, "", Text(payload["plan"] or "", style="muted"))


def schema(payload: Mapping[str, Any]) -> list[RenderableType]:
    """
    Every table a query may read, each with its columns.

    Args:
        payload (Mapping[str, Any]): A ``SqlSchemaResult``.

    Returns:
        list[RenderableType]: The view.
    """
    parts: list[RenderableType] = []
    for spec in payload["tables"]:
        grid = table("column", "type", "", title=f"{spec['name']} — {spec['doc']}")
        for column in spec["columns"]:
            grid.add_row(column["name"], Text(column["type"], style="muted"), column["doc"])
        parts.extend((grid, ""))
    parts.append(Text(f"projection {payload['projection']}", style="muted"))
    return parts


__all__ = ["explanation", "result", "schema"]
