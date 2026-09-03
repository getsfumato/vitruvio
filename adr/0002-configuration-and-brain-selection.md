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

### Two questions, in that order: which project, then which brain

Amended in M4. A brain name means nothing until it is known whose vocabulary it belongs to, so selection is
two questions and the second is answered inside the first.

**Which project** — a `vitruvio.toml`:

1. `--config FILE`, verbatim
2. `--project NAME`, looked up in the machine's project registry
3. `$VITRUVIO_CONFIG` / `$VITRUVIO_PROJECT`
4. the nearest `vitruvio.toml`, walking up from the working directory

**Which brain**, within that project:

1. `--brain NAME` (a brain the project declares) or `--brain PATH`
2. `$VITRUVIO_BRAIN`
3. `[brain].path` in the project file
4. the project's only named brain, when it holds exactly one
5. what `vitruvio brain use` last recorded **for this project**

Failing, the error names the layers — and in a project of six brains, names the six. "No brain selected" with
no further detail is the least useful message a tool can emit.

The file layers resolve a relative path **against the configuration file's directory**, never against the
working directory. A project config that means a different brain depending on which subdirectory you ran from
would not be a reproducibility artifact. The walk-up also stops at the first file rather than merging every one
on the way up: layered configuration across directory levels is a feature nobody asked for and a debugging
session everybody remembers.

### A project is addressable by name, from any directory

`$XDG_STATE_HOME/vitruvio/state.toml` holds a `[projects]` table mapping a project's name to its
`vitruvio.toml`. `project init` writes an entry; `project register`, `project list` and `project forget` manage
them by hand.

It is a registry of **names**, holding no configuration of its own — every value is a path to a committed file,
and everything about a project is still read from there. That is what keeps a machine-local convenience from
becoming a second, unversioned place a project can be configured. Losing the registry costs the ability to say
`--project facultad` from an unrelated directory and nothing else.

### There is no machine-wide "current brain" for a project that declares brains

Also M4, and this one is a subtraction. Layer 4 used to be a single `current` pointer in the state file,
consulted by every invocation on the machine. Two things were wrong with it:

- **It cannot describe the actual usage.** Several agents, several projects, several subjects, at the same
  time. One pointer has one value, so two terminals could not disagree — and a `brain use` in one changed what
  the other resolved.
- **It resolved in silence.** A pointer left behind in one project answered for a *different* project whose
  brains were all addressed by name. The result is a write into another subject's brain, with nobody informed,
  and content addressing has no undo for that.

So the saved choice is now keyed by project, and a project that declares brains **raises rather than falling
back** to the machine-wide pointer. The unscoped pointer survives for a brain that belongs to no project at
all, which is the one case it was always right for.

`--project` and `--brain` on the invocation remain the only layers an agent should rely on: they are the ones
that survive being read by somebody else.

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
- Several layers of precedence is more than some tools have. Each one has a distinct user: flags for agents and
  scripts, the environment for containers and CI, the file for reproducibility, the state file for a human in
  a shell. Dropping any of them pushes its user onto a worse path.
- One invocation can state its whole context, so concurrency is free: `vitruvio --project facultad --brain
  analisis-numerico` and `vitruvio --project eticompass --brain metrica-a` are two commands that share no
  mutable state and cannot influence one another. That is what makes several agents on one machine safe.
- The subtraction is a **breaking change** for a workflow that relied on `brain use` in one project answering
  for another. It fails loudly, with the project's brain names in the hint, rather than resolving to something
  unintended — which is the trade this decision is: an error somebody reads, instead of a write nobody sees.
- `vitruvio browse` is the one command that treats an unselected brain as a question rather than an error: it
  opens a picker of projects and their brains. It is interactive by definition, so a list is a better answer
  than five flag names; every other command still refuses, because a non-interactive caller cannot answer.
