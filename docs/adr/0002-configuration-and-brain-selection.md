# ADR-0002: Configuration, Brain Selection, And Where Vitruvio Writes

**Decision status:** Accepted, implemented in M0.

## Context

A brain on disk *is* an OCI image layout: `oci-layout`, `index.json`, `blobs/sha256/`, plus the SDK's own
sidecar `boltzmann/` holding `head.json` and `tombstones.json`. Any conforming client can read it, and
`oras cp` can move it. Vitruvio needs somewhere to put a great deal of derived state — six indices,
statistics, an embedding cache, cost calibration — and needs to know which brain a command refers to, who to
attribute a write to, and how to reach a registry.

Four constraints:

- Indices are **derived views**, per the protocol. Storing them where they look like content would contradict
  the architecture the runtime exists to implement.
- The same query against the same brain should give comparable answers on two machines, so the choice of
  embedding model and index set has to be recorded somewhere versionable.
- Credentials must not end up in that versioned file.
- Every write is attributed in provenance, so an actor is not optional.

## Decision

### One sibling file, one sibling directory

`vitruvio.toml` sits *beside* the brain and is committed. `<brain>/.vitruvio/` holds everything derived:
`stats/`, `indices/` and `embeddings/`. Nothing vitruvio writes goes
inside `blobs/` or inside the SDK's `boltzmann/`.

Deleting `.vitruvio/` costs time and never knowledge. Writing into another component's sidecar works until
the day it does not.

### Brain selection, in four layers

1. `--brain PATH`
2. `$VITRUVIO_BRAIN`
3. `[brain].path` in the nearest `vitruvio.toml`, walking up from the working directory
4. `current` in `$XDG_STATE_HOME/vitruvio/state.toml`, written by `vitruvio brain use`

Failing, the error names all four. "No brain selected" with no further detail is the least useful message a
tool can emit.

Layer 3 resolves a relative path **against the configuration file's directory**, never against the working
directory. A project config that means a different brain depending on which subdirectory you ran from would
not be a reproducibility artifact. The walk-up also stops at the first file rather than merging every one on
the way up: layered configuration across directory levels is a feature nobody asked for and a debugging
session everybody remembers.

### Secrets have no schema

There is no field for a token or an API key anywhere in the configuration schema. They are read from the
environment — `VITRUVIO_*` first, then the bare conventional name (`OPENAI_API_KEY`, `DOCKER_TOKEN`) so an
existing shell or CI job just works. Resolved secrets are wrapped in `Secret`, which renders as `<redacted>`
under `str`, `repr` and `__format__`; obtaining the value takes an explicit `.reveal()`, which is one grep in
review. The registry credential *store* (a keyring, or a `0600` file) is `vitruvio.runtime`'s, because it
needs to know about hosts.

### An unattributed write is refused

`ResolvedConfig.actor()` raises when no actor id resolves. The alternative — inventing one, or writing
`unknown` — produces a provenance record that lies about its own origin, which is worse than a failed
command. `kind` defaults to `human`; an agent driving the CLI is expected to pass `--actor-kind agent`, and
the shipped skills say so.

### Comments are lost by `config set`

`vitruvio config set` round-trips the document through a plain dictionary, so comments do not survive. It
warns, and hand-editing remains the recommended path for anything structural. It exists for the two writes
that must not require an editor: setting a value from a script, and the model-tag write-back after a vector
index is first built. It also validates before writing: a rejected `config set` beats a file the next command
refuses to parse.

## Consequences

- `oras cp` still moves a brain, and another client still reads it.
- Two clones of a project retrieve comparably, because the embedder and index set are in a committed file.
- A committed `vitruvio.toml` can never leak a credential.
- `.vitruvio/` and `brain/` are in `.gitignore`. Committing derived state would also make a machine-specific
  artifact look shared: embedding vectors and HNSW graphs are not reproducible byte-for-byte across machines.
- Four layers of precedence is more than some tools have. Each one has a distinct user: flags for agents and
  scripts, the environment for containers and CI, the file for reproducibility, the state file for a human in
  a shell. Dropping any of them pushes its user onto a worse path.
