---
name: vitruvio-sync
description: Work out why a local Boltzmann brain and its published copy disagree, and bring them back in step without losing either side's work. Use when a push exits 8, when the user asks whether their brain is behind, ahead, out of sync, or diverged from the registry, when two people wrote into the same brain, or when the user is about to pull or force-push to "fix" a mismatch.
allowed-tools: Bash(vitruvio:*), Read
---

# Local and remote disagree

There is a brain on disk and a brain in a registry, and they are not the same. That sentence covers four different
situations, and three of them have a one-command answer that is safe. The fourth is a reconciliation. The mistake
this skill exists to prevent is treating all four as the fourth — or, worse, treating the fourth as one of the
first three and losing a week of somebody's work.

**Establish which situation you are in before running anything that writes.** Every command in the first section
below moves no pointer and discards nothing.

## First, find out what is true

```bash
vitruvio brain state --json            # where this brain came from, and what it is now
vitruvio dist tags --json              # what the registry holds under the configured reference
vitruvio dist plan-pull --tag <TAG> --json
```

Read three things:

- `data.origin` in `brain state` — `reference` and `tag` are the copy this brain was installed from. `null` means
  it was never pulled: it was born here, and "remote" is only whatever `vitruvio.toml` or the logged-in account
  derives. `data.ancestry` lists the snapshots this brain has been, most recent first.
- `data.tags` — whether the tag the user is talking about exists at all. A tag nobody pushed yet is not a
  divergence, it is a first push.
- `data.is_noop` and `data.impact` in `plan-pull`. `is_noop: true` means the registry holds exactly what is
  installed. `impact.blocks` is how many blocks were committed here since the last pull — read
  `impact.certainty` first, because a plan is `approximate` and `unknown` comes with `blocks: null`.

When both sides have moved, bring their history alongside yours **without adopting it** and look at the split:

```bash
vitruvio dist fetch --tag <TAG> --no-reconcile --json     # their head lands in the store; the pointer stays
vitruvio reconcile tree <THEIRS> --json                    # THEIRS is data.digest from the fetch
```

`tree` reports `ours`, `theirs`, the `ancestor` they parted at, and per module how many blocks were
`added_by_us`, `added_by_them` and `removed`. That table is the whole picture, and it is what you show the user.

## The four situations

| what you observed | situation | the safe answer |
|---|---|---|
| `plan-pull` says `is_noop: true`; a push would find its own digest | in step | nothing |
| registry moved, `impact.blocks` is `0` with `exact` certainty | **behind** — nothing committed here since the pull | `dist pull`; then the post-pull checks below |
| registry unchanged since the pull, blocks committed here | **ahead** | `dist push`; it is a fast-forward and exits 0 |
| both moved: push exits **8**, `tree` shows blocks on both sides | **diverged** | `dist fetch`, then reconcile — see below |

Two of these deserve a word.

**Behind is the only case where `pull` is right**, and it is right because `impact` says there is nothing to
lose. If `impact.blocks` is greater than zero, or `certainty` is `unknown`, you are not behind — you are diverged
and have not fetched yet. Say so and go down the fourth row.

After every successful `dist pull`, run `brain verify` and `auth status` on that same brain. If `data.trust_root`
from `auth status` is non-null, the installed brain is governed: always run `auth attribution`. Raise a **possible
authorship breach** warning when the governed head is not `authorized`, or the audit reports `complete: false` or
`fully_vouched: false`; include `asserted`, `legacy`, `evidence_gaps`, and `detail`. This is not proof of corruption —
a merge may legitimately introduce an actor the head's signer does not vouch for — but never call the result fully
authenticated until the warning has been reviewed.

**Ahead needs no ceremony.** A push that is a fast-forward is refused by nothing. If it comes back 8, the registry
moved between your `plan-pull` and your `push`; that is the fourth row now, not a retry.

## Diverged: keep both sides

```bash
vitruvio dist fetch --tag <TAG> --json      # 1. bring theirs; reconciles by itself only when that decides nothing
vitruvio brain verify --json                # 2. after any reconciliation that committed
vitruvio dist push --tag <TAG> --json       # 3. their head is now an ancestor of yours; the push is a fast-forward
```

Step 1 does one of four things, and `data.reconciliation.why` says which:

| `why` | what happened | next |
|---|---|---|
| `clean` (`attempted: true`) | the brain declared a strategy and every block applied | steps 2 and 3 |
| `already contained` | their history is already in yours | step 3; if that exits 8, re-read the tag |
| `no strategy declared` | nobody has said how to record the join | `reconcile plan <THEIRS>`; **ask the user** |
| `not clean` | blocks did not apply, or work of yours would leave; **nothing was written** | open it by hand — below |

`<THEIRS>` is `data.reconciliation.theirs`, which is also `data.digest`. The fetch does not remember it for you,
so carry it.

### `no strategy declared`

The three strategies land byte-for-byte the same composition and differ only in whose name stays on the incoming
work — `merge` keeps their snapshots and signatures, `rebase` and `squash` reissue the work under yours. That is
an attribution decision and vitruvio refuses to make it. So do you:

```bash
vitruvio reconcile plan <THEIRS> --json      # show data.attribution to the user
vitruvio reconcile merge <THEIRS> --reason "incorporate <who>'s <what>" --json
```

Say that `merge` is the only strategy under which a contributor's snapshots still cover something, and that
`rebase` invalidates any root they already published. Then run what they chose. **Do not add `reconcile = ...` to
`vitruvio.toml` to unblock yourself**; if the user wants it declared for next time, write what they said.

### `not clean`

The fetch reported and stopped, because opening a reconciliation blocks every write until it is resolved. You open
it deliberately, with the strategy the user chose:

```bash
vitruvio reconcile merge <THEIRS> --reason "..." --json   # exits 12 and halts, listing what is open
vitruvio reconcile status --json                          # every open question is one block
vitruvio reconcile resolve <BLOCK> --reject --json        # or --admit, or --prefer <BLOCK>
vitruvio reconcile accept-removals --json                 # only if `withdrawn` is non-empty, and only after saying so
vitruvio reconcile continue --json
vitruvio brain verify --json
```

Exit 12 with `halted: true` is a question, not a failure. What the verdicts mean and which decision each one
admits is the `vitruvio-reconcile` skill's subject; the two rules that matter here are that a `rejected` block is
never `--admit`ted, and that `missing_evidence` is relayed as written — `dropped_deliberately` means the
contributor must *not* resend, `never_held` means they must resend *whole*.

If `withdrawn` names blocks, **say how many and from which modules before accepting.** Exclusion wins by
construction — a block the other history dropped leaves your composition — but it is the user's work leaving, and a
reconciliation that removes their blocks is not "clean" in any report you write.

`vitruvio reconcile abort` abandons an open reconciliation and writes nothing. It always works, including when
`status` refuses because the layout moved underneath it.

## What never fixes a divergence

- **`dist push --force`.** It overwrites the registry with your history and discards theirs. There is no undo and
  no command that recovers what a force-push replaced. The flag exists for a version that is genuinely to be
  thrown away, and "I want the push to go through" is not that.
- **`dist pull` to escape exit 8.** A pull adopts the published composition; every block committed here since the
  last pull leaves it. The blobs remain and the old snapshot is retained, but **no command restores it**. This is
  the row-two answer and only the row-two answer.
- **Retrying the push.** Exit 8 is not a transient failure and comes back identical every time. The registry did
  not hiccup; it refused.
- **Editing `vitruvio.toml`** to declare a strategy, flip `publish = false`, or point at a different reference so
  the refusal goes away. Each of those is a statement the user makes, not a workaround.

## Things that look like a divergence and are not

| symptom | what it is | do |
|---|---|---|
| a commit or ingest exits **12**, `RECONCILE_OPEN` | a reconciliation is already open on this brain | `reconcile status`; `continue` or `abort` |
| push exits **9** | the registry was unreachable or refused the request | retry; check `registry whoami`, `registry check` |
| push exits **4**, `REFERENCE_NOT_FOUND` | nothing published under that reference and tag yet | it is a first push; push |
| push exits **6**, `PUBLISH_FORBIDDEN` | the brain declares `publish = false` — it is somebody else's upstream | do not publish; `pull` is how this brain updates |
| push exits **5**, `NO_COMMON_ANCESTOR` after a fetch | the two histories are unrelated brains | do not reconcile; ask which one the user means |
| `brain verify` fails | corruption, not a sync problem | `inspect resolvability`; stop |
| `origin.partial: true` in `brain state` | a selective install — modules deliberately not taken | nothing; those modules are missing, not stale |
| search finds nothing after a pull or reconciliation | the vector index is stale after any write | `index build`; not a divergence |

## In a project with several brains

Each named brain has its own remote and its own answer. Run `vitruvio project show --json` and go through them one
at a time with `--brain <name>`; a brain marked `publish = false` can only ever be behind, and one with nothing
committed is skipped by `dist push --all` rather than failed. Do not report a project as diverged because one brain
is: name the brain.

## What to tell the user

In every case, before the writing command, state in one line: which of the four situations it is, how many blocks
each side added and in which modules (from `tree`), what would leave (from `impact` or `withdrawn`), and which
command you are about to run. In the diverged case, add whose name stays on the incoming work under the strategy
they chose. After it, `brain verify` and say the push is a fast-forward again — or say precisely why it still is not.
