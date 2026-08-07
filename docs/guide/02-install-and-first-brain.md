# 2. Install, and a first brain

```bash
uv tool install vitruvio                 # or: pipx install vitruvio
vitruvio --version
```

The base install carries no model. That is intentional: the default text embedder is `hashing:bow`, which needs
nothing, and the extras are opt-in.

| extra | brings | for |
|---|---|---|
| `[local]` | sentence-transformers, torch | real local text embeddings |
| `[vision]` | SigLIP, pypdfium2 | image embeddings, PDF text |
| `[api]` | httpx | API embedders and API proposers |
| `[keyring]` | keyring | registry tokens in the system keychain |
| `[all]` | all of the above | |

## A brain in three commands

```bash
vitruvio brain init ./demo --actor you@example.com
vitruvio source register notes.md
vitruvio search "whatever notes.md is about"
```

`brain init` creates the layout and writes `vitruvio.toml` **beside** it. Commit that file: it records the actor, the
retention policy and the embedder, which is what makes a second clone of the project retrieve comparably rather than
by coincidence.

## How a brain gets selected

In order, and the first one wins:

1. `--brain PATH` — an explicit instruction.
2. `$VITRUVIO_BRAIN` — how a container or a CI job says it without editing files.
3. `[brain].path` in the nearest `vitruvio.toml`, walking up from the working directory.
4. `current` in the XDG state file, written by `vitruvio brain use`.

Layer 3 resolves a relative path **against the file's directory**, never against the working directory: a config that
means a different brain depending on which subdirectory you happened to be in is not a reproducibility artifact.

One extra rule worth knowing: when `--brain` names a brain outside the current tree and the walk-up finds nothing, the
walk restarts *beside the brain*. Without it, the `vitruvio.toml` that `brain init` had just written would be ignored
by the very next command.

## Writes need an actor

```
error: no actor identity is configured, and every write is attributed in provenance
```

This is a refusal, not an inconvenience. An unattributed write is a provenance record that lies, and the whole value
of the provenance module is that it does not.

Reads never need one: inspecting someone else's brain is legitimate and attributes nothing.

## Next

[3. Registering evidence](03-registering-evidence.md)
