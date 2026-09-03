# ADR-0001: Monorepo Layout And Package Seams

**Decision status:** Accepted, implemented in M0.

## Context

Vitruvio is the engine half of the Boltzmann protocol. `pyboltzmann` ships identity, Merkle DAGs,
snapshots, validation, retention and OCI distribution, and deliberately ships no engine: `Index` (six
kinds), `QueryPlanner`, `CandidateProposer` and `NormalizationPipeline` are Protocols with zero
implementations. Vitruvio implements those, exposes them through a CLI now, and through an MCP server and an
HTTP API later.

Three forces pull the layout apart:

- **Weight.** `sentence-transformers` plus torch is roughly 2.5 GB. `vitruvio config show` must start in
  tens of milliseconds. Those cannot be the same installable unit.
- **Replaceability.** The index engines and the planner are the parts most likely to be rewritten, including
  in another language. A seam that only exists as a naming convention will not survive that.
- **Three interfaces, one behaviour.** A CLI, an MCP server and an API that each re-derive what an operation
  means will drift, and the drift will show up as three different answers to the same question.

## Decision

One uv workspace, eight libraries and one app, sharing a single PEP 420 import namespace `vitruvio`.

| distribution | import | responsibility |
|---|---|---|
| `vitruvio-kernel` | `vitruvio.kernel` | which brain, who am I, under what policy |
| `vitruvio-stats` | `vitruvio.stats` | the statistics vocabulary indices produce and the planner consumes |
| `vitruvio-embeddings` | `vitruvio.embeddings` | text and vision embedders, model tags, caching |
| `vitruvio-indices` | `vitruvio.indices` | the six `Index` implementations, plus text analysis |
| `vitruvio-planner` | `vitruvio.planner` | the cost-based `QueryPlanner` and EXPLAIN |
| `vitruvio-ingest` | `vitruvio.ingest` | normalization pipelines and candidate proposers |
| `vitruvio-runtime` | `vitruvio.runtime` | the service layer every interface shares |
| `vitruvio-bench` | `vitruvio.bench` | synthetic corpora and the recall/latency harness (unpublished) |
| `vitruvio` | `vitruvio.cli` | the CLI |

Dependencies point downhill only: `kernel ← stats ← embeddings ← indices ← planner ← runtime ← cli`, with
`ingest` a sibling of `planner`. Enforced by `.importlinter` in CI, not by convention.

Each seam earns its place for a stated reason:

- **kernel** is separate so that configuration commands do not transitively import `usearch`.
- **stats** is separate to break a cycle: indices produce fragments, the planner consumes them, neither
  should import the other.
- **embeddings** is separate because the vector index needs only the `Embedder` *Protocol*, while torch
  belongs behind an extra.
- **planner** is separate from **indices**, one-directionally. An index that can see the planner starts
  making planning decisions and stops being independently testable.
- **runtime** is separate from **cli**: this is the seam that makes the MCP server and the API thin.
- **bench** is separate because both the index tests and the planner tests need a 5000-block corpus, and
  below a few hundred blocks an exhaustive scan legitimately wins every plan comparison — so a small fixture
  would pass without exercising a single index path.

Two rules are mechanical rather than advisory:

1. **An app may import `vitruvio.runtime` and `vitruvio.kernel`, and may never import `boltzmann`.** If an
   app needs an SDK type, the service layer is missing a method. The one case that nearly became an
   exception — `--actor-kind`, whose values are an SDK enum — was removed instead, by having the CLI accept a
   string and `vitruvio.kernel.parse_actor_kind` coerce it.
2. **One version across the monorepo.** semantic-release bumps all nine `pyproject.toml` files together, and
   `vitruvio.kernel.version` is the single place code reads it from.

## Consequences

- A bare `pip install vitruvio` is small and starts fast. Semantic retrieval with real models is
  `vitruvio[local]`; cross-modal is `vitruvio[vision]`.
- Adding the MCP server means adding `apps/mcp` and nothing else. If it turns out to need more, that is a
  signal the service layer is incomplete, which is information worth having.
- Nine `pyproject.toml` files must agree on tooling. They cannot disagree: every `[tool.*]` section lives at
  the workspace root, and the members carry only their name, version and dependencies.
- PEP 420 has a sharp edge that cost real time to find. Hatchling's `packages = ["src/vitruvio/kernel"]`
  makes an editable install put `src/vitruvio` on `sys.path`, so `import kernel` succeeds and
  `import vitruvio.kernel` fails. Every member must use `only-include` plus `sources = ["src"]`. Guarded by
  `tests/test_namespace.py` and a pre-commit hook, because the failure is a silence rather than an error.
