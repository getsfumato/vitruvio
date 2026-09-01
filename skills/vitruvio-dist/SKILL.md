---
name: vitruvio-dist
description: Publish a Boltzmann brain to a registry, or install one from a registry. Use when asked to push, pull, publish, share, or install a brain, when a push was refused, or when registry credentials need setting up.
allowed-tools: Bash(vitruvio:*), Read
---

# Publishing and installing a brain

A published brain is an OCI Artifact: a small manifest, one layer per memory module, and a config blob that is the
snapshot document. Because the modules are separate blobs, a consumer can install one module and later update only
what changed.

## Always plan-pull before you pull

```bash
vitruvio dist plan-pull <REF> --tag v1 --json
```

A canonical layer can be gigabytes. "How much is this going to cost" should be answerable without paying it, and
`fetch_bytes` answers it. `is_noop: true` means the brain is already at the published state.

```bash
vitruvio dist pull <REF> --tag v1 --json
vitruvio brain verify --json          # do this after every pull
vitruvio auth status --json           # determines whether the installed head is governed
```

After every successful `dist pull`, run those two checks against the exact brain that was pulled. Read
`data.trust_root` from `auth status`: a non-null value means the installed head is governed. For a governed brain,
always run the `auth attribution` authorship audit too, even when the pull reports `data.authenticity: authorized`:

```bash
vitruvio auth attribution --json
```

The pull's authenticity gate and this audit answer different questions. Pull refuses a signed unauthorized head
before adoption, but configured policy may still permit an unsigned governed head; attribution is report-only and
compares newly introduced provenance actors with the `subject`s vouched for by the accepted signing keys.

Raise an explicit **possible authorship breach** warning when the governed head is not `authorized`, or when
attribution returns `complete: false` or `fully_vouched: false`. Include `asserted`, `legacy`, `evidence_gaps`, and
`detail` in the warning so the user can distinguish an unvouched actor, a legacy identifier, and unreadable evidence.
Do not call the brain corrupt solely because this audit warns: merges can legitimately introduce an actor not vouched
for by the head's signer. Never hide or downgrade the warning, and do not describe that head as fully authenticated.

If a published vector index is incompatible with the configured embedder, keep the strict refusal by default. When
the user explicitly chooses to install the verified modules without those derived layers, plan and pull with
`--ignore-vector-indices`, then rebuild compatible vectors locally:

```bash
vitruvio dist plan-pull <REF> --tag v1 --ignore-vector-indices --json
vitruvio dist pull <REF> --tag v1 --ignore-vector-indices --json
vitruvio index build --force --json
vitruvio brain verify --json
vitruvio auth status --json
# If data.trust_root is non-null:
vitruvio auth attribution --json
```

This omits only vector-index layers, not memory modules. Read `ignored_vector_indices` in both results and surface the
warning: until the rebuild completes, structural and lexical retrieval remain available but vector retrieval does not.

A **selective** pull (`--module semantic`) is a legitimate, permanent state. The modules you did not take are
*missing*, not broken, and `inspect resolvability` reports them as such — do not treat that report as corruption.

## Publishing

```bash
vitruvio dist pack --tag v1 --json                 # build it locally, push nothing
vitruvio dist push <REF> --tag v1 --json
```

### A project publishes several brains at once

When the repository holds a *project* — several named brains under one `vitruvio.toml` — you usually pass no
reference at all. Each brain derives `<namespace>/<project>-<brain>`:

```bash
vitruvio project show --json                       # read `repository` before pushing: it is the destination
vitruvio dist push --all --tag v1 --json
```

`--all` **skips** a brain with nothing committed rather than failing it — a subject nobody has started yet is the
ordinary state of a project. Read `skipped` in the payload before concluding something went wrong. A brain that
fails for a real reason does not stop the others, and the command exits non-zero at the end.

`--all` refuses a reference, because a reference names one repository and this publishes several. It also **skips a
brain declaring `publish = false`**, which is how a project marks somebody else's upstream.

### A pull replaces local work, and says so

Use it to *install* someone's brain, not to escape a divergence — `dist fetch` is for that.

`dist pull` adopts the published composition with no fast-forward check, so blocks committed locally since the last
pull leave the composition. Both commands report it:

```bash
vitruvio dist plan-pull --tag v2.4 --json    # read data.impact: certainty, blocks, block_ids
vitruvio dist pull --tag v2.4 --json         # the same shape; completed impact is exact when readable
```

Read `impact.certainty` before treating the count as a fact: planning is `approximate`, completed readable pulls are
`exact`, and an unreadable comparison is `unknown` with `blocks: null`. `discarded` and `discarded_blocks` remain as
legacy fields, but they cannot represent unknown honestly. When exact impact is greater than zero, say plainly that
the blocks left the composition and name the snapshot from `brain history` that still holds them. Nothing is
destroyed -- the blobs remain and the snapshot is retained -- but **no command restores it**, so do not promise a
rollback you cannot perform.

When a user asks to update a brain they have been writing into, run `plan-pull` first and show them
`impact`, including its certainty, before pulling. That is the only point at which the choice is still theirs.

### Never publish a brain the project marked unpublishable

`vitruvio --brain X dist push` exits **6** with code `PUBLISH_FORBIDDEN` when brain X declares `publish = false`.
That is a declaration, not a malfunction: the brain was installed from someone else, and pushing it would publish a
fork under this project's repository while the real one moves on. Do not work around it by passing an explicit
reference, and do not edit `vitruvio.toml` to flip the flag. Report the refusal and stop; flipping it is the user's
decision to state, not yours to infer from being asked to publish.

The prohibition is on publishing only. `source pull`, `ingest run` and `index build` on that brain are all fine.

`pack` first, and **read the warnings**. The one that matters: *"the vector index will not be published"*. A brain
published without its vector layer is a brain nobody else can search semantically, because the vector index is the
one index a consumer cannot rebuild. The usual cause is a stale index — `vitruvio index build` and pack again.

Two refusals come from the protocol and both are correct:

- **Exit 8 — not a fast-forward.** Someone pushed since this brain was pulled, and the histories diverged. The
  answer is `dist fetch`, not `pull` — see below. **Never `--force`**: it discards their version, and there is
  no undo.
- **Narrowing refused.** Publishing fewer modules than the last version would make a consumer's selective update
  silently lose one.

### Exit 8 is reconcilable, and `pull` is the wrong reflex

Not sure yet whether the brain is behind, ahead or diverged? The `vitruvio-sync` skill is the decision tree; this
section is the diverged branch of it.

`pull` *adopts* the published composition, so it resolves the divergence by discarding whatever was committed
here. `fetch` adopts nothing: it brings their history alongside yours, the pointer does not move, and both sides'
work survives.

```bash
vitruvio dist fetch <REF> --tag v1 --json
```

Read `data.reconciliation`. Three outcomes, and each has one next step:

| `why` | what happened | next |
|---|---|---|
| — (`attempted: true`) | the plan was clean, so it committed | `brain verify` |
| `already contained` | their history is in here already | nothing |
| `no strategy declared` | nobody stated how to record it | see below — **ask the user** |
| `not clean` | blocks did not apply, or work of yours would leave. **Nothing was written and no reconciliation was opened** | `vitruvio reconcile resolve` |

**`no strategy declared` is not a malfunction to work around.** The three strategies land the same blocks and
differ only in whose name stays on the incoming work, so vitruvio refuses to choose. Do not edit `vitruvio.toml`
to unblock yourself and do not pick the one that looks tidiest — run `vitruvio reconcile plan <THEIRS> --json`,
show the `attribution` table, and let the user decide. `merge` is the only strategy under which the contributor's
snapshots, and anything they signed, still cover something.

When it is `not clean`, the verdicts are the report to act on. `missing_evidence` carries the one distinction an
agent must relay correctly: `dropped_deliberately` means tell the contributor **not** to resend, because this
brain excluded that evidence on purpose; `never_held` means tell them to resend the contribution **whole**,
because its source never arrived. Same verdict, opposite advice.

Never `resolve --admit` a `rejected` block, and do not report the refusal as a bug: a derived block whose
evidence is not in the composition cannot be audited against its source, and `brain verify` would not catch it —
verification recomputes hashes and compositions, not citations across modules.

An open reconciliation **refuses every ordinary write** on that brain. If a commit or an ingest comes back with
exit 12, that is why; `reconcile status` shows what is open and `reconcile abort` abandons it, writing nothing.

## Credentials

**A prior `docker login` does not authenticate vitruvio.** This is deliberate, not an oversight: the ORAS client
shells out to Docker's credential helper with no timeout, and a helper that blocks hangs the whole push with no
output at all. vitruvio isolates itself from that store and asks you to import once, under its own timeout:

```bash
vitruvio registry login docker.io --from-docker --json
vitruvio registry login docker.io --username <U> --token-stdin < pat.txt
vitruvio registry whoami docker.io --json
```

Docker Hub needs a **Personal Access Token with Read & Write** scope; the account password does not work. The token
is read from stdin or an unechoed prompt, never from a flag — a token on a command line lands in the shell history
and the process list. Never put one in `vitruvio.toml`, which is committed.

`whoami` reports where the credential came from, which is how you answer "why is it publishing as someone else".

## Check a registry before the first push

```bash
vitruvio registry check <REF> --json
```

A brain's manifest carries a custom `artifactType` and a custom `config.mediaType`, and registries have
historically disagreed about whether that is allowed. This pushes a probe artifact with exactly that manifest shape
and reports four things: `/v2/` reachable, credentials carry write scope, the custom config media type was
accepted, and the `artifactType` survived a round trip.

If a registry refuses, the alternatives are a registry that accepts it (`ghcr.io` does) or a local one:

```bash
docker run -d -p 5000:5000 --name registry registry:2
vitruvio registry check localhost:5000/you/brain --insecure --anonymous --json
vitruvio dist push localhost:5000/you/brain --tag v1 --insecure --anonymous --json
```

There is no compatibility mode, and asking for one is asking for a different artifact: changing the media type
would mean the thing published is no longer a Boltzmann brain.

## No network at all

```bash
vitruvio dist push demo/brain --tag v1 --local ./registry --json
vitruvio dist pull demo/brain --tag v1 --local ./registry --json
```

A filesystem registry of OCI layouts. Same code path, no credentials, no rate limits — the right thing to test a
round trip against before pointing anything at a real host.

## One note on hostnames

`docker.io` is the *index* hostname, not the API: `https://docker.io/v2/` returns a web page. The API is at
`registry-1.docker.io`, and vitruvio substitutes it for you. Every command prints the `effective` endpoint, so the
substitution is never invisible — if it looks surprising, that field is the answer.
