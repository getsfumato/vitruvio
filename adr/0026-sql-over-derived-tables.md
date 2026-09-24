# ADR-0026: SQL over derived tables, guarded by sqlglot and run by a sealed DuckDB

**Decision status:** Accepted.

## Context

Search ranks. It answers "what is most relevant to X" with an Evidence Bundle cut at a limit, and its filters are a
fixed conjunction: memory type, subject, time window, tags, classes, evidence. The brain had no honest way to answer
"how many facts are there about X", "how many episodes with P since March, per tag" or "which procedures use this
concept". A count taken from a bundle counts the top-k. OR, NOT, grouping and following a reference from one memory
type into another were not expressible at all.

There were three ways to add this:
- A hand-written query language compiled onto the bitmap and B-tree indices.
- A parser such as sqlglot with an evaluator of our own.
- A real SQL engine over tables projected from the blocks.

The first two keep every answer on the indices, but each meant writing and keeping a query engine. Both would also
stop at whatever subset we wrote: joins, `HAVING` and window functions would each be more work.

## Decision

**Every module is a table, derived from its blocks, and SQL runs over those tables in DuckDB.** The work lives in a
new distribution, `vitruvio-sql` (`vitruvio.sql`), which sits beside the planner and below the runtime. Nothing in
it opens a brain. The runtime passes it the installed modules and the planner's ledger through `SqlOps`, and every
interface reaches it through `BrainService`: the CLI's `vitruvio sql`, and the future HTTP API and MCP server.

- **Tables.**
  - One table per memory type, named for it. `blocks` is the identity columns over every module.
  - Every member of the composition gets a row, including one whose bytes are gone. That row has
    `resolvable = false` and null fields: a redacted block still proves into the root, so it still counts.
  - Values are stored as the block wrote them, not folded the way an index folds them.
  - List fields stay lists and steps stay structs, so grouping over a list is an explicit `UNNEST`.
  - Timestamps are naive UTC.
  - The column set is versioned by `SQL_PROJECTION_ID`.
- **Cache.**
  - A table is cached as Parquet under `<brain>/.vitruvio/sql/`.
  - The file is named by a digest of the projection version, the module's root and its set of unreadable members.
  - A cache file is therefore right for exactly one composition, and is never invalidated because nothing stale is
    ever looked up. Files for older compositions are removed when a newer one is written.
  - The cache does not travel with the brain; it is rebuilt locally.
- **The ledger is joined in when tables are loaded, not stored with them.**
  - Supersession and demotion are recorded in the provenance module, so they can change while another module's
    root does not.
  - Superseded and demoted blocks are hidden by default, as search hides them, and `include_superseded` shows them.
  - Each result reports how many members it hid.
- **The guard (sqlglot).**
  - It accepts one statement, and that statement must be a query.
  - It refuses by construct: DDL and DML, `COPY`, `ATTACH`, `INSTALL`, `PRAGMA`, `SET`, table functions, and
    file-reading functions.
  - It rewrites table names onto the engine's views and keeps the caller's aliases.
  - It adds `ORDER BY ALL` when the query gives no order, so one question over one root has one answer.
  - Its signature is a digest of the canonical query plus the visibility it ran under.
- **The seal (DuckDB).**
  - Tables are loaded before any caller SQL runs.
  - Then external access is switched off and the configuration locked, so no statement can read or write a file,
    load an extension, or turn access back on.
  - The guard is the wall that explains a refusal; the seal is the wall that holds if the guard misses something.
  - Queries run under a timeout and a memory limit.
- **Honesty in the result.**
  - `verified_against` names the root of every module read.
  - `not_installed` names tables that were empty because their module is absent. Zero from a module that is not
    here is different from zero from one that is.
  - `truncated` reports a row limit that was hit, instead of cutting the result silently.
  - `--verify` proves every block the result names in an `id` column.
  - `exact` is true. Only a future similarity predicate, which needs an explicit threshold, may set it to false,
    and it will list why.
- **Packaging.** The distribution sits behind the `sql` extra of `vitruvio-runtime` and the CLI. Without it,
  `vitruvio sql` is a usage error naming the extra.

## Consequences

- Joins across memory types, `HAVING`, CTEs and window functions come for free, because they are DuckDB's.
- Queries do not use the bitmap and B-tree indices. DuckDB scans columnar tables, which on brains of thousands to
  millions of blocks costs milliseconds. The first query after a composition changes pays for projecting that module.
- DuckDB is a binary wheel of 10 to 30 MB. That is why the engine is an extra rather than part of the default install.
- Changing a column is a change to what saved queries mean. It needs a `SQL_PROJECTION_ID` bump and a changelog entry.
- Later phases:
  - an `about(text, min_score)` UDF over the vector index, which makes a result approximate and says so;
  - `FROM brain.table` across a project's brains;
  - CSV and Parquet canonical blocks exposed as tables. The runtime would read their blobs out of the store, so
    DuckDB still never touches the filesystem.
