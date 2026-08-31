---
name: vitruvio-cli
description: The vitruvio command surface — every group, what each command does, and which flag decides the outcome. Use when you need to find the right vitruvio command, when a command failed and the flag may be wrong, when scripting or automating vitruvio, or when asked what vitruvio can do.
allowed-tools: Bash(vitruvio:*), Read
---

# The vitruvio command surface

Eighteen groups, one hundred and ten commands. This skill is the map: which group owns a task, which command inside it, and
the one flag per command that changes the answer rather than the formatting.

It deliberately does **not** teach judgement. How to read a search result without over-claiming is `vitruvio-query`;
which of five removal mechanisms you actually want is `vitruvio-retention`. This is the surface, not the semantics.

## Before anything else

```bash
vitruvio --help                 # the groups
vitruvio <group> --help         # the commands in one
vitruvio <group> <cmd> --help   # every flag, with the reasoning
```

`--help` is authoritative and this file is not: the reference below is generated from the same declarations that
parse the arguments, but `--help` is the declaration itself. When they disagree, `--help` is right and this file is
stale — say so rather than working around it.

## The global options

They belong to the **meta app**, so they go *before* the command, never after:

```bash
vitruvio --json --brain algebra query search "..."     # correct
vitruvio query search "..." --json                     # also accepted, but --brain would not be
```

| option | what it does |
|---|---|
| `--json` | one envelope on stdout, and nothing else. Implies `--quiet`. **Always pass it.** |
| `--brain NAME-OR-PATH` | which brain. A project's brain *name* is tried before a path |
| `--project NAME` | which project, by name, from any directory. **Pass it with `--brain`** |
| `--config FILE` | use this `vitruvio.toml` verbatim, instead of discovering one |
| `--actor ID` | who to attribute writes to |
| `--actor-kind` | `human`, `agent`, `service`, `pipeline`. **Set `agent` when a model drives** |
| `--assisted-by ID` | assisting actor for this invocation; repeatable, recorded as an agent in provenance v2 |
| `--quiet` / `-q` | suppress notes on stderr |
| `--no-color` | plain output |
| `-v`, `-vv` | more detail on stderr |

## State the whole context on every invocation

```bash
vitruvio --json --project facultad --brain analisis-numerico query search "..."
```

`--project` and `--brain` together identify a brain completely. Nothing about that command depends on the working
directory, and nothing another terminal does can change what it means — so several agents can drive several
projects at once, which is the normal way vitruvio is used.

Do **not** rely on a saved selection. `vitruvio brain use` records a default for one project, for a human in a
shell; it is the weakest layer, and a project holding several brains refuses rather than guessing:

```
error: this project holds 2 brains and none was selected
hint: pass --brain with one of: metrica-a, metrica-b
```

That refusal is the correct response to an under-specified command — fix the command, do not run `brain use` to
make it pass, because that changes state other sessions read.

`vitruvio project list` is how to discover what `--project` accepts. A project the CLI has never `init`ed on this
machine — a clone — needs `vitruvio project register` once, from its directory.

`$VITRUVIO_PROJECT` and `$VITRUVIO_BRAIN` are the same two answers for a whole session, if exporting once is
better than passing flags every time.

## The groups

### `brain` — the brain itself
| command | for |
|---|---|
| `brain init PATH` | create a layout. Writes a `vitruvio.toml` beside it |
| `brain state` | **run this first in a session**: which modules, which version, where it came from |
| `brain use NAME-OR-PATH` | a default for **this project only**. For a shell, not for you |
| `brain list` | this project's brains, then every layout this machine has seen |
| `brain verify` | every block against every module root |
| `brain info` | per-module shape: roots, counts, registered indices |
| `brain history` | the snapshot chain, newest first |
| `brain migrate --to PATH` | temporary one-way recreation of a legacy brain. Dry-run first; destination must not exist |

### `source` — evidence in, by hand or declared
| command | for |
|---|---|
| `source register FILE` | canonical evidence. `--media-type`, `--normalize-with`, `--license`, `--origin` |
| `source replace FILE --supersedes ID` | a newer edition, plus the supersession edge |
| `source put FILE` | store bytes addressably **without** a canonical block |
| `source pull [NAME]` | acquire from the selected brain's declaration. `--option k=v` overrides one named pull; `--all` stays within that brain |
| `source status` | what the selected brain declares, and whether each can be used right now |
| `source kinds` | which acquisition kinds are installed, and where each came from |
| `source add NAME --kind K` | declare one under the selected brain. `--path`, `--option k=v`, `--normalize-with` |
| `source remove NAME` | undeclare it. Registered blocks are untouched |
| `source scaffold KIND` | write a starter plugin for a kind vitruvio does not ship |

### `task` — the propose → validate → commit loop
| command | for |
|---|---|
| `task define BLOCK` | what is being asked, over which block. `--allowed semantic` (repeatable) |
| `task schema --task FILE` | the exact shape candidates must satisfy |
| `task validate FILE --task FILE` | the gate's verdict per candidate, committing nothing |
| `task commit FILE --task FILE` | commit. Refused entirely if anything was rejected fixably |
| `task rederive BLOCK` | a better model revisits a derived block |

### `ingest` — the same loop in one call
| command | for |
|---|---|
| `ingest run FILE` | register, propose, validate, commit. `--dry-run` **first**, always |
| `ingest pipelines` | normalization pipelines, and which are unavailable behind an extra |

### `index` — derived, rebuildable state
| command | for |
|---|---|
| `index build` | build or rebuild. Needed after a `PROJECTION_ID` bump or a model change |
| `index list` | every registered index, and whether it is usable |
| `index stats` | what the planner reads: cardinality, selectivity, recall curves |
| `index verify` | each index against the composition it claims to describe |
| `index gc` | drop index files no registration names. `--apply` to act |

### `query` — retrieval
| command | for |
|---|---|
| `query search TEXT` | the bundle. `--memory-type`, `--subject`, `--tag`, `--class`, `--since`, `--limit`, `--mode` |
| `query explain TEXT` | the chosen plan, the rejected ones, and why. `--class` filters canonicals; `--analyze` adds actuals |
| `query resolve BLOCK` | one block, in full |
| `query prove BLOCK` | a Merkle inclusion proof. `--memory-type` |

`vitruvio search TEXT` is a top-level alias for `query search`.

### `catalog` — canonical metadata
| command | for |
|---|---|
| `catalog show` | schemes, classes, hierarchy and effective source membership |
| `catalog apply FILE` | atomically validate and apply a `vitruvio.catalog/v1` TOML/JSON manifest; dry-run first |
| `catalog scheme NAME` | declare one scheme; `--exclusive` permits one direct placement per source |
| `catalog class SCHEME LABEL` | declare a class; repeat `--broader scheme/label` for parents |
| `catalog place SOURCE --class SCHEME/LABEL` | classify a canonical block; `--class` is repeatable |
| `catalog browse --class SCHEME/LABEL` | sources in the class or descendants; repeated classes intersect |
| `catalog path --scheme SCHEME` | a virtual navigation path over an ordered set of schemes |

### `auth` — SSH authenticity and governance
| command | for |
|---|---|
| `auth keys` | Ed25519 public keys currently offered by `ssh-agent` |
| `auth status` | integrity and authenticity as separate verdicts |
| `auth sign KEY` | explicitly sign a snapshot through the agent; private keys never enter Vitruvio |
| `auth pin` | pin the current or out-of-band trust-root digest in consumer state |
| `auth attribution` | compare provenance actors with subjects vouched by accepted signatures |
| `auth plan-rotate ROOT` | serialize the exact new-root document a distributed quorum will sign |
| `auth countersign PLAN KEY` | countersign those exact bytes and write a detached record |
| `auth rotate` | commit a local or quorum-approved trust-root revision |
| `auth revoke KEY` | retire a key prospectively or mark it compromised from a snapshot |

### `compound` — the same question, several brains of one project
| command | for |
|---|---|
| `compound search TEXT` | one query across two or more of the project's brains. `--brains a --brains b` (or `--brains a,b`), or `--all`; `--fuse` merges by rank instead of grouping per brain |
| `compound explain TEXT` | one plan per brain, side by side. Same selection flags |

Brains are named, never given as paths — a compound composes *this* project's brains only. `vitruvio-compound` is the
skill that walks a user through choosing them.

### `retain` — removal, five mechanisms
| command | for |
|---|---|
| `retain plan-drop BLOCK` | **always before a drop**: the cascade is not local |
| `retain drop BLOCK` | exclude from the composition. `--memory-type`, `--reason` |
| `retain drop-producer NAME` | everything one producer derived. `--producer-version` |
| `retain supersede BLOCK --supersedes ID` | precedence, not removal |
| `retain demote BLOCK` | lower its standing, keep it a member |
| `retain redact BLOCK` | **destroys bytes.** Needs a policy that permits it, and a human |
| `retain prune` | drop retained roots past the policy's limit. `--apply` |
| `retain policy` | what this brain permits |

### `dist` — publication
| command | for |
|---|---|
| `dist pack` | build the artifact locally. **Read the warnings** |
| `dist push [REF]` | publish. `--tag`, `--all`, `--module`, `--local`, never `--force` |
| `dist plan-pull [REF]` | what a pull would transfer **and what it would discard**; `--ignore-vector-indices` plans without derived vector layers |
| `dist pull [REF]` | install. Adopts the published composition; read `discarded`; ancestor heads require explicit `--allow-rollback` |
| `dist fetch [REF]` | bring another history **without adopting it**, and reconcile it when the plan is clean. The answer to exit 8 |
| `dist tags [REF]` | what is published |

### `reconcile` — join a history somebody else advanced
| command | for |
|---|---|
| `reconcile plan THEIRS` | what joining would produce, every incoming block already judged, and what each strategy costs |
| `reconcile merge THEIRS --reason R` | both histories as parents. The only one their snapshots survive |
| `reconcile rebase THEIRS --reason R` | replay theirs onto yours. New identities, so **before publication only** |
| `reconcile squash THEIRS --reason R` | collapse their versions into one. Provenance is still kept |
| `reconcile status` | what is open, what has been decided |
| `reconcile resolve [BLOCK]` | `--admit`, `--reject`, `--prefer ID`. No block opens an interactive workspace |
| `reconcile accept-removals` | agree that work of yours may leave. One answer, not one per block |
| `reconcile continue` | conclude it |
| `reconcile abort` | abandon it. Nothing was written |
| `reconcile tree [THEIRS]` | where the two parted, and what each added |

**The three strategies land the same blocks.** A snapshot states a whole composition rather than a patch, so there
is nothing to replay sequentially. What differs is the lineage recorded, and therefore who stays on record as the
author of the incoming work — a merge keeps their snapshots, a rebase and a squash do not. So **never pick one for
the user.** Report what `plan` says each would cost and let them choose, or use what the brain declares.

### `update` — the tool itself
| command | for |
|---|---|
| `vitruvio update --check` | is there a newer release. Read `available`; exits 0 either way |
| `vitruvio update` | install it. Re-runs the official installer, pinned to the version reported |

**Never run a bare `vitruvio update` on the user's behalf.** It replaces the installed vitruvio, so it asks
first, and `--yes` exists for a script whose operator already decided — not for an agent deciding for them.
`--check` is the one to reach for, and it is safe: it installs nothing.

vitruvio also prints a one-line notice on stderr after an ordinary command when a newer release exists. It is
suppressed under `--json`, under `--quiet`, and when stderr is not a terminal, so it can never appear in output
you are parsing. `VITRUVIO_NO_UPDATE_CHECK=1` turns it off entirely.

### `registry` — credentials
| command | for |
|---|---|
| `registry login HOST` | `--username`, `--token-stdin`, `--from-docker`. Never a token in argv |
| `registry logout HOST` | forget it |
| `registry whoami` | which credential would be used, and where it came from |
| `registry list` | every host with a stored credential |
| `registry check [REF]` | reachability, write scope, and media-type support, against the real registry |

### `project` — several brains under one config
| command | for |
|---|---|
| `project init NAME` | create one, and register the name. `--namespace docker.io/you` |
| `project add NAME` | add a brain. `--path`, `--reference`, `--no-publish`, `--no-create` |
| `project remove NAME` | unregister a brain. The layout on disk is left alone |
| `project show` | every brain, where it lives, where it publishes |
| `project list` | **every project `--project` accepts**, and the brains in each |
| `project register [NAME]` | make the project here addressable by `--project`. For a clone |
| `project forget NAME` | drop the name. Touches no files |

### `inspect` — read-only questions
| command | for |
|---|---|
| `inspect doctor` | the whole install: config, indices, embedders, what is missing |
| `inspect module KIND` | one module's shape and a sample of its blocks |
| `inspect blocks KIND` | what the blocks *say*, one line each. `--contains`, `--limit`, `--offset` |
| `inspect block ID` | one block without opening a query path |
| `inspect content DIGEST` | the bytes a block names. `--open` hands them to the desktop's viewer, `--out FILE` exports them |
| `inspect links ID` | the provenance records naming a block: where it came from, what was done to it |
| `inspect roots` | every module root and the snapshot that pins them |
| `inspect resolvability` | readable vs **tombstoned** vs simply absent — three different things |
| `inspect prove BLOCK` | an inclusion proof |

`inspect blocks` is **not** retrieval. It lists a module in its own order and `--contains` filters rows that were
already read: no index is consulted and nothing is ranked. When relevance is what you want, that is `search`.

### `browse` — the interactive interface
| command | for |
|---|---|
| `browse` | open the brain in a terminal UI. `--memory-type` to land on a module |

For a person, not for you: it needs a terminal and refuses `--json`. `inspect blocks`, `inspect content` and
`inspect links` are the same three reads it is built on, with an envelope.

`s` opens the query workspace: query plus optional RFC3339 `since`/`until`, graph depth (blank means one), the first
25 matches, the executed physical plan and selected indices. Graph, vector/PCA and ordered-range tabs are bounded
inspection views of indices that actually ran; hash lookup is named in the plan and has no diagram.

If you are telling a user how to drive it: arrows walk the blocks, `left`/`m` reaches the module sidebar and
`right`/`enter` comes back, `i` says which brain is open and why, `t` swaps a PDF for its extracted text, `o`
opens the bytes in the desktop's own viewer, `?` lists every key. A preview is a thumbnail — a page in a
60-column pane is 60x80 pixels — so `t` and `o` are how a document actually gets read.

### `config` — the configuration
| command | for |
|---|---|
| `config show` | the merged result, and where each value came from |
| `config get KEY` / `config set KEY VALUE` | one value. Both act on the same file `config path` names |
| `config path` | which `vitruvio.toml` is in play |
| `config validate` | the schema, without opening a brain |
| `config embedder list` | every embedder this build can construct |
| `config embedder test` | actually embed something and report the width and model tag |

### `skills`, `completion`, `bench`
| command | for |
|---|---|
| `skills list` | the skills this build ships |
| `skills install` | copy them into a repository. `--into`, `--skill`, `--force` |
| `completion SHELL` | a static completion script. Never calls back into vitruvio |
| `bench` | recall and latency gates. `--tier`, `--queries`, `--gate`. Needs the `[bench]` extra |
| `bench corpus` | generate a synthetic corpus |

## The exit codes decide whether to retry

`0` ok · `1` a bug in vitruvio · `2` you asked wrong · `3` config or no brain · `4` not found · `5` **protocol
violation, never retry** · `6` **a policy refused, never retry** · `7` candidates rejected, repair and retry · `8`
not a fast-forward, pull then push · `9` registry, retryable · `10` needs a human · `11` a source, retryable.

Full reasoning in `../vitruvio/references/exit-codes.md`, which ships with the `vitruvio` skill — this one links
into it rather than repeating it, so install both.

## What does not exist

Do not offer these; they are not implemented, and a plausible-looking command that fails teaches nothing:

- **No `calibrate`.** `query explain --analyze` reports actuals beside estimates and persists nothing. Correcting
  the cost model means editing `[planner]` in `vitruvio.toml` by hand.
- **No history-rewriting rollback command.** `dist pull --allow-rollback` may deliberately adopt a served ancestor;
  the snapshot it replaces stays in `brain history`, but nothing rewrites or deletes history. Use `dist fetch` plus
  `reconcile` whenever the point is to keep both sides' work.
- **No `answer` field**, on any command. The brain returns evidence and the prose is yours.

## Every command and every flag

`../vitruvio/references/cli-reference.md`, generated from the cyclopts declarations by
`python -m vitruvio.cli.reference --write`, and checked in CI. Read it when you need a flag this file omitted; read
`--help` when you need to be certain.
