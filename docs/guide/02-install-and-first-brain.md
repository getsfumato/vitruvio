# 2. Install, and a first brain

```bash
curl -fsSL https://vitruvio.sfumato.sh/install.sh | sh
vitruvio --version
```

The installer downloads a release's wheel bundle and hands it to `uv tool install`, bootstrapping `uv` and a
Python if the host has neither. vitruvio is nine pure-Python distributions rather than one binary, and they are
published as release assets rather than to PyPI — so `uv tool install vitruvio` and `pipx install vitruvio` do
not resolve, and the installer is the supported path. `VITRUVIO_VERSION` pins a version, `VITRUVIO_EXTRAS`
selects extras, and `VITRUVIO_BIN_DIR` chooses where the command lands.

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

Two questions, in this order, because a brain name means nothing until it is known whose vocabulary it belongs to.

**Which project** — a `vitruvio.toml`. First one wins:

1. `--config FILE` — that file, verbatim.
2. `--project NAME` — by name, from anywhere. See [13. Projects](13-projects.md).
3. `$VITRUVIO_CONFIG` or `$VITRUVIO_PROJECT` — the same two answers, from the environment.
4. The nearest `vitruvio.toml`, walking up from the working directory.

**Which brain**, inside that project:

1. `--brain NAME` — a brain the project declares — or `--brain PATH`.
2. `$VITRUVIO_BRAIN` — how a container, a CI job, or one agent's session says it without editing files.
3. `[brain].path` in the project file.
4. The project's only brain, when it holds exactly one.
5. What `vitruvio brain use` last recorded **for this project**.

The file layers resolve a relative path **against the file's directory**, never against the working directory: a
config that means a different brain depending on which subdirectory you happened to be in is not a reproducibility
artifact.

There is deliberately **no machine-wide "current brain"** for a project that declares brains. A single pointer shared
by every terminal cannot describe several projects open at once, and it used to resolve in silence — so a project with
brains asks by name instead:

```
error: this project holds 2 brains and none was selected
hint: pass --brain with one of: algebra, analisis-ii
```

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

## Staying current

```bash
vitruvio update --check                  # is there a newer release
vitruvio update                          # install it
```

After an ordinary command, vitruvio prints one line on stderr when a newer release exists. It asks GitHub at
most once a day, caches the answer — including a failed lookup, so being offline costs one timeout rather than
one per command — and gives up after two seconds. It is suppressed under `--json`, under `--quiet`, and when
stderr is not a terminal, so it can never land in output something else is parsing. `VITRUVIO_NO_UPDATE_CHECK=1`
turns it off for good.

The notice never installs anything and never asks a question: a prompt after an unrelated command would hang
the first script that ran unattended. `vitruvio update` is where the decision lives, and it confirms before
replacing anything unless you pass `--yes`.

Updating re-runs the same installer, pinned to the version you were shown, rather than reimplementing it. The
`--reinstall` inside it is why: only `vitruvio` is pinned, so an upgrade that did not force it would find the
eight sibling libraries already satisfied and leave a new CLI on old packages — which then reports the old
version, because `--version` reads the kernel's.

A source checkout is refused, since the installer would replace the environment the command is served from.
Use git there.
