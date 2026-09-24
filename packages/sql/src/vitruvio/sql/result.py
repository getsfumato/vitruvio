"""What running a query hands back: the rows, and everything a reader needs to trust them.

JSON-native all the way down. DuckDB returns ``datetime``, ``Decimal``, nested lists and structs; they are converted
here, once, so the runtime's serializer has nothing left to decide and an API or an MCP server returns exactly what
the CLI prints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class SqlColumn:
    """
    One column of a result.

    Attributes:
        name (str): What DuckDB called it: the alias if the query gave one, the expression otherwise.
        type (str): Its DuckDB type, as DuckDB spells it.
    """

    name: str
    type: str


@dataclass(frozen=True, slots=True)
class SqlOutcome:
    """
    A query's answer, bound to the roots it was computed over.

    Attributes:
        sql (str): What the caller wrote.
        canonical_sql (str): The same query, as the guard spells it back.
        executed_sql (str): What the engine ran: canonical, with the tables rewritten onto the brain's views.
        signature (str): Identifies the question. The same signature over the same roots is the same answer.
        columns (list[SqlColumn]): The result's columns, in order.
        rows (list[list[Any]]): The rows, JSON-native, at most ``limit`` of them.
        row_count (int): How many rows are in ``rows``.
        truncated (bool): Whether the query produced more rows than ``limit``.
        limit (int): The most rows returned.
        tables (list[str]): The brain tables the query read.
        verified_against (dict[str, str]): Each module read, and the Merkle root its table was built from.
        not_installed (list[str]): Tables the query read whose module this brain does not have. They were empty
            rather than an error, since a selectively pulled brain is a legitimate state -- but a count of zero
            from a module that is not here is not a count of zero from one that is, so it is said.
        hidden (dict[str, int]): Per table read, how many members were hidden as superseded or demoted. Empty when
            superseded blocks were included.
        include_superseded (bool): Whether superseded and demoted blocks were included.
        exact (bool): Whether every value is exact. True unless a predicate was approximate by construction.
        approximate (list[dict[str, Any]]): What made the answer approximate, when something did.
        verified_rows (int | None): When rows were verified, how many inclusion proofs checked out. ``None`` when
            they were not asked for.
        degradations (list[dict[str, str]]): What went less well than asked, and why.
        plan (str | None): DuckDB's physical plan, when the query was explained rather than run.
    """

    sql: str
    canonical_sql: str
    executed_sql: str
    signature: str
    columns: list[SqlColumn]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    limit: int
    tables: list[str]
    verified_against: dict[str, str]
    not_installed: list[str]
    hidden: dict[str, int]
    include_superseded: bool
    exact: bool = True
    approximate: list[dict[str, Any]] = field(default_factory=list)
    verified_rows: int | None = None
    degradations: list[dict[str, str]] = field(default_factory=list)
    plan: str | None = None


def json_native(value: Any) -> Any:
    """
    One value from DuckDB, as JSON can carry it.

    Timestamps come back as naive UTC -- the tables store them that way -- and leave with the ``Z`` the protocol
    wrote them with. A ``Decimal`` that is whole becomes an ``int`` and any other becomes a ``float``: a sum over
    integer columns is a ``HUGEINT`` DuckDB hands over as ``Decimal``, and ``"3"`` where a count belongs would make
    every caller parse it back.

    Args:
        value (Any): What DuckDB returned.

    Returns:
        Any: The same value, JSON-native.
    """
    if isinstance(value, dict):
        converted: Any = {str(key): json_native(item) for key, item in value.items()}
    elif isinstance(value, list | tuple):
        converted = [json_native(item) for item in value]
    elif value is None or isinstance(value, bool | int | float | str):
        converted = value
    elif isinstance(value, datetime):
        stamp = value.isoformat()
        converted = stamp if value.tzinfo is not None else f"{stamp}Z"
    elif isinstance(value, date | time):
        converted = value.isoformat()
    elif isinstance(value, Decimal):
        converted = int(value) if value == value.to_integral_value() else float(value)
    elif isinstance(value, bytes | bytearray | memoryview):
        converted = bytes(value).hex()
    else:
        converted = str(value)
    return converted


__all__ = ["SqlColumn", "SqlOutcome", "json_native"]
