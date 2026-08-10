# vitruvio

The engine for [Boltzmann Brain](https://github.com/gaussia-labs/papers): portable, verifiable, model-agnostic
knowledge.

The protocol says a brain conserves, validates and retrieves knowledge while an interchangeable model interprets it.
[`pyboltzmann`](https://github.com/gaussia-labs/pyboltzmann) implements the protocol and ships **no engine** — its
`Index`, `QueryPlanner`, `CandidateProposer` and `NormalizationPipeline` are Protocols with zero implementations.
Vitruvio fills them in: six index kinds, text and vision embeddings, and a cost-based planner that chooses indices
from the shape of the query and can explain why.

> Status: early. The CLI is complete and tested; nothing has been released to PyPI yet.

```console
pip install vitruvio                 # no torch, no downloads — still embeds, still indexes
pip install 'vitruvio[all]'          # local and API embeddings, PDF pages, LLM proposers
```

```console
vitruvio brain init ./brain --actor you@example.com
vitruvio ingest run notes.md --dry-run
vitruvio index build
vitruvio search "descomponer una función periódica en senos"
```

Every command takes `--json` and emits exactly one envelope on stdout. Notes and warnings go to stderr, so
`vitruvio search q --json | jq` works unconditionally. **The brain returns evidence, never prose** — there is no
`answer` field and there will not be one.

## Documentation

| | |
|---|---|
| [Guide](docs/guide/README.md) | fourteen chapters, in order. Start at [what a brain is](docs/guide/01-what-a-brain-is.md) |
| [Decisions](docs/adr/README.md) | eleven ADRs: what was chosen, what it cost, what was rejected |
| [Architecture](docs/architecture.md) | the workspace, the layering, and why an app may not import the SDK |
| [Contributing](docs/contributing.md) | the dev loop and the gate every change passes |
| [`skills/`](skills/README.md) | the agent-facing contracts, installable with `vitruvio skills install` |

## License

MIT.
