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

from pathlib import Path
from typing import Any

from vitruvio.kernel import ResolvedConfig, UsageError
from vitruvio.runtime import sql_result
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession
from vitruvio.runtime.sql_result import SqlResult, SqlSchemaResult


def sql_cache_dir(config: ResolvedConfig) -> Path:
    """Where this brain's derived SQL tables are cached. One definition, so the engine and the purge agree."""
    return config.derived / "sql"


def purge_sql_cache(config: ResolvedConfig) -> int:
    """
    Delete every cached SQL table of this brain, and report how many files went.

    The cache is a copy of block payloads. A cached table is keyed by the composition it was built from, so it is
    never *read* for another -- but it is still on disk after a redaction, a drop or a prune, holding exactly the
    fields those operations exist to make unreadable. Every retention mechanism that removes knowledge calls this,
    and it removes the whole directory's tables rather than guessing which ones named the block: a table is rebuilt
    by the next query that needs it, and an erasure that left one copy behind is not an erasure.

    Needs no extra: deleting files imports no engine, so a brain whose cache was written by an install with
    ``vitruvio[sql]`` is purged by one without it.

    Args:
        config (ResolvedConfig): The brain's configuration.

    Returns:
        int: How many cached files were removed.
    """
    directory = sql_cache_dir(config)
    removed = 0
    if not directory.is_dir():
        return removed
    for entry in directory.iterdir():
        if entry.is_file() and (entry.suffix == ".parquet" or entry.name.startswith(".")):
            entry.unlink(missing_ok=True)
            removed += 1
    return removed


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


class _VectorScorer:
    """
    Similarity for ``about`` and ``similarity``, over the brain's own vector indices.

    Stood up only when a query uses one of them: scoring needs the RETRIEVE brain, which registers the configured
    indices and may resolve an embedder, and a plain count should not pay for either. Each module is scored by its
    own index, exactly and over its whole population, and a module that cannot be scored says why rather than
    contributing silence -- an absent index, one built for another composition, an embedder that is unavailable or
    does not reproduce the stored vectors.
    """

    def __init__(self, session: BrainSession) -> None:
        self.session = session

    def similarity(self, text: str) -> Any:
        from vitruvio.embeddings import EmbedderUnavailableError
        from vitruvio.indices import VectorIndex

        package = _engine_package()
        brain = self.session.brain(Capability.RETRIEVE)
        scores: dict[str, float] = {}
        models: dict[str, str] = {}
        missing: dict[str, str] = {}
        with translated():
            modules = brain.modules()
        for kind, module in sorted(modules.items(), key=lambda item: item[0].value):
            # The same usability rule the planner applies in `CostBasedPlanner.capabilities`: an index bound to
            # another root is stale, and one bound to none was built over this composition in this session.
            vector = module.indices.get("vector")
            if not isinstance(vector, VectorIndex):
                missing[kind.value] = "no vector index is configured for this module"
            elif not vector.population:
                missing[kind.value] = "its vector index is empty; run `vitruvio index build`"
            elif vector.bound_root is not None and vector.bound_root != str(module.root):
                missing[kind.value] = "its vector index describes another composition; run `vitruvio index build`"
            elif not vector.queryable:
                missing[kind.value] = vector.query_failure.replace("_", " ")
            else:
                try:
                    found = vector.similarities(text)
                except EmbedderUnavailableError as error:
                    missing[kind.value] = f"embedder unavailable: {error}"
                    continue
                members = {str(identity) for identity in module.block_ids}
                scores.update({block: score for block, score in found.items() if block in members})
                tag = vector.model_tag or vector.expected_model_tag
                models[kind.value] = f"{tag} (local fallback)" if vector.embedder.tag.is_fallback else tag
        return package.Similarity(scores=scores, models=models, missing=missing)


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

    def _sql_brain(self) -> Any:
        """
        This brain as the engine takes it: its installed modules, its table cache, and a scorer over its vectors.

        Not an operation -- it returns engine inputs, not a result -- but the one method :class:`CompoundOps` reaches
        into: a compound is built from one of these per member, each brain keeping its own cache and scorer, so
        combining brains is a matter of handing several to one engine rather than teaching the engine what a session
        is.

        Returns:
            vitruvio.sql.SqlBrain: The engine's view of this brain.
        """
        package = _engine_package()
        brain = self.session.brain(Capability.BROWSE)
        with translated():
            modules = brain.modules()
        return package.SqlBrain(modules, cache_dir=sql_cache_dir(self.config), scorer=_VectorScorer(self.session))

    def _engine(self) -> Any:
        """An engine over this brain alone."""
        member = self._sql_brain()
        return _engine_package().SqlEngine(member.modules, cache_dir=member.cache_dir, scorer=member.scorer)

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

        ``about(id, 'text', min_score)`` and ``similarity(id, 'text')`` score blocks against a text with the
        brain's vector indices. Only a query that uses them opens the RETRIEVE brain, and its result is always
        marked approximate.

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
        return sql_result.schema(package.SqlEngine.schema(), package.SQL_PROJECTION_ID, package.FORMATS)


__all__ = ["SqlOps", "purge_sql_cache", "sql_cache_dir"]
