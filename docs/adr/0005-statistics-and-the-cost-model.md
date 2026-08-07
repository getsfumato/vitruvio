# ADR-0005: Statistics, The Cost Model, And Recall As Part Of The Objective

**Decision status:** Accepted, implemented in M2/M4.

## Context

The SDK ships `boltzmann.query.scan`, a linear scan, and leaves `QueryPlanner` an empty Protocol. Vitruvio exists to
fill that in, and the requirement was a planner that *selects indices by query shape* rather than a router that maps
"natural language" to "vector index". The difference is measurable, and it runs in both directions: a heuristic router
picks the vector index on a 200-block brain where embedding the query alone costs more than reading every block, and
picks a term scan on a 100k-block brain where a bitmap-masked probe would have been two orders of magnitude cheaper.

A cost model without statistics is a heuristic with more code, so statistics came first (M2), the model second (M4).

## Decision

### Cost is in estimated microseconds of wall time

Not in abstract units. The operators span dict probes, HNSW walks, blob reads and a neural forward pass; any synthetic
unit needs conversion factors anyway, and being honest about the unit is what lets `EXPLAIN ANALYZE` **validate** the
model instead of just displaying it. `Calibration` is a frozen dataclass of measured defaults, re-measurable with
`vitruvio calibrate`, and `calibrate --from-samples` refits from `.vitruvio/estimation.jsonl` by least squares — so the
model improves on each brain it runs against rather than staying frozen at whatever the author's laptop measured.

The constant that decides the most is `c_embed_text = 4500 µs`.

### Statistics are computed in the pass that is already happening

Each index emits a `StatsFragment` during its build; nothing iterates twice. `StatsCatalog.assemble` is total and
order-independent — it merges whatever fragments exist and recomputes the module-level one if missing.

**Freshness is two-level, because one level cannot express it.** `build()` receives the resolvable blocks and *not* the
`MerkleRoot`, so the catalog compares both a `root` (any change of composition) and a
`leaf_fingerprint = sha256(sorted resolvable ids)` — which catches the case the root cannot: a redaction that
tombstones bytes without changing the composition. If the SDK ever passes the root to `build()`, this collapses to one
level.

Three statistics are what make this a planner rather than a spreadsheet:

- `reach_mean_by_depth`, **measured** by BFS from a 128-node sample. Costing `GraphExpand` as `d̄^depth` is wrong by
  orders of magnitude, because frontiers overlap.
- `recall_curve`, **measured** at build time by brute-forcing 64 queries against `search(exact=True)`. This is what
  makes estimated recall an empirical quantity.
- `joint`, a 128-cell subject×tag contingency table — free, and it fixes the one correlation that actually shows up.

Selectivity estimation is **damped** (`Π sel_(i)^(1/2^(i-1))` over ascending selectivities) and biased to *over*
estimate cardinality, because underestimating the pool makes the planner choose a `k` too small to hold the answer.
The failure mode that hurts is recall, not latency.

### Recall is in the objective, not hoped for

`cov_g = recall_index_g · pool_adequacy_g`, with `recall_index_g = interp(recall_curve, ef_eff)` and
`ef_eff = max(1, ef · s)` — pushing a highly selective mask into HNSW does **not** preserve recall, because the walk
visits `ef` nodes of which only about `s·ef` survive the filter. That single fact is why `BruteVector` exists: below
`brute_threshold` vectors in mask, an exact scan is recall 1.0 and often cheaper. The model *derives* that switch
rather than hardcoding it.

Plan recall is `1 - Π_g (1 - authority_g · cov_g)` with every `authority_g < 1`, which makes a multi-index plan
strictly better in recall than any single-index plan. The protocol's "no index is authoritative" invariant is thereby
part of the objective function instead of bolted on beside it.

The objective is three layers, and the stratification is how pruning happens without violating that invariant:

1. **A validity rule, never costed and never negotiated:** with two or more scoring generators available and
   `intent ≠ EXACT`, a plan with fewer than two is inadmissible. Single-authority plans are structurally
   inexpressible, so no pruning can reach them. One exemption, and it is not a loophole: a plan whose only generator
   is `SeqScan` is a *no*-authority plan, not a single-authority one — it reads everything. Without that exemption,
   adding a second index made the pure scan inadmissible and broke monotonicity.
2. **A recall floor per intent**, as feasibility rather than as a price. With one caveat found by running it: the
   floor is capped at the best recall achieved by any valid non-`SeqScan` plan, and the capping is *reported* as a
   degradation. An uncapped floor is unreachable in exactly the situation where it matters most — measured at 100k
   blocks, it forced a 5.3 s scan over a 600 µs probe.
3. `J = cost_µs + λ·(1 − recall_hat)·C_miss`, to choose among plans that are already correct.

### Enumeration is exhaustive, not Cascades

The combinatorial explosion that justifies a memo comes from join reordering, and there are no joins here: one "table"
per module and a `Fuse` that is commutative, associative and fixed-cost. The real decisions — which generators, which
`k` and `ef`, pushdown versus post-filter versus brute, depth, per-scope budget — yield at most a few hundred plans per
scope, costable well inside a millisecond.

Exhaustive enumeration buys a property Cascades does not: **adding an index can never worsen the chosen plan**, because
the new space is a strict superset and both are searched completely. That is a property a test can assert, and it is
asserted.

## Consequences

- Verified by running the CLI at two scales, same code: at 3000 blocks `TermScan` (13.4 ms, J=61486) beats `SeqScan`
  (143 ms, J=143089); at 4 blocks `SeqScan` (350 µs) beats `TermScan`. Opposite decisions from measured cardinality is
  the whole claim of this ADR.
- Golden plans snapshot **structure and ranking, not microseconds** — the chosen signature, the considered signatures
  with their `J` range, `est_rows` per node, cost bucketed to one significant figure. A recalibration must not break
  forty goldens; absolute formulas are unit-tested in `cost.py`, which is where numeric precision belongs.
- `Ledger.of` decodes every provenance block, so it is cached per provenance root and reported as `prelude_us` — the
  first uncached run charged 136 ms to a query that did not cause it.
- The plan cache is keyed by query *shape* with per-literal selectivity bucketed in log₂, which is what makes it a
  cache rather than a lie: two literals share a plan exactly when the estimates that chose it still hold.
