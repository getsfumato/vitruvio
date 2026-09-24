"""SQL over the brain, as operations.

The third way of reading a brain, beside browsing and retrieval, and the one that counts. ``search`` answers "what
is most relevant to X" with a bundle cut at a limit; ``sql`` answers "how many", "grouped how" and "which ones" over
every accessible member, exactly, bound to the roots it read.

Everything that makes that true lives in :mod:`vitruvio.sql`: the tables, the guard, the sealed engine. What is
here is what only a session knows -- which modules are installed, what the ledger hides, where this brain keeps its
derived state -- handed to that package, and its outcome given a declared shape. An HTTP API or an MCP server calls
these same three methods and gets the same answer the CLI prints.

The engine ships behind the ``sql`` extra, because DuckDB is a binary wheel most installs never need. Asking for it
without the extra is a usage error naming the extra, never an ``ImportError`` reported as a bug.
"""

from __future__ import annotations

from typing import Any

from vitruvio.kernel import ResolvedConfig, UsageError
from vitruvio.runtime import sql_result
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession
from vitruvio.runtime.sql_result import SqlResult, SqlSchemaResult


def _engine_package() -> Any:
    """The SQL package, or a usage error naming the extra that provides it."""
    try:
        import vitruvio.sql as package
    except ImportError as error:
        raise UsageError(
            "SQL queries need the engine, which is not installed",
            hint="install vitruvio[sql]",
        ) from error
    return package


class SqlOps:
    """SQL over the brain's derived tables, as operations."""

    def __init__(self, session: BrainSession) -> None:
        """
        Args:
            session (BrainSession): The shared session.
        """
        self.session = session

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration, read through the session that owns it."""
        return self.session.config

    def _engine(self) -> Any:
        """An engine over this brain's installed modules, caching tables beside its other derived state."""
        package = _engine_package()
        brain = self.session.brain(Capability.BROWSE)
        return package.SqlEngine(brain.modules(), cache_dir=self.config.derived / "sql")

    def sql(
        self,
        query: str,
        *,
        include_superseded: bool = False,
        # A literal rather than ``vitruvio.sql.DEFAULT_LIMIT``: the generated facade copies this signature, and the
        # package that names the constant sits behind an extra that may be absent.
        limit: int = 1000,
        verify: bool = False,
    ) -> SqlResult:
        """
        Answer one read-only SQL query over the brain.

        Every module is a table named for its memory type -- ``semantic``, ``episodic``, ``procedural``,
        ``canonical``, ``provenance`` -- and ``blocks`` is every member of every module. Superseded and demoted
        blocks are hidden, as search hides them, unless ``include_superseded`` says otherwise.

        Args:
            query (str): One ``SELECT`` over those tables. Anything that is not a read is refused.
            include_superseded (bool): Read superseded and demoted blocks too.
            limit (int): The most rows to return; ``truncated`` says when there were more.
            verify (bool): When the result has an ``id`` column, prove each block it names into its root.

        Returns:
            SqlResult: The rows, and the roots of every module they were computed over.
        """
        with translated(), self._engine() as engine:
            return sql_result.outcome(
                engine.query(query, include_superseded=include_superseded, limit=limit, verify=verify)
            )

    def sql_explain(self, query: str, *, include_superseded: bool = False) -> SqlResult:
        """
        Report how a query would be run, without running it.

        Args:
            query (str): The query.
            include_superseded (bool): Explain it over every member rather than the accessible ones.

        Returns:
            SqlResult: No rows; ``plan`` holds the engine's physical plan and ``executed_sql`` what it was made from.
        """
        with translated(), self._engine() as engine:
            return sql_result.outcome(engine.explain(query, include_superseded=include_superseded))

    def sql_schema(self) -> SqlSchemaResult:
        """
        Every table a query may read, and every column it may name.

        Opens no brain: the schema is the projection's, and the same for every brain this version of vitruvio
        reads. A module this brain lacks is still a table -- an empty one.

        Returns:
            SqlSchemaResult: The tables, their columns and types, and the projection version defining them.
        """
        package = _engine_package()
        return sql_result.schema(package.SqlEngine.schema(), package.SQL_PROJECTION_ID)


__all__ = ["SqlOps"]
