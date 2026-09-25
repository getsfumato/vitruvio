"""The engine: load a brain's derived tables into DuckDB, seal it off from the host, and answer one query.

The order of operations is the security argument, so it is worth stating once. Everything that touches the
filesystem -- reading a cached table, writing one -- happens *before* the caller's SQL exists as far as DuckDB is
concerned. Then external access is switched off and the configuration locked, so no statement can switch it back on,
and only then does the guarded query run. A query that slipped past :mod:`vitruvio.sql.guard` would meet a database
that holds the brain's tables in memory and can reach nothing else.

Tables are cached as Parquet next to the brain's other derived state, one file per module, named by a digest of the
projection version, the module's Merkle root and which of its members are unreadable. A composition that changed, a
block that was redacted, a projection that grew a column: each yields a different name, so a cache is either exactly
right or not found. Nothing is ever invalidated because nothing stale is ever looked up.

The ledger is not cached with the tables. Whether a block is superseded is recorded in the provenance module, so it
can change while a semantic module's root does not; it is joined on at load time instead, from a ledger the caller
passes in or one read here. That makes the provenance module an input to every query that hides anything, and the
outcome says so: its root is reported beside the tables' own, and a brain without it cannot claim an exact answer
over "the accessible blocks", because it cannot tell which those are.

One time budget covers the whole request -- projecting a module, loading a cache, running the query, fetching and
verifying the rows -- because a caller choosing a timeout is choosing how long it is willing to wait for an answer,
not how long DuckDB may spend on the part it happens to own. DuckDB runs single-threaded over tables inserted in
identity order, so an aggregate that depends on row order (``first``, ``list``, a ``LIMIT`` inside a subquery) sees the
same order every time.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from boltzmann.blocks.memory_type import MemoryType

from vitruvio.kernel import UsageError
from vitruvio.sql.guard import GuardedQuery, every_name, guard, visible_name
from vitruvio.sql.result import SqlColumn, SqlOutcome, json_native
from vitruvio.sql.similarity import SCORE_BLOCK, SCORE_VALUE, Scorer, Similarity, similarity_table
from vitruvio.sql.tables import BLOCKS_TABLE, SQL_PROJECTION_ID, TABLES, TableSpec, project_block, table_for

if TYPE_CHECKING:
    import duckdb
    from boltzmann.module.ledger import Ledger
    from boltzmann.module.module import Module

DEFAULT_LIMIT = 1000
"""How many rows a query returns unless the caller says otherwise. A bound on the envelope, not on the answer:
``truncated`` says when there were more."""

DEFAULT_TIMEOUT = 30.0
"""Seconds a query may run before it is interrupted."""

DEFAULT_MEMORY_LIMIT = "2GB"
"""How much memory DuckDB may use for one query."""


class SqlTimeoutError(UsageError):
    """A query ran past its time budget and was interrupted. Narrowing it is the fix, so this is a usage error."""

    code = "SQL_TIMEOUT"


class _Budget:
    """
    One deadline for a whole request, enforced two ways.

    DuckDB work is interrupted when the deadline passes: a timer calls ``interrupt`` on whatever connection is open.
    Python work -- projecting blocks into rows, verifying proofs -- cannot be interrupted from outside, so it calls
    :meth:`check` as it goes. Between the two, no phase of producing an answer runs unbounded.
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.deadline = time.monotonic() + seconds
        self.connection: duckdb.DuckDBPyConnection | None = None
        self._expired = False
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def __enter__(self) -> _Budget:
        self._timer.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._timer.cancel()

    def _fire(self) -> None:
        self._expired = True
        connection = self.connection
        if connection is not None:
            with contextlib.suppress(Exception):
                connection.interrupt()

    def error(self) -> SqlTimeoutError:
        return SqlTimeoutError(
            f"answering the query took more than {self.seconds:g}s and was stopped",
            hint="narrow it with WHERE or aggregate before joining; the first query after a change also builds tables",
        )

    def check(self) -> None:
        """Stop Python-side work once the deadline has passed."""
        if self._expired or time.monotonic() >= self.deadline:
            raise self.error()


def _stored_table(name: str, member: int = 0) -> str:
    return f"__vitruvio_table_{name}__{member}"


def _ledger_table(member: int) -> str:
    return f"__vitruvio_ledger__{member}"


def _literal(text: str) -> str:
    """A path as a SQL string literal. DuckDB takes no parameter for a ``COPY`` target or a table function's path."""
    return "'" + text.replace("'", "''") + "'"


def _column_list(spec: TableSpec) -> str:
    return ", ".join(f'"{column.name}" {column.type}' for column in spec.stored)


def _rows(memory_type: MemoryType, module: Module, check: Callable[[], None]) -> list[dict[str, Any]]:
    """
    Every member of a module, as a row, in identity order so a table's insertion order is deterministic.

    Unreadable means what ``module.resolvable()`` says: tombstoned, or not installed. Those members get their null
    row. A block the store *claims* to hold and then fails to read -- an I/O error, a digest that does not verify --
    is not unreadable, it is a failure, and it propagates. Turning it into a null row would be caching a false
    tombstone under a key that cannot change until the composition does, so a passing fault would become a permanent
    wrong answer.
    """
    resolvable = module.resolvable()
    rows = []
    for identity in sorted(module.block_ids, key=str):
        check()
        payload = None
        version = None
        if resolvable.get(identity, True):
            block = module.get(identity)
            payload = block.payload()
            version = type(block).SCHEMA_VERSION
        rows.append(project_block(memory_type, str(identity), payload, schema_version=version))
    return rows


def _insert(
    connection: duckdb.DuckDBPyConnection, target: str, columns: Mapping[str, str], rows: list[dict[str, Any]]
) -> None:
    """Load rows through newline-delimited JSON, which is the one path that types nested lists of structs -- and,
    unlike ``executemany``, stays fast at a hundred thousand rows."""
    if not rows:
        return
    typed = "{" + ", ".join(f"'{name}': '{kind}'" for name, kind in columns.items()) + "}"
    with tempfile.TemporaryDirectory(prefix="vitruvio-sql-") as scratch:
        path = Path(scratch) / "rows.ndjson"
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False))
                handle.write("\n")
        connection.execute(
            f"INSERT INTO {target} SELECT * FROM read_json({_literal(str(path))}, "
            f"format = 'newline_delimited', columns = {typed})"
        )


def _cache_key(memory_type: MemoryType, module: Module) -> str:
    unreadable = sorted(str(identity) for identity, readable in module.resolvable().items() if not readable)
    material = "\x00".join((SQL_PROJECTION_ID, memory_type.value, str(module.root), *unreadable))
    return hashlib.sha256(material.encode()).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class SqlBrain:
    """
    One brain, as the engine needs it: its modules, and what only its session can supply.

    Attributes:
        modules (Mapping[MemoryType, Module]): The installed modules. A module absent here is an empty table.
        ledger (Ledger | None): What the provenance module says about supersession and demotion. Read from
            ``modules`` when not given; a caller that already holds one -- the planner caches it -- passes it in.
        cache_dir (Path | None): Where this brain's derived tables are cached. ``None`` builds every table afresh.
        scorer (Scorer | None): What scores this brain's blocks against a text, for ``about`` and ``similarity``.
            Asked only when a query uses one of them, so it may be expensive to stand up.
    """

    modules: Mapping[MemoryType, Module]
    ledger: Ledger | None = None
    cache_dir: Path | None = None
    scorer: Scorer | None = None


class SqlEngine:
    """
    SQL over one brain's modules, or over several brains of a project at once.

    Built per request and closed after it. DuckDB runs in memory, so an engine holds a copy of every table it
    loaded; keeping one alive across requests would mean keeping every module in memory for as long as the process
    lives, and answering from a composition the brain may since have moved past.

    **One brain** is the ``modules`` form, and its tables are the ones :data:`~vitruvio.sql.tables.TABLES` lists.
    **A compound** is the ``brains`` form: every brain is loaded into the same database, each with its own ledger,
    cache and scorer. A bare ``semantic`` is then that table across every brain, with a leading ``brain`` column; a
    qualified ``algebra.semantic`` is one brain's. Joins, aggregates and ``GROUP BY brain`` across brains are
    DuckDB's, so nothing about merging results is reimplemented here. Blocks are content-addressed, so a block held
    by two brains is two rows -- one per brain -- which is what ``GROUP BY brain`` should see, and ``count(DISTINCT
    id)`` is how to count it once.

    Args:
        modules (Mapping[MemoryType, Module] | None): One brain's installed modules.
        ledger (Ledger | None): That brain's ledger, if already read. See :class:`SqlBrain`.
        scorer (Scorer | None): That brain's scorer. See :class:`SqlBrain`.
        cache_dir (Path | None): That brain's table cache. See :class:`SqlBrain`.
        brains (Mapping[str, SqlBrain] | None): A compound: the brains by name, in the order they are consulted.
            Give either this or ``modules``.
        timeout (float): Seconds a whole request may take: building or loading tables, running, fetching, verifying.
        memory_limit (str): DuckDB's memory limit, in its own units.
    """

    def __init__(
        self,
        modules: Mapping[MemoryType, Module] | None = None,
        *,
        ledger: Ledger | None = None,
        scorer: Scorer | None = None,
        cache_dir: Path | None = None,
        brains: Mapping[str, SqlBrain] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        memory_limit: str = DEFAULT_MEMORY_LIMIT,
    ) -> None:
        if (modules is None) == (brains is None):
            raise TypeError("give one brain's modules, or the brains of a compound, and not both")
        if brains is not None:
            if not brains:
                raise UsageError("a compound needs at least one brain")
            self._members = [(name, brain) for name, brain in brains.items()]
        else:
            assert modules is not None
            self._members = [("", SqlBrain(dict(modules), ledger=ledger, cache_dir=cache_dir, scorer=scorer))]
        self._compound = brains is not None
        self.timeout = timeout
        self.memory_limit = memory_limit
        self._ledgers: dict[int, Ledger] = {}
        self._scored: dict[tuple[int, str], Similarity] = {}
        self._similarity: dict[str, Similarity] = {}
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._budget: _Budget | None = None

    # --- Public surface ---------------------------------------------------------------------------------------

    @staticmethod
    def schema() -> list[TableSpec]:
        """
        The tables as data rather than as a database.

        Static, and answered without loading anything, because the schema is the projection's and not any brain's:
        a caller asking what it may write -- an agent composing a query, ``vitruvio sql --schema`` -- should not pay
        for projecting a module to learn a column name, nor need a brain at all.
        """
        return [*TABLES.values(), BLOCKS_TABLE]

    @property
    def brains(self) -> tuple[str, ...] | None:
        """The brains a compound consults, in order; ``None`` for a single brain."""
        return tuple(name for name, _ in self._members) if self._compound else None

    def query(
        self,
        sql: str,
        *,
        include_superseded: bool = False,
        limit: int = DEFAULT_LIMIT,
        verify: bool = False,
    ) -> SqlOutcome:
        """
        Run one query.

        Args:
            sql (str): One read-only SQL statement over the brain's tables.
            include_superseded (bool): Read superseded and demoted blocks too.
            limit (int): The most rows to return. More are counted as ``truncated``, not returned.
            verify (bool): When the result has an ``id`` column, check each returned block's inclusion proof
                against its module's root, and drop a row whose proof fails.

        Returns:
            SqlOutcome: The rows, bound to the roots they were computed over.

        Raises:
            UsageError: When the guard refuses the query, or DuckDB cannot bind or run it.
            SqlTimeoutError: When it runs past the timeout.
        """
        if limit < 1:
            raise UsageError("limit must be at least 1")
        guarded = guard(sql, include_superseded=include_superseded, brains=self.brains)
        with self._budgeted() as budget:
            connection = self._open(guarded.references, guarded.similarity)
            cursor = self._run(connection, guarded.executed)
            columns = [SqlColumn(name=str(entry[0]), type=str(entry[1])) for entry in cursor.description or ()]
            fetched = cursor.fetchmany(limit + 1)
            budget.check()
            truncated = len(fetched) > limit
            rows = [[json_native(value) for value in row] for row in fetched[:limit]]

            degradations: list[dict[str, str]] = []
            verified_rows = None
            if verify:
                rows, verified_rows, degradations = self._verify(columns, rows)
            return self._outcome(
                guarded,
                columns=columns,
                rows=rows,
                truncated=truncated,
                limit=limit,
                verified_rows=verified_rows,
                degradations=degradations,
            )

    def explain(self, sql: str, *, include_superseded: bool = False) -> SqlOutcome:
        """
        Report how a query would be run, without running it.

        Returns:
            SqlOutcome: No rows; ``plan`` holds DuckDB's physical plan and ``executed_sql`` what it was made from.
        """
        guarded = guard(sql, include_superseded=include_superseded, brains=self.brains)
        with self._budgeted():
            connection = self._open(guarded.references, guarded.similarity)
            cursor = self._run(connection, f"EXPLAIN {guarded.executed}")
            plan = "\n".join(str(row[-1]) for row in cursor.fetchall())
            return self._outcome(guarded, columns=[], rows=[], truncated=False, limit=0, plan=plan)

    def close(self) -> None:
        """Release the in-memory database."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @contextlib.contextmanager
    def _budgeted(self) -> Iterator[_Budget]:
        """Run one request under the time budget, turning an interruption anywhere in it into the timeout error."""
        import duckdb

        with _Budget(self.timeout) as budget:
            self._budget = budget
            try:
                yield budget
            except duckdb.InterruptException:
                raise budget.error() from None
            finally:
                self._budget = None

    def _check(self) -> None:
        if self._budget is not None:
            self._budget.check()

    def __enter__(self) -> SqlEngine:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # --- Loading ----------------------------------------------------------------------------------------------

    def _key(self, member: int, name: str) -> str:
        """How a module or table of one member is named in an outcome: bare for one brain, ``brain.name`` otherwise."""
        return f"{self._members[member][0]}.{name}" if self._compound else name

    def _targets(self, brain: str | None) -> range | list[int]:
        """The members a table reference reads: all of them for a bare name, one for a qualified one."""
        if brain is None:
            return range(len(self._members))
        return [next(index for index, (name, _) in enumerate(self._members) if name == brain)]

    @staticmethod
    def _references(tables: Iterable[str | tuple[str | None, str]]) -> tuple[tuple[str | None, str], ...]:
        return tuple((None, table) if isinstance(table, str) else table for table in tables)

    def _needed(self, references: Iterable[tuple[str | None, str]]) -> dict[int, dict[MemoryType, TableSpec]]:
        """Per member, the modules a query's tables need. ``blocks`` needs every module that member has installed."""
        needed: dict[int, dict[MemoryType, TableSpec]] = {index: {} for index in range(len(self._members))}
        for brain, table in references:
            for member in self._targets(brain):
                if table == BLOCKS_TABLE.name:
                    modules = self._members[member][1].modules
                    needed[member].update({kind: table_for(kind) for kind in modules})
                else:
                    spec = TABLES[table]
                    assert spec.memory_type is not None
                    needed[member][spec.memory_type] = spec
        return needed

    def _open(
        self, tables: Iterable[str | tuple[str | None, str]], similarity: Iterable[str] = ()
    ) -> duckdb.DuckDBPyConnection:
        """A sealed in-memory database holding exactly the tables a query reads, and the views over them."""
        import duckdb

        self.close()
        references = self._references(tables)
        connection = duckdb.connect(
            ":memory:",
            config={
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
                "memory_limit": self.memory_limit,
                # One thread over tables inserted in identity order: an order-sensitive aggregate or a LIMIT without
                # ORDER BY inside a subquery reads rows in the same order on every run. The brains this answers over
                # are small enough that parallelism would buy milliseconds and cost the guarantee.
                "threads": 1,
                "preserve_insertion_order": True,
            },
        )
        if self._budget is not None:
            self._budget.connection = connection
        try:
            needed = self._needed(references)
            blocks = {
                member for brain, table in references if table == BLOCKS_TABLE.name for member in self._targets(brain)
            }
            for member, specs in needed.items():
                for kind, spec in specs.items():
                    self._check()
                    self._load(connection, member, kind, spec)
                self._check()
                self._load_ledger(connection, member, specs)
                for spec in specs.values():
                    self._views(connection, member, spec)
                if member in blocks:
                    self._blocks_view(connection, member, specs)
            for table in sorted({table for brain, table in references if brain is None}):
                self._union(connection, table)
            for position, text in enumerate(similarity):
                self._check()
                self._load_similarity(connection, position, text)
            self._check()
            # The seal. After these two statements nothing the query says can read or write a file, load an
            # extension, or turn either back on.
            connection.execute("SET enable_external_access = false")
            connection.execute("SET lock_configuration = true")
        except BaseException:
            connection.close()
            raise
        self._connection = connection
        return connection

    def _load(self, connection: duckdb.DuckDBPyConnection, member: int, kind: MemoryType, spec: TableSpec) -> None:
        target = _stored_table(spec.name, member)
        brain = self._members[member][1]
        module = brain.modules.get(kind)
        if module is None:
            connection.execute(f"CREATE TABLE {target} ({_column_list(spec)})")
            return
        cached = None
        if brain.cache_dir is not None:
            cached = brain.cache_dir / f"{spec.name}-{_cache_key(kind, module)}.parquet"
            if cached.is_file():
                connection.execute(f"CREATE TABLE {target} AS SELECT * FROM read_parquet({_literal(str(cached))})")
                return
        self._build(connection, target, spec, _rows(kind, module, self._check))
        if cached is not None:
            self._persist(connection, target, cached, spec.name)

    @staticmethod
    def _build(connection: duckdb.DuckDBPyConnection, target: str, spec: TableSpec, rows: list[dict[str, Any]]) -> None:
        """Create a module's table and fill it."""
        connection.execute(f"CREATE TABLE {target} ({_column_list(spec)})")
        _insert(connection, target, {column.name: column.type for column in spec.stored}, rows)

    def _score(self, member: int, text: str) -> Similarity:
        """One member's scores for a text, asked of its scorer once per request."""
        cached = self._scored.get((member, text))
        if cached is not None:
            return cached
        brain = self._members[member][1]
        if brain.scorer is None:
            scored = Similarity(
                scores={}, missing={kind.value: "no vector indices were made available" for kind in brain.modules}
            )
        else:
            scored = brain.scorer.similarity(text)
        self._scored[(member, text)] = scored
        return scored

    def _load_similarity(self, connection: duckdb.DuckDBPyConnection, position: int, text: str) -> None:
        """Score every block against one text and load the scores as a table, before the seal."""
        if all(brain.scorer is None for _, brain in self._members):
            raise UsageError(
                "about() and similarity() need the brain's vector indices, and none were made available here",
                hint="run the query through `vitruvio sql`, which scores against the brain's vector indices",
            )
        scores: dict[str, float] = {}
        models: dict[str, str] = {}
        missing: dict[str, str] = {}
        for member in range(len(self._members)):
            scored = self._score(member, text)
            # A block two brains both hold is about the text if either scores it so. With one model across a
            # project the two scores agree; with two, `approximate` names both, which is what makes the rule visible.
            for block, score in scored.scores.items():
                scores[block] = max(score, scores.get(block, 0.0))
            models.update({self._key(member, kind): tag for kind, tag in scored.models.items()})
            missing.update({self._key(member, kind): why for kind, why in scored.missing.items()})
        combined = self._similarity[text] = Similarity(scores=scores, models=models, missing=missing)
        if not combined.scores:
            reasons = "; ".join(f"{kind}: {why}" for kind, why in sorted(combined.missing.items())) or "no vectors"
            raise UsageError(
                f"nothing could be scored against {text!r}, so about() would be false for every block ({reasons})",
                hint="build the vector indices with `vitruvio index build`, or check `vitruvio config embedder`",
            )
        table = similarity_table(position)
        connection.execute(f"CREATE TABLE {table} ({SCORE_BLOCK} VARCHAR PRIMARY KEY, {SCORE_VALUE} DOUBLE)")
        rows = [{SCORE_BLOCK: block, SCORE_VALUE: score} for block, score in sorted(combined.scores.items())]
        _insert(connection, table, {SCORE_BLOCK: "VARCHAR", SCORE_VALUE: "DOUBLE"}, rows)

    def _persist(self, connection: duckdb.DuckDBPyConnection, target: str, path: Path, name: str) -> None:
        """Write a table's cache atomically, and drop the ones for compositions this module has moved past."""
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
        try:
            connection.execute(f"COPY {target} TO {_literal(str(partial))} (FORMAT parquet)")
            partial.replace(path)
        finally:
            with contextlib.suppress(OSError):
                partial.unlink()
        for stale in path.parent.glob(f"{name}-*.parquet"):
            if stale != path:
                with contextlib.suppress(OSError):
                    stale.unlink()

    def _load_ledger(
        self, connection: duckdb.DuckDBPyConnection, member: int, needed: Mapping[MemoryType, TableSpec]
    ) -> None:
        table = _ledger_table(member)
        connection.execute(f"CREATE TABLE {table} (id VARCHAR PRIMARY KEY, superseded BOOLEAN, demoted BOOLEAN)")
        if not needed:
            return
        brain = self._members[member][1]
        ledger = self._ledgers.get(member, brain.ledger)
        if ledger is None:
            from boltzmann.module.ledger import Ledger

            ledger = Ledger.of(dict(brain.modules))
        self._ledgers[member] = ledger
        superseded = {str(identity) for identity in ledger.superseded_by}
        demoted = {str(identity) for identity in ledger.demoted}
        entries = [(identity, identity in superseded, identity in demoted) for identity in sorted(superseded | demoted)]
        if entries:
            connection.executemany(f"INSERT INTO {table} VALUES (?, ?, ?)", entries)

    def _brain_column(self, member: int) -> str:
        """A compound's leading ``brain`` column, as a projection; nothing for a single brain."""
        return f"{_literal(self._members[member][0])} AS brain, " if self._compound else ""

    def _views(self, connection: duckdb.DuckDBPyConnection, member: int, spec: TableSpec) -> None:
        stored = ", ".join(f't."{column.name}"' for column in spec.stored)
        connection.execute(
            f"CREATE VIEW {every_name(spec.name, member)} AS SELECT {self._brain_column(member)}{stored}, "
            "coalesce(l.superseded, false) AS superseded, coalesce(l.demoted, false) AS demoted "
            f"FROM {_stored_table(spec.name, member)} AS t LEFT JOIN {_ledger_table(member)} AS l ON l.id = t.id"
        )
        connection.execute(
            f"CREATE VIEW {visible_name(spec.name, member)} AS SELECT * FROM {every_name(spec.name, member)} "
            "WHERE NOT superseded AND NOT demoted"
        )

    def _blocks_view(
        self, connection: duckdb.DuckDBPyConnection, member: int, specs: Mapping[MemoryType, TableSpec]
    ) -> None:
        brain = "brain, " if self._compound else ""
        shared = brain + ", ".join(f'"{column.name}"' for column in BLOCKS_TABLE.columns)
        parts = [f"SELECT {shared} FROM {every_name(spec.name, member)}" for spec in specs.values()]
        if not parts:
            typed = ", ".join(f'CAST(NULL AS {column.type}) AS "{column.name}"' for column in BLOCKS_TABLE.columns)
            parts = [f"SELECT {self._brain_column(member)}{typed} WHERE false"]
        name = BLOCKS_TABLE.name
        connection.execute(f"CREATE VIEW {every_name(name, member)} AS {' UNION ALL '.join(parts)}")
        connection.execute(
            f"CREATE VIEW {visible_name(name, member)} AS SELECT * FROM {every_name(name, member)} "
            "WHERE NOT superseded AND NOT demoted"
        )

    def _union(self, connection: duckdb.DuckDBPyConnection, table: str) -> None:
        """A bare table name: the table across every member, which for one brain is simply that brain's."""
        parts = [f"SELECT * FROM {every_name(table, member)}" for member in range(len(self._members))]
        connection.execute(f"CREATE VIEW {every_name(table)} AS {' UNION ALL '.join(parts)}")
        connection.execute(
            f"CREATE VIEW {visible_name(table)} AS SELECT * FROM {every_name(table)} WHERE NOT superseded AND NOT demoted"
        )

    # --- Running ----------------------------------------------------------------------------------------------

    def _run(self, connection: duckdb.DuckDBPyConnection, sql: str) -> duckdb.DuckDBPyConnection:
        """Run the caller's statement, reporting what DuckDB refuses as the caller's mistake rather than ours."""
        import duckdb

        self._check()
        try:
            return connection.execute(sql)
        except (
            duckdb.ParserException,
            duckdb.BinderException,
            duckdb.CatalogException,
            duckdb.ConversionException,
            duckdb.InvalidInputException,
            duckdb.OutOfRangeException,
            duckdb.NotImplementedException,
            duckdb.PermissionException,
            duckdb.ConstraintException,
            duckdb.TypeMismatchException,
        ) as error:
            first = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
            raise UsageError(first, hint="`vitruvio sql --schema` lists every table and column") from None

    def _verify(
        self, columns: list[SqlColumn], rows: list[list[Any]]
    ) -> tuple[list[list[Any]], int | None, list[dict[str, str]]]:
        """Check the inclusion proof of every block a result names in its ``id`` column, in whichever brain holds it."""
        from boltzmann.identity.digest import BlockId

        position = next((index for index, column in enumerate(columns) if column.name == "id"), None)
        if position is None:
            return (
                rows,
                None,
                [{"kind": "verification_skipped", "reason": "the result has no id column, so no block to prove"}],
            )
        modules = [module for _, brain in self._members for module in brain.modules.values()]
        kept: list[list[Any]] = []
        degradations: list[dict[str, str]] = []
        for row in rows:
            self._check()
            value = row[position]
            try:
                identity = BlockId.parse(value) if isinstance(value, str) else None
            except ValueError:
                identity = None
            module = next((module for module in modules if identity in module), None)
            if identity is None or module is None:
                degradations.append({"kind": "verification_failed", "reason": f"{value!r} is not a member here"})
                continue
            if not module.inclusion_proof(identity).verify(module.root):
                degradations.append({"kind": "verification_failed", "reason": f"{value} does not prove into its root"})
                continue
            kept.append(row)
        return kept, len(kept), degradations

    def _hidden(self, guarded: GuardedQuery) -> dict[str, int]:
        assert self._connection is not None
        counts = {}
        for label, (brain, table) in zip(guarded.tables, guarded.references, strict=True):
            self._check()
            member = None if brain is None else self._targets(brain)[0]
            (count,) = self._connection.execute(
                f"SELECT count(*) FROM {every_name(table, member)} WHERE superseded OR demoted"
            ).fetchone() or (0,)
            counts[label] = int(count)
        return counts

    def _outcome(
        self,
        guarded: GuardedQuery,
        *,
        columns: list[SqlColumn],
        rows: list[list[Any]],
        truncated: bool,
        limit: int,
        verified_rows: int | None = None,
        degradations: list[dict[str, str]] | None = None,
        plan: str | None = None,
    ) -> SqlOutcome:
        needed = self._needed(guarded.references)
        verified_against: dict[str, str] = {}
        not_installed: set[str] = set()
        approximate: list[dict[str, Any]] = []
        degradations = list(degradations or [])
        for member, specs in needed.items():
            modules = self._members[member][1].modules
            verified_against.update(
                {self._key(member, kind.value): str(modules[kind].root) for kind in specs if kind in modules}
            )
            not_installed.update(self._key(member, spec.name) for kind, spec in specs.items() if kind not in modules)
            # Hiding superseded and demoted blocks reads the provenance module, so it is an input to the answer
            # whether or not the query names it. Its root is reported with the others; its absence as what it is.
            if not specs or guarded.include_superseded:
                continue
            provenance = modules.get(MemoryType.PROVENANCE)
            if provenance is not None:
                verified_against[self._key(member, MemoryType.PROVENANCE.value)] = str(provenance.root)
                continue
            where = f" of {self._members[member][0]}" if self._compound else ""
            reason = (
                f"the provenance module{where} is not installed, so superseded and demoted blocks could not be told "
                "apart and none were hidden"
            )
            not_installed.add(self._key(member, MemoryType.PROVENANCE.value))
            approximate.append({"kind": "visibility_unknown", "reason": reason})
            degradations.append({"kind": "visibility_unknown", "reason": reason})

        scope = {self._key(member, kind.value) for member, specs in needed.items() for kind in specs}
        for text in guarded.similarity:
            scored = self._similarity[text]
            missing = {kind: why for kind, why in sorted(scored.missing.items()) if kind in scope}
            approximate.append(
                {
                    "kind": "similarity",
                    "text": text,
                    "min_score": list((guarded.thresholds or {}).get(text, ())),
                    "models": {kind: tag for kind, tag in sorted(scored.models.items()) if kind in scope},
                    "unscored": missing,
                }
            )
            degradations.extend(
                {
                    "kind": "similarity_unscored",
                    "reason": f"{kind} could not be scored against {text!r} ({why}), so none of its blocks is about it",
                }
                for kind, why in missing.items()
            )
        return SqlOutcome(
            sql=guarded.sql,
            canonical_sql=guarded.canonical,
            executed_sql=guarded.executed,
            signature=guarded.signature,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            limit=limit,
            tables=list(guarded.tables),
            verified_against=dict(sorted(verified_against.items())),
            not_installed=sorted(not_installed),
            hidden={} if guarded.include_superseded else self._hidden(guarded),
            include_superseded=guarded.include_superseded,
            exact=not approximate,
            approximate=approximate,
            verified_rows=verified_rows,
            degradations=degradations,
            plan=plan,
            brains=list(self.brains or ()),
        )


__all__ = ["DEFAULT_LIMIT", "DEFAULT_MEMORY_LIMIT", "DEFAULT_TIMEOUT", "SqlBrain", "SqlEngine", "SqlTimeoutError"]
