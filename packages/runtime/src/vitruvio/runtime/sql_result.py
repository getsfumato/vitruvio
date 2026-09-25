"""Typed SQL results: the answer to a query over the brain's derived tables, and the schema those tables have.

A vertical slice in the sense of ADR-0023, and like :mod:`vitruvio.runtime.reconcile_result` it owns its own
serialization. What it serializes is already JSON-native -- :mod:`vitruvio.sql` converted every DuckDB value on the
way out -- so the functions here only name the shape, which is what lets the CLI, an HTTP API and an MCP server read
one declared result rather than three guesses at a dataclass.

Rows are lists, positional under ``columns``, rather than objects. SQL allows two columns of one name --
``SELECT count(*), count(*)`` -- and an object keyed by name would keep one of them.

No ``from __future__ import annotations`` here, for the reason ``block_result`` states: under PEP 563 a ``TypedDict``
reads ``NotRequired`` as a string and files every optional key as required, silently, on 3.11.
"""

from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, cast

if TYPE_CHECKING:
    from vitruvio.sql import SqlOutcome, TableSpec


class SqlColumnResult(TypedDict):
    """One column of a result or a table: its name, and its type as DuckDB spells it."""

    name: str
    type: str


class SqlDegradationResult(TypedDict):
    """Something that went less well than asked, and why."""

    kind: str
    reason: str


class SqlApproximationResult(TypedDict):
    """
    One reason an answer is not exact.

    ``kind`` says which: ``similarity`` for an ``about``/``similarity`` text, with the model that scored each module
    and the modules that could not be scored; ``visibility_unknown`` when no provenance module could say which blocks
    are superseded. The keys each kind carries are present only for that kind.
    """

    kind: str
    reason: NotRequired[str]
    text: NotRequired[str]
    min_score: NotRequired[list[float]]
    models: NotRequired[dict[str, str]]
    unscored: NotRequired[dict[str, str]]


class SqlResult(TypedDict):
    """
    A query's answer, bound to the roots it was computed over.

    ``verified_against`` is the claim a reader checks first: every module the query read, and the Merkle root its
    table was built from. ``hidden`` says how many members were left out as superseded or demoted, per table, and
    ``not_installed`` which tables were empty because their module is not here -- a count of zero from a module this
    brain does not have is not a count of zero from one it does.
    """

    sql: str
    canonical_sql: str
    executed_sql: str
    signature: str
    columns: list[SqlColumnResult]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    limit: int
    tables: list[str]
    verified_against: dict[str, str]
    not_installed: list[str]
    hidden: dict[str, int]
    include_superseded: bool
    exact: bool
    approximate: list[SqlApproximationResult]
    verified_rows: int | None
    degradations: list[SqlDegradationResult]
    plan: str | None


class SqlTableColumnResult(TypedDict):
    """One column a query may name."""

    name: str
    type: str
    doc: str


class SqlTableResult(TypedDict):
    """One table a query may read."""

    name: str
    memory_type: str | None
    doc: str
    columns: list[SqlTableColumnResult]


class SqlSchemaResult(TypedDict):
    """Every table a query may read, and the projection version that defines them."""

    projection: str
    tables: list[SqlTableResult]


def outcome(value: "SqlOutcome") -> SqlResult:
    """A query's outcome, as its declared result."""
    return {
        "sql": value.sql,
        "canonical_sql": value.canonical_sql,
        "executed_sql": value.executed_sql,
        "signature": value.signature,
        "columns": [{"name": column.name, "type": column.type} for column in value.columns],
        "rows": value.rows,
        "row_count": value.row_count,
        "truncated": value.truncated,
        "limit": value.limit,
        "tables": value.tables,
        "verified_against": value.verified_against,
        "not_installed": value.not_installed,
        "hidden": value.hidden,
        "include_superseded": value.include_superseded,
        "exact": value.exact,
        # The engine builds each entry per kind, which no checker can relate to the TypedDict; ADR-0023's one cast.
        "approximate": [cast(SqlApproximationResult, dict(entry)) for entry in value.approximate],
        "verified_rows": value.verified_rows,
        "degradations": [{"kind": item["kind"], "reason": item["reason"]} for item in value.degradations],
        "plan": value.plan,
    }


def schema(tables: "list[TableSpec]", projection: str) -> SqlSchemaResult:
    """The tables a query may read, as their declared result."""
    return {
        "projection": projection,
        "tables": [
            {
                "name": spec.name,
                "memory_type": spec.memory_type.value if spec.memory_type is not None else None,
                "doc": spec.doc,
                "columns": [{"name": column.name, "type": column.type, "doc": column.doc} for column in spec.columns],
            }
            for spec in tables
        ],
    }


__all__ = [
    "SqlApproximationResult",
    "SqlColumnResult",
    "SqlDegradationResult",
    "SqlResult",
    "SqlSchemaResult",
    "SqlTableColumnResult",
    "SqlTableResult",
    "outcome",
    "schema",
]
