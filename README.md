# vitruvio

The engine for [Boltzmann Brain](https://github.com/gaussia-labs/papers): portable, verifiable, model-agnostic
knowledge.

The protocol says a brain conserves, validates and retrieves knowledge while an interchangeable model interprets it.
[`pyboltzmann`](https://github.com/gaussia-labs/pyboltzmann) implements the protocol and ships **no engine** — its
`Index`, `QueryPlanner`, `CandidateProposer` and `NormalizationPipeline` are Protocols with zero implementations.
Vitruvio fills them in: six index kinds, text and vision embeddings, and a cost-based planner that chooses indices
from the shape of the query and can explain why.

```console
curl -fsSL https://raw.githubusercontent.com/getsfumato/vitruvio/main/install.sh | sh
```

Installs the latest release into `~/.local/bin` with its own isolated environment, taking the wheels from the
GitHub release and fetching a Python if the host has nothing new enough. `VITRUVIO_EXTRAS=all` for local and API
embeddings, PDF pages and LLM proposers; `VITRUVIO_VERSION` to pin; `VITRUVIO_BIN_DIR` to install elsewhere.
Or, once vitruvio is on PyPI:

```console
uv tool install vitruvio             # no torch, no downloads — still embeds, still indexes
uv tool install 'vitruvio[all]'      # local and API embeddings, PDF pages, LLM proposers
```

```console
vitruvio brain init ./brain --actor you@example.com
vitruvio ingest run notes.md --dry-run
vitruvio index build
vitruvio search "descomponer una función periódica en senos"
vitruvio browse                      # read the brain: modules, blocks, and the PDFs and images inside them
```

Every command that returns data takes `--json` and emits exactly one envelope on stdout. Notes and warnings go to
stderr, so `vitruvio search q --json | jq` works unconditionally. (`browse` is the one exception, and it says so:
an interface for a person has no envelope, so it refuses the flag rather than printing something a caller cannot
read.) **The brain returns evidence, never prose** — there is no
`answer` field and there will not be one.

## Documentation

| | |
|---|---|
| [Guide](docs/guide/README.md) | fifteen chapters, in order. Start at [what a brain is](docs/guide/01-what-a-brain-is.md) |
| [Decisions](docs/adr/README.md) | twelve ADRs: what was chosen, what it cost, what was rejected |
| [Architecture](docs/architecture.md) | the workspace, the layering, and why an app may not import the SDK |
| [Contributing](docs/contributing.md) | the dev loop and the gate every change passes |
| [Releasing](RELEASING.md) | how a commit on `main` becomes a version, a tag and an installable release |
| [`skills/`](skills/README.md) | the agent-facing contracts, installable with `vitruvio skills install` |

## License

MIT.
