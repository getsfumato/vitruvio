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
  - It is a copy of block payloads, so every retention mechanism that removes knowledge (drop, drop-by-producer,
    applied prune, redaction) deletes it. An erasure that left a copy in a derived cache would not be an erasure.
  - A member is unreadable only when the composition says so: tombstoned, or not installed. A read that fails is an
    error that propagates, never a null row. A passing I/O fault would otherwise become a false tombstone, cached
    under a key that cannot change until the composition does.
- **The ledger is joined in when tables are loaded, not stored with them.**
  - Supersession and demotion are recorded in the provenance module, so they can change while another module's
    root does not.
  - Superseded and demoted blocks are hidden by default, as search hides them, and `include_superseded` shows them.
  - Each result reports how many members it hid.
  - Hiding reads the provenance module, so that module is an input to every query that hides anything. Its root is
    reported in `verified_against` even when the query never names it.
  - Without a provenance module nothing can be hidden. The result then lists `provenance` in `not_installed` and
    sets `exact: false`, rather than presenting the count as a count of accessible blocks.
- **The guard (sqlglot).**
  - It accepts one statement, and that statement must be a query.
  - It refuses by construct: DDL and DML, `COPY`, `ATTACH`, `INSTALL`, `PRAGMA`, `SET`, table functions, and
    file-reading functions.
  - It reserves the prefix `__vitruvio_` for every name the engine creates, and refuses it in any identifier a caller
    writes. The rewrite points at internal tables by unqualified names, so a caller's CTE of the same name would be
    resolved first and could forge a score.
  - It refuses sampling, and functions whose value differs from run to run (`random()`, `uuid()`, `now()` and
    their kin). Either would make one signature over one set of roots name two answers.
  - It rewrites table names onto the engine's views and keeps the caller's aliases. A CTE name shadows a table
    only where that CTE is in scope.
  - It adds `ORDER BY ALL` when the query gives no order, so one question over one root has one answer.
  - Its signature is a digest of the canonical query plus the visibility it ran under.
- **The seal (DuckDB).**
  - Tables are loaded before any caller SQL runs.
  - Then external access is switched off and the configuration locked, so no statement can read or write a file,
    load an extension, or turn access back on.
  - The guard is the wall that explains a refusal; the seal is the wall that holds if the guard misses something.
  - DuckDB runs single-threaded over tables inserted in identity order, so order-sensitive aggregates and a
    `LIMIT` inside a subquery also see one order.
  - One time budget covers the whole request: projecting or loading tables, running, fetching and verifying. It
    is enforced by interrupting DuckDB and by checks inside the Python loops. A memory limit applies as well.
- **Honesty in the result.**
  - `verified_against` names the root of every module read.
  - `not_installed` names tables that were empty because their module is absent. Zero from a module that is not
    here is different from zero from one that is.
  - `truncated` reports a row limit that was hit, instead of cutting the result silently.
  - `--verify` proves every block the result names in an `id` column.
  - `exact` is true unless something the answer depends on could not be established: a missing provenance module,
    or a similarity predicate (below). `approximate` lists each cause.
  - No value is rounded. A `DECIMAL` leaves as its exact decimal string. Nonfinite floats leave as `"Infinity"`,
    `"-Infinity"` and `"NaN"`, so strict JSON serialization never fails.
- **Similarity is a threshold over every block, and always approximate.**
  - `about(id, 'text', min_score)` is true for a block whose similarity to the text is at least `min_score`.
    `similarity(id, 'text')` is that score, for ordering or display.
  - The text and the threshold must be literals, with `min_score` in `(0, 1]`. Each distinct text is embedded
    once, and every block is scored exactly before the database is sealed. `VectorIndex.similarities` walks every
    chunk and takes the best one per block, so nothing is cut at a limit, and a count over `about` counts every
    block that clears the bar.
  - The score is the vector index's cosine, not the fused score `search` reports. A threshold chosen by reading
    search results would mean nothing here.
  - The package receives scores through a `Scorer` protocol. The runtime's implementation reads each module's own
    vector index under the planner's usability rule, and opens the RETRIEVE brain only for queries that use
    similarity.
  - A module that cannot be scored is reported, never silently zero: no vector index, a stale one, or an embedder
    that is unavailable or mismatched. If nothing at all can be scored, the query is refused with a pointer to
    `vitruvio index build`.
  - Every answer that uses similarity carries `exact: false` and one `approximate` entry per text, naming its
    thresholds, the model that scored each module (a local fallback is labelled as such), and the modules that went
    unscored.
- **Several brains are one database.**
  - `compound sql` loads every member's tables into a single engine, and DuckDB answers once. A count or join
    across brains is one question over their union, not answers to be merged, so no merge rule is written here.
  - A bare table spans every member, with a leading `brain` column. `brain.table` reads one member. A reference to
    a brain outside the compound is refused.
  - Internally, a member is named by its position, never by its name, because a brain name is caller data.
  - Each member brings its own ledger, table cache and scorer.
  - A block held by two brains is one row per brain. `count(DISTINCT id)` counts it once.
  - The brains consulted are part of the signature.
  - Every key in `verified_against`, `hidden` and `not_installed` is `brain.module`.
  - Under `about()`, only the brains a query reads score it, so `FROM a.semantic` is never decided by brain b's
    model. Among those brains, a block two of them hold takes the higher score, and `approximate` names every
    model that contributed.
  - `hidden` is counted per brain even for a bare table, because a sum across brains could not say which brain
    hid what.
- **Registered data files are tables, read from the store.**
  - A canonical block registered as CSV, TSV or Parquet is readable as `data."<file>"`. It is named by the last
    segment of its registration's origin, by its id, or by a unique id prefix.
  - An ambiguous name is refused. A superseded version is reachable only by id unless superseded blocks are
    included.
  - The engine reads the bytes through the module's store, which verifies them against their digest. It writes
    them to its own temporary file and parses them before the seal. A query can name a dataset, never a path.
  - Only the datasets a query names are parsed. `datasets` lists every one without reading any.
  - `--verify` proves each dataset's block. It keeps a row whose `id` value is not a block identity, such as a
    CSV's own `id` column, and reports it as unproven. Only a value that is a block identity and fails its proof
    drops its row.
  - A query that reads only datasets is subject to the same visibility rule as one that reads module tables:
    without provenance it is `exact: false`.
  - The result names each dataset it read and the block the dataset resolved to. The canonical and provenance
    roots are reported, because the bytes come from one and the name from the other.
- **Packaging.** The distribution sits behind the `sql` extra of `vitruvio-runtime` and the CLI. Without it,
  `vitruvio sql` is a usage error naming the extra.

## Consequences

- Joins across memory types, `HAVING`, CTEs and window functions come for free, because they are DuckDB's.
- Queries do not use the bitmap and B-tree indices. DuckDB scans columnar tables, which on brains of thousands to
  millions of blocks costs milliseconds. The first query after a composition changes pays for projecting that module.
- DuckDB is a binary wheel of 10 to 30 MB. That is why the engine is an extra rather than part of the default install.
- Changing a column is a change to what saved queries mean. It needs a `SQL_PROJECTION_ID` bump and a changelog entry.
- Scoring every block is linear in the vector population per distinct text. That is fine at this scale, and it is
  the price of a threshold that means the same thing on every run.
- A compound holds every member's tables in memory at once. That is bounded by the memory limit, and it is the
  reason a compound is built per request.
- A dataset is parsed on every query that names it; its table is not cached. The file is already local and
  verified, and a cache would be one more copy for retention to purge.
- CSV types are sniffed by DuckDB from the bytes, which is deterministic for the same file. A column whose type a
  query depends on is safest cast explicitly.
