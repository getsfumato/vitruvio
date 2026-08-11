# Architecture

One uv workspace, eight libraries and one app, sharing the PEP 420 namespace `vitruvio`. Dependencies point
downhill only, enforced by `import-linter` in CI rather than by convention.

```
packages/kernel      which brain, who am I, under what policy
packages/stats       the statistics vocabulary indices produce and the planner consumes
packages/embeddings  text and vision embedders, model tags, caching   [torch behind an extra]
packages/indices     the six Index implementations, plus text analysis
packages/planner     the cost-based QueryPlanner and EXPLAIN
packages/ingest      normalization pipelines, candidate proposers, declared sources
packages/runtime     the service layer every interface shares
packages/bench       synthetic corpora and the recall/latency harness  [unpublished]
apps/cli             the CLI, and the TUI it opens                        [dist: vitruvio]
```

## The two contracts that carry weight

**The kernel is the floor.** It imports pydantic and the SDK's value types and nothing heavier. That is
load-bearing: `vitruvio config show` and `vitruvio brain use` must start in tens of milliseconds, and if
configuration lived beside the runtime then importing it would drag in `usearch` and the index registry.

**An app may import `vitruvio.runtime` and `vitruvio.kernel`, and may never import `boltzmann`.** If an app needs an
SDK type, the service layer is missing a method, and adding it there is what makes the same capability available to
all three interfaces instead of one. That is what will keep the future MCP server and HTTP API thin rather than a
third and fourth implementation of the same behaviour.

The one exception that nearly existed — `--actor-kind`, whose values are an SDK enum — was removed by having the CLI
take a string and the kernel coerce it. An exception list in `.importlinter` is a boundary that has started leaking.

## Why a cost model rather than a heuristic router

Embedding one query costs about 4.5 ms locally. On a brain of a couple of hundred blocks that is more expensive than
reading the entire module, so "natural language means use the vector index" is simply wrong there — and it runs in
both directions: the same heuristic picks a term scan on a 100k-block brain where a bitmap-masked probe would have
been two orders of magnitude cheaper.

Only a cost model notices. Verified by running the CLI at two scales with the same code: at 3000 blocks `TermScan`
(13.4 ms) beats `SeqScan` (143 ms); at 4 blocks the reverse. Opposite decisions from measured cardinality is the
whole claim.

The reasoning is [ADR-0005](adr/0005-statistics-and-the-cost-model.md); the reader's view is
[chapter 6](guide/06-the-planner.md).

## Where things are written down

- [`docs/guide/`](guide/README.md) — how to use it, in order.
- [`docs/adr/`](adr/README.md) — why it is like this, one file per decision, never renumbered.
- [`skills/`](../skills/README.md) — how an *agent* drives it. Authored once at the repository root and reached
  through a symlink from inside the package, so `vitruvio skills install` ships exactly what is under version
  control.
- `.claude/skills/` — skills for agents working *on* this repository, which is a different audience.
