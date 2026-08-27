# vitruvio

The runtime for a **[Boltzmann brain](https://github.com/gaussia-labs/papers)**: portable, verifiable,
model-agnostic knowledge that any model can read and none of them owns.

A brain is a store of content-addressed, typed blocks — the evidence you registered, what happened and when, the
facts and procedures derived from it, and the provenance tying each derivation to its source — arranged as a
Merkle DAG per memory module, so that every version can be verified, published, installed and reconciled with a
copy somebody else advanced. The brain conserves, validates and retrieves knowledge; an interchangeable model
interprets it. **The brain returns evidence, never prose** — there is no `answer` field, and there will not be one.

[`pyboltzmann`](https://github.com/gaussia-labs/pyboltzmann) implements the protocol and ships no engine. Vitruvio
is the engine: six index kinds, text and vision embeddings, a cost-based planner that chooses indices from the
shape of the query and can explain why, an ingestion gate that validates what a model proposes before it is
committed, and distribution over any OCI registry.

## Install

```console
curl -fsSL https://vitruvio.sfumato.sh/install.sh | sh
vitruvio --version
```

The installer puts the latest release into `~/.local/bin` in its own isolated environment, fetching `uv` and a
Python if the host has nothing new enough. `VITRUVIO_VERSION` pins a version, `VITRUVIO_EXTRAS=all` adds local and
API embeddings, PDF pages and LLM proposers, `VITRUVIO_BIN_DIR` installs elsewhere. vitruvio is not on PyPI yet, so
the installer is the supported path.

## Skills, for agents

The knowledge of how to drive a brain ships as skills, so an agent learns the contract rather than guessing at it:

```console
npx skills add getsfumato/vitruvio
```

That installs them into your agent's skills directory. The CLI carries the same set and can install it itself —
`vitruvio skills install` — which is the one to use when the skills must match the exact binary they drive.
[`skills/`](skills/README.md) lists what each one covers.

## A first brain

```console
vitruvio brain init ./brain --actor you@example.com
vitruvio source register notes.md
vitruvio index build
vitruvio search "what notes.md is about"
vitruvio browse                      # read the brain: modules, blocks, and the PDFs and images inside them
```

Every command that returns data takes `--json` and emits exactly one envelope on stdout; notes and warnings go to
stderr, so `vitruvio search q --json | jq` works unconditionally. (`browse` is the one exception: an interface for a
person has no envelope, so it refuses the flag rather than printing something a caller cannot read.)

## Documentation

| | |
|---|---|
| [Guide](docs/guide/README.md) | in order, from [what a brain is](docs/guide/01-what-a-brain-is.md) through [install](docs/guide/02-install-and-first-brain.md), [searching](docs/guide/05-searching.md), [ingest](docs/guide/08-ingest.md), [publishing](docs/guide/10-publishing.md) and [reconciling](docs/guide/16-reconciling.md) |
| [Decisions](docs/adr/README.md) | one ADR per decision: what was chosen, what it cost, what was rejected |
| [Architecture](docs/architecture.md) | the workspace, the layering, and why an app may not import the SDK |
| [Skills](skills/README.md) | the agent-facing contracts, and the one place they are authored |
| [Contributing](docs/contributing.md) | the dev loop and the gate every change passes |
| [Releasing](RELEASING.md) | how a commit on `main` becomes a version, a tag and an installable release |
| [The paper](https://github.com/gaussia-labs/papers) | the Boltzmann Brain protocol vitruvio runs |

## License

MIT.
