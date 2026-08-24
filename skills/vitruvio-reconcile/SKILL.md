---
name: vitruvio-reconcile
description: Join a Boltzmann brain history somebody else advanced into this one. Use when a push was refused as diverged, when asked to merge, rebase or squash two brains, when a fetch reported open questions, or when a command exits 12 because a reconciliation is open.
allowed-tools: Bash(vitruvio:*), Read
---

# Reconciling two histories

Two people wrote into the same brain from a shared version. Both sets of work are real and the protocol keeps
both — this is the mechanism that does it, and it replaces the old advice of pulling and losing one side.

## Forget what you know from git

**The three strategies produce the same set of blocks.** In git they differ in how the result is *computed*: a
rebase replays patches and can land on a tree a merge would not have produced. Here a snapshot states a whole
composition rather than a patch, so there is nothing to replay sequentially, and merge, rebase and squash land
byte-for-byte the same composition.

What differs is the lineage recorded, and therefore **who stays on record as the author**:

| strategy | parents | their snapshots | their signatures |
|---|---|---|---|
| `merge` | two or more | kept | survive |
| `rebase` | one (yours) | reissued under new identities | lost |
| `squash` | one (yours) | collapsed into one | lost |

So the choice is about attribution, never about tidiness. `ReconcileRequest.strategy` has no default in the
protocol and vitruvio adds none.

**Never choose a strategy for the user.** Run `reconcile plan` and show them the `attribution` table. `merge` is
the only one under which a contributor's snapshots, and anything they signed, still cover something — prefer it
for somebody else's contribution, and reserve `rebase` for work that has not been published, because it
invalidates any root already out there.

**There are no textual conflicts.** A block is immutable and content-addressed, so two people editing the same
block is not a representable state. What people do instead is supersede, contradict or drop — each of which is a
case this handles. A conflict here is a *validation* failure: the structural reconciliation is automatic, its
result goes through the ingestion gate, and what did not apply comes back as verdicts. There is nothing to
hand-edit and no merged file with markers in it.

## The flow

```bash
vitruvio dist fetch <REF> --tag v1 --json   # 1. bring their history; the pointer does not move
vitruvio reconcile plan <THEIRS> --json     # 2. what would happen, and what each strategy costs
vitruvio reconcile merge <THEIRS> --reason "incorporate Ana's ingest" --json
vitruvio reconcile status --json            # 4. if it stopped to ask
vitruvio brain verify --json                # 5. after every reconciliation
```

The structural indices track the reconciled composition by themselves. The **vector** index does not — it is
stale after any write, reconciliation included — so if the user relies on semantic search, run
`vitruvio index build` afterwards. This is the same advice as after an ingest, not a reconciliation quirk.

`dist fetch` does steps 1–3 by itself **when it is safe to**: the brain has to declare a strategy, and the plan
has to be clean. Read `data.reconciliation.why` — `no strategy declared`, `already contained`, `not clean`, or
`attempted: true`. See the `vitruvio-dist` skill for that table.

A brain declares its strategy in `vitruvio.toml`:

```toml
[brains.algebra]
path = "./brains/algebra"
reconcile = "merge"        # merge | rebase | squash
```

**Absent by design.** Do not add it to unblock yourself — it is a statement about attribution and it is the
user's to make. Ask, then write what they say.

## When it stops to ask

Exit **12** and `halted: true` mean the operation is asking a question, not failing. Nothing was written and no
pointer moved. `reconcile status` lists what is open; every question is one block.

```bash
vitruvio reconcile status --json
vitruvio reconcile resolve <BLOCK> --reject --json
vitruvio reconcile continue --json
vitruvio reconcile abort --json              # abandons it; nothing was written either way
```

`vitruvio reconcile resolve` with **no block** opens an interactive workspace for a human — do not use it
yourself, it needs a terminal and has no JSON output. Decide one block at a time instead.

### The three decisions, and the one that is refused

- `--reject` — always available. Declining needs no justification the protocol can check. The block is not
  destroyed: it stays in the store and in the history it came from, and the new root simply does not name it.
- `--admit` — for a **contradiction** or a **pending review**. Holding two claims that disagree is a legitimate
  state and which one is right is not a question the protocol answers.
- `--prefer <BLOCK>` — for two histories that replaced the same block with different successors. Both stay
  recorded; this names which takes precedence.

**Never `--admit` a `rejected` block, and do not report the refusal as a bug.** A derived block whose cited
evidence is absent from the composition breaks R1, and no later check recovers it — `brain verify` recomputes
hashes and compositions, not citations across modules. The fix is to supply the evidence in an ordinary commit,
outside the reconciliation.

### `missing_evidence`: same verdict, opposite advice

The one field an agent must relay correctly. Both readings are a rejection; the advice inverts:

| value | what happened | tell the contributor |
|---|---|---|
| `dropped_deliberately` | this brain excluded that evidence on purpose, and a removal record proves it | **do not resend** — it would re-import exactly what was excluded |
| `never_held` | the identity is unknown here; the derived block shipped without its canonical source | **resend it whole** — the work may be fine and the transfer was incomplete |

Collapsing these two discards legitimate work over a packaging mistake, or re-imports evidence somebody removed
on purpose. Read the field; do not infer from the verdict.

## When work of yours would leave

Exclusion wins by construction: a block the other history dropped **does** leave your composition, even though
you still hold it. That is the rule and it is deliberate, but applying it is a decision about work already here,
so it has to be stated:

```bash
vitruvio reconcile accept-removals --json
```

One answer, not one per block — there is no per-block choice to offer when exclusion wins by construction.
Re-admitting a removed block afterwards is an ordinary commit.

Before accepting, **say how much is leaving and from which modules** (`withdrawn` in the status payload). Do not
describe a reconciliation that removes the user's blocks as clean.

`abort` always works, including when `status` refuses. If the layout was changed outside vitruvio while a
reconciliation was open, the recorded state describes a head the brain has moved off — `status` reports that as
a refusal, and `abort` is the remedy. The payload then carries `stale: true` and no detail, because reading the
detail is what failed, not the abandoning.

## An open reconciliation blocks writes

While one is open the SDK refuses every ordinary write — commit, ingest, register, drop. A commit coming back
with exit 12 and `RECONCILE_OPEN` is this, not corruption. `reconcile status` shows what is open; `continue`
concludes it and `abort` abandons it, writing nothing either way.

## Seeing the shape of it

```bash
vitruvio reconcile tree --json          # where the two parted, and what each side added per module
vitruvio brain history --graph          # the lineage: `*` first-parent chain, `o` merged in, `M` a reconciliation
```

History is a DAG now, not a chain. The **first parent** is not merely first: it is the history a reconciliation
was performed onto, it is what every rule meaning "the parent" refers to, and it is the chain an audit follows.

## Two failures that are not reconcilable

- **`NO_COMMON_ANCESTOR` (exit 5).** The two histories share no ancestor, so a block present on one side and
  absent from the other is ambiguous between "they added it" and "I dropped it" — which demand opposite
  outcomes. Refused rather than guessed. Do not retry; these are unrelated brains.
- **A push still refused after reconciling.** Verify first (`brain verify`), then re-read the reference and tag.
  A successful reconciliation makes their head an ancestor of yours, so the push is a fast-forward again.
