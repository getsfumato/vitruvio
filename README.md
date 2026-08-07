# vitruvio

A runtime for [Boltzmann Brain](https://github.com/gaussia-labs/papers): portable, verifiable,
model-agnostic knowledge.

The [Boltzmann protocol](https://github.com/gaussia-labs/pyboltzmann) says that a brain conserves, validates
and retrieves knowledge, while an external and interchangeable model interprets it. Knowledge lives in typed,
content-addressed blocks across five memory modules — canonical, episodic, semantic, procedural,
provenance — each pinned by a Merkle root and distributed as an OCI artifact.

`pyboltzmann` implements that protocol and deliberately ships **no engine**. `Index` (six kinds),
`QueryPlanner`, `CandidateProposer` and `NormalizationPipeline` are Protocols with zero implementations, and
the SDK's own search is a linear scan that exists as a correctness oracle rather than as a product.

**Vitruvio is the engine.** Six index kinds, text and vision embeddings, and a cost-based query planner that
chooses indices from the shape of the query and can explain why.

> Status: early. M0 (workspace, kernel, CLI skeleton) is in place; the index engines and the planner are the
> work in progress. See `docs/adr/` for the decisions already made.

## Install

```console
pip install vitruvio                 # light: no torch, no model downloads
pip install 'vitruvio[local]'        # local text embeddings (sentence-transformers)
pip install 'vitruvio[vision]'       # + image and PDF-page embeddings (SigLIP)
pip install 'vitruvio[all]'          # everything, including API providers and LLM proposers
```

A bare install still embeds and still builds a vector index — with feature hashing, tagged `hashing/bow` so
that nobody mistakes the result for semantics. Every code path is exercisable without a 2.5 GB download.

## Use

```console
vitruvio brain init ./brain --actor you@example.com
vitruvio source register paper.pdf --media-type application/pdf --normalize-with pdf-text
vitruvio ingest run notes.md --dry-run      # register, propose, validate — commit nothing
vitruvio index build
vitruvio search "descomponer una función periódica en senos"
vitruvio query explain "..." --analyze      # the chosen plan, the alternatives, estimates vs actuals
vitruvio retain plan-drop <BLOCK> -t semantic   # always before a drop: the cascade is not local
vitruvio dist push docker.io/<namespace>/my-brain --tag v1
vitruvio skills install                     # so an agent knows how to drive all of the above
```

Eleven command groups: `brain`, `source`, `task`, `ingest`, `index`, `query`, `retain`, `dist`, `registry`,
`inspect`, `config` — plus `skills`, `completion` and `search` as a top-level alias. `vitruvio skills list` and
[`docs/guide/`](docs/guide/README.md) are the two places to start.

Every command takes `--json` and emits exactly one envelope on stdout, with a top level that does not change
between commands:

```json
{ "vitruvio": "0.1.0", "command": "query.search", "ok": true, "data": {}, "warnings": [], "error": null }
```

Notes, warnings and progress go to stderr, so `vitruvio search q --json | jq` works unconditionally. The
brain returns evidence and never prose: rendering matches into an answer is the caller's job, which is why
`vitruvio skills install` ships skills that tell an agent exactly that.

## Architecture

One uv workspace, eight libraries and one app, sharing the PEP 420 namespace `vitruvio`. Dependencies point
downhill only, enforced by import-linter in CI.

```
packages/kernel      which brain, who am I, under what policy
packages/stats       the statistics vocabulary indices produce and the planner consumes
packages/embeddings  text and vision embedders, model tags, caching   [torch behind an extra]
packages/indices     the six Index implementations, plus text analysis
packages/planner     the cost-based QueryPlanner and EXPLAIN
packages/ingest      normalization pipelines and candidate proposers
packages/runtime     the service layer every interface shares
packages/bench       synthetic corpora and the recall/latency harness  [unpublished]
apps/cli             the CLI                                          [dist: vitruvio]
```

An app may import `vitruvio.runtime` and `vitruvio.kernel`, and may never import `boltzmann`. That is what
will make the MCP server and the HTTP API thin rather than a third and fourth implementation of the same
behaviour.

Why a cost model rather than a heuristic router: embedding one query costs about 4.5 ms locally, so on a brain
of a couple of hundred blocks it is more expensive than reading the entire module. "Natural language means
use the vector index" is simply wrong there, and only a cost model notices.

## Develop

```console
uv sync --all-packages          # the default dev environment: no torch, fast
uv run pytest -m "not slow"
uv run ruff check . && uv run ruff format .
uv run mypy packages apps
uv run lint-imports             # architectural boundaries
uv run cz commit
```

- `docs/adr/` — architecture decision records.
- `docs/guide/` — the user guide.
- `.claude/skills/` — skills for agents working on this repository.

## Related

- [The paper](https://github.com/gaussia-labs/papers) — Boltzmann Brain: a versioned, distributable and
  model-agnostic knowledge architecture.
- [pyboltzmann](https://github.com/gaussia-labs/pyboltzmann) — the protocol SDK vitruvio implements against.

## License

MIT.
