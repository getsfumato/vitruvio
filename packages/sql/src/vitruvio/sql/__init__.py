"""SQL over a Boltzmann brain, answered exactly and bound to the roots it was computed over.

Search ranks; this counts. ``search`` answers "what is most relevant to X" with a bundle cut at a limit, which is the
wrong instrument for "how many facts are there about X" -- a count over a top-k is a count of the top-k. This
package answers that second kind of question with SQL, over tables derived from the modules themselves.

Three pieces, in the order a query meets them:

- :mod:`vitruvio.sql.tables` projects every block of a module into a row, one derived table per memory type,
  cached against the module's Merkle root so a table is never read for a composition it was not built from.
- :mod:`vitruvio.sql.guard` parses the query with sqlglot, refuses anything that is not a read, and rewrites the
  brain's table names onto those derived tables -- hiding superseded blocks unless the caller asks for them.
- :mod:`vitruvio.sql.similarity` is the one approximate predicate: ``about(id, 'text', min_score)`` over scores a
  caller-supplied :class:`Scorer` computes, marking every answer that uses it as approximate.
- :mod:`vitruvio.sql.datasets` reads registered CSV, TSV and Parquet files as tables, ``data."ventas.csv"``,
  from the store's verified bytes.
- :mod:`vitruvio.sql.engine` runs what the guard admitted in an in-memory DuckDB whose access to the host is
  switched off before the first character of the caller's SQL reaches it.

Nothing here opens a brain. The caller hands in the modules and the set of blocks the ledger hides, which is what
lets the runtime, a future HTTP API and an MCP server share one engine without any of them reimplementing it.
"""

from __future__ import annotations

from vitruvio.sql.datasets import DATA_SCHEMA, DATASETS_TABLE, FORMATS, Dataset
from vitruvio.sql.engine import DEFAULT_LIMIT, SqlBrain, SqlEngine, SqlTimeoutError
from vitruvio.sql.guard import QUERYABLE, GuardedQuery, guard
from vitruvio.sql.result import SqlColumn, SqlOutcome, json_native
from vitruvio.sql.similarity import Scorer, Similarity
from vitruvio.sql.tables import BLOCKS_TABLE, SQL_PROJECTION_ID, TABLES, Column, TableSpec, project_block

__all__ = [
    "BLOCKS_TABLE",
    "DATASETS_TABLE",
    "DATA_SCHEMA",
    "FORMATS",
    "Dataset",
    "DEFAULT_LIMIT",
    "QUERYABLE",
    "SQL_PROJECTION_ID",
    "TABLES",
    "Column",
    "GuardedQuery",
    "Scorer",
    "Similarity",
    "SqlBrain",
    "SqlColumn",
    "SqlEngine",
    "SqlOutcome",
    "SqlTimeoutError",
    "TableSpec",
    "guard",
    "json_native",
    "project_block",
]
