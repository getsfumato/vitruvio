"""What stands between a caller's SQL and the engine: parse it, refuse it, or rewrite it onto the brain.

The engine is sandboxed on its own -- :mod:`vitruvio.sql.engine` switches DuckDB's access to the host off before any
query runs -- so this is the first of two walls, not the only one. It is the one that can say *why*. A refusal from
DuckDB reads "Permission Error: file system operations are disabled"; a refusal from here names the construct and
what to write instead, which is the difference between an agent that rephrases and one that reports a bug.

Five things happen, in order:

1. **Parse.** One statement, in DuckDB's dialect. Two statements are refused rather than the first one run.
2. **Refuse what is not a read.** The statement must be a query -- a ``SELECT``, a set operation, a ``WITH`` over
   them. DDL, DML, ``COPY``, ``ATTACH``, ``PRAGMA``, ``SET`` and ``INSTALL`` are refused by kind; table functions
   and the file readers are refused by name, so ``FROM read_csv('/etc/passwd')`` never becomes a question for the
   sandbox to answer.
3. **Refuse what cannot have one answer.** ``TABLESAMPLE`` and ``USING SAMPLE`` read a subset while the outcome
   claims every member; ``random()``, ``uuid()``, ``now()`` and their kin return something different on each run.
   Either would make one signature over one set of roots name two answers, so both are refused.
4. **Rewrite the tables.** ``semantic`` becomes the engine's view of that module -- the accessible blocks by
   default, every member when superseded blocks are asked for. The alias stays what the caller wrote, so
   ``semantic.label`` still resolves. A name is a CTE only where that CTE is in scope: a ``WITH semantic`` inside a
   subquery does not shadow the real table outside it.
5. **Make the order total.** A query with no ``ORDER BY`` gets ``ORDER BY ALL``. Without it the same query over the
   same root could return its rows in two orders, and a signature over the query would then name two answers.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vitruvio.kernel import UsageError
from vitruvio.sql.tables import BLOCKS_TABLE, TABLES

if TYPE_CHECKING:
    from sqlglot import exp

QUERYABLE = (*TABLES, BLOCKS_TABLE.name)
"""Every table name a query may use, in the order ``--schema`` lists them."""

_FILE_READERS = ("read_", "glob", "sniff_csv", "parquet_", "iceberg_", "delta_scan", "sqlite_", "postgres_", "mysql_")
"""Function-name prefixes that read from outside the brain. DuckDB's sandbox refuses them too; naming them here is what
makes the refusal say what was refused."""

_VOLATILE = frozenset(
    {
        "random",
        "rand",
        "setseed",
        "uuid",
        "gen_random_uuid",
        "uuidv4",
        "uuidv7",
        "now",
        "today",
        "current_timestamp",
        "current_date",
        "current_time",
        "current_localtimestamp",
        "get_current_timestamp",
        "get_current_time",
        "transaction_timestamp",
        "localtimestamp",
        "localtime",
        "nextval",
        "currval",
    }
)
"""Functions whose value is not determined by their arguments and the tables: each run, or each row, differs."""

_HINT = "vitruvio sql is read-only: write one SELECT over " + ", ".join(QUERYABLE)


def visible_name(table: str) -> str:
    """
    Where a caller's ``FROM semantic`` is pointed by default.

    Prefixed so that no name a caller may write can reach it directly: the guard admits only :data:`QUERYABLE`,
    none of which starts with ``__vitruvio_``, so the only way onto a view is the rewrite that chose it -- and the
    visibility the outcome reports is the visibility the query ran under.
    """
    return f"__vitruvio_visible_{table}"


def every_name(table: str) -> str:
    """Where ``FROM semantic`` is pointed under ``include_superseded``. A separate view rather than a flag on one, so
    the choice is visible in ``executed_sql`` instead of hiding in a join the caller never sees."""
    return f"__vitruvio_all_{table}"


@dataclass(frozen=True, slots=True)
class GuardedQuery:
    """
    A caller's query, admitted and rewritten.

    Attributes:
        sql (str): What the caller wrote.
        canonical (str): The same query as sqlglot writes it back: one spelling per meaning, which is what the
            signature is taken over.
        executed (str): What the engine runs -- canonical, with the tables rewritten and the order made total.
        tables (tuple[str, ...]): The brain tables it reads, sorted, each once.
        ordered (bool): Whether the caller gave an order. When not, ``ORDER BY ALL`` was added.
        include_superseded (bool): Whether the tables were rewritten onto every member rather than the
            accessible ones.
        signature (str): A digest of the canonical query and the visibility it ran under. Two runs with the same
            signature over the same roots return the same rows in the same order.
    """

    sql: str
    canonical: str
    executed: str
    tables: tuple[str, ...]
    ordered: bool
    include_superseded: bool
    signature: str


def _parse(sql: str) -> exp.Expr:
    import sqlglot
    from sqlglot.errors import ParseError, TokenError

    text = sql.strip().rstrip(";").strip()
    if not text:
        raise UsageError("the query is empty", hint=_HINT)
    try:
        statements = [statement for statement in sqlglot.parse(text, dialect="duckdb") if statement is not None]
    except (ParseError, TokenError) as error:
        raise UsageError(f"the query does not parse: {error}", hint=_HINT) from None
    if len(statements) != 1:
        raise UsageError(f"one statement per query, and this has {len(statements)}", hint=_HINT)
    return statements[0]


def _refuse_functions(tree: exp.Expr) -> None:
    from sqlglot import exp

    volatile = tuple(
        getattr(exp, name)
        for name in ("Rand", "Uuid", "CurrentTimestamp", "CurrentDate", "CurrentTime", "Localtimestamp", "Localtime")
        if hasattr(exp, name)
    )
    for function in tree.find_all(exp.Func):
        name = (function.name if isinstance(function, exp.Anonymous) else function.sql_name()).lower()
        if name.startswith(_FILE_READERS) or isinstance(function, exp.ReadCSV):
            raise UsageError(
                f"{name}() reads from outside the brain, and queries may only read the brain's tables",
                hint=_HINT,
            )
        if name in _VOLATILE or isinstance(function, volatile):
            raise UsageError(
                f"{name}() returns a different value on every run, and a query here has one answer per set of roots",
                hint="compute it outside the query, or compare against a literal",
            )
    for sample in tree.find_all(exp.TableSample):
        raise UsageError(
            f"sampling is not supported: {sample.sql(dialect='duckdb')} reads a subset, and the answer claims every "
            "accessible member",
            hint="use LIMIT with ORDER BY for a bounded, repeatable subset",
        )


def _ctes_in_scope(table: exp.Table) -> set[str]:
    """
    The CTE names a table reference can see, walking outward from it.

    A query's ``WITH`` is visible in that query's body and in the CTEs defined after it -- and in the CTE itself when
    the ``WITH`` is recursive -- but not outside the query. Collecting every CTE in the statement into one set, as
    the first version did, let ``EXISTS (WITH semantic AS (...) ...)`` hide the real ``semantic`` in the outer query.
    """
    from sqlglot import exp

    names: set[str] = set()
    child: exp.Expr = table
    node = table.parent
    while node is not None:
        if isinstance(node, exp.With):
            defined = node.expressions
            position = next((index for index, cte in enumerate(defined) if cte is child), len(defined))
            visible = defined[: position + 1] if node.args.get("recursive") else defined[:position]
            names |= {cte.alias_or_name.lower() for cte in visible}
        elif isinstance(node, exp.Query):
            clause = node.args.get("with_")
            if clause is not None and clause is not child:
                names |= {cte.alias_or_name.lower() for cte in clause.expressions}
        child, node = node, node.parent
    return names


def _rewrite_tables(tree: exp.Expr, *, include_superseded: bool) -> tuple[str, ...]:
    from sqlglot import exp

    used: set[str] = set()
    for table in list(tree.find_all(exp.Table)):
        if not isinstance(table.this, exp.Identifier):
            raise UsageError(
                f"table functions are not supported: {table.this.sql(dialect='duckdb')}",
                hint=_HINT,
            )
        name = table.name.lower()
        if table.db or table.catalog:
            qualified = ".".join(part for part in (table.catalog, table.db, table.name) if part)
            raise UsageError(
                f"{qualified!r} names a schema, and a single brain has none",
                hint="query one brain's tables by their bare name: " + ", ".join(QUERYABLE),
            )
        if name in _ctes_in_scope(table):
            continue
        if name not in QUERYABLE:
            raise UsageError(f"there is no table called {table.name!r}", hint="the tables are: " + ", ".join(QUERYABLE))
        used.add(name)
        alias = table.alias
        table.set("this", exp.to_identifier(every_name(name) if include_superseded else visible_name(name)))
        if not alias:
            table.set("alias", exp.TableAlias(this=exp.to_identifier(name)))
    return tuple(sorted(used))


def guard(sql: str, *, include_superseded: bool = False) -> GuardedQuery:
    """
    Admit a query or refuse it, and rewrite what is admitted onto the engine's views.

    Args:
        sql (str): The caller's SQL. One statement; a trailing semicolon is allowed.
        include_superseded (bool): Read every member rather than only the accessible ones.

    Returns:
        GuardedQuery: The admitted query, canonicalised and rewritten.

    Raises:
        UsageError: When the query does not parse, is not a single read, or names a table or a function it may not.
    """
    from sqlglot import exp

    tree = _parse(sql)
    if not isinstance(tree, exp.Query):
        kind = type(tree).__name__.upper()
        raise UsageError(f"only queries are allowed, and this is a {kind} statement", hint=_HINT)
    for node in tree.walk():
        if isinstance(node, exp.DML | exp.DDL | exp.Command | exp.Pragma | exp.Set | exp.Copy):
            raise UsageError(f"only queries are allowed, and this contains a {type(node).__name__.upper()}", hint=_HINT)
    _refuse_functions(tree)

    canonical = tree.sql(dialect="duckdb")
    rewritten = tree.copy()
    tables = _rewrite_tables(rewritten, include_superseded=include_superseded)
    ordered = rewritten.args.get("order") is not None
    if not ordered:
        rewritten = rewritten.order_by(exp.Column(this=exp.Var(this="ALL")), copy=False)

    digest = hashlib.sha256(f"{canonical}\x00superseded={include_superseded}".encode()).hexdigest()
    return GuardedQuery(
        sql=sql,
        canonical=canonical,
        executed=rewritten.sql(dialect="duckdb"),
        tables=tables,
        ordered=ordered,
        include_superseded=include_superseded,
        signature=f"sha256:{digest}",
    )


__all__ = ["QUERYABLE", "GuardedQuery", "every_name", "guard", "visible_name"]
