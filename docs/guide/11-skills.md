# 11. Skills: making the CLI drivable by an agent

```bash
vitruvio skills list
vitruvio skills install                      # into .claude/skills/
vitruvio skills install --into path --force
vitruvio completion zsh > "${fpath[1]}/_vitruvio"
```

The paper treats a **skill** as one of a brain's access contracts, alongside the CLI. `skills install` is what makes
that real: any repository holding a brain obtains the skills without cloning vitruvio, so the knowledge of *how to
drive this* travels with the brain.

Nine skills ship, and they are layered rather than exhaustive:

| skill | covers |
|---|---|
| `vitruvio` | the entry point: what a brain is, the envelope, the exit codes, the five modules |
| `vitruvio-cli` | the command surface: every group, every command, the flag that decides the outcome |
| `vitruvio-query` | searching, and reading a bundle without over-claiming |
| `vitruvio-ingest` | the propose → validate → commit loop. The highest-value one |
| `vitruvio-retention` | the five removal mechanisms and the discipline each needs |
| `vitruvio-dist` | publishing and installing |
| `vitruvio-reconcile` | joining two histories: merge, rebase or squash, and the attribution each costs |
| `vitruvio-sync` | telling behind, ahead and diverged apart, and getting local and remote back in step |
| `vitruvio-compound` | composing several brains of one project into one query, and reading what they agree on |

The entry skill carries four references: `json-envelope.md`, `exit-codes.md`, `evidence-bundle.md`, and
`cli-reference.md`.

## The reference is generated

`cli-reference.md` is produced from the cyclopts command declarations — the same declaration that parses the arguments
— by `python -m vitruvio.cli.reference --write`, and CI fails if the committed copy is out of date.

A stale reference is worse than no reference: an agent that trusts a flag which no longer exists spends its next turn
recovering from a usage error, and nothing in the output tells it the documentation was wrong.

## Authored at the root, shipped in the wheel

The files live at [`skills/`](../../skills/README.md) in the repository root, and `vitruvio/cli/skills` inside the
package is a symlink to them. One copy under version control, two addresses.

Both halves are needed. Shipping them inside the wheel is what ties a skill to the version of the CLI it documents —
a skill installed from a different release than the binary it drives is precisely the failure this arrangement
prevents. Keeping the authored copy at the root is what lets a tool install skills without pip, and what makes them
reviewable in a diff rather than buried under `apps/cli/src/`.

`vitruvio skills list` reports which of the two it is reading, and prefers the packaged copy: in an installed wheel
it is the only one that exists, and preferring the working copy would make the command depend on which directory you
were standing in.

An existing skill directory is left alone unless `--force`, because a consumer may have edited one and silently
overwriting local edits is not something a copy command should do.

## The four habits the skills teach

1. Always `--json`, and branch on `ok` then `error.code`.
2. Read `warnings` **even when `ok` is true** — a degraded answer that looks clean is the failure the design is arranged
   to prevent.
3. The brain returns evidence; you write the prose. There is no `answer` field.
4. Never invent a `block_id`.

## Completion is static

```bash
vitruvio completion bash | sudo tee /usr/local/etc/bash_completion.d/vitruvio
vitruvio completion fish > ~/.config/fish/completions/vitruvio.fish
```

The scripts know the command names and nothing about any brain's contents. A completion hook that called back into
`vitruvio` would open a brain to answer, and with a vector index registered that means constructing an embedder.
Pressing Tab should not load a model.

## Next

[12. Benchmarking and calibration](12-benchmarking.md)
