---
name: vitruvio-retention
description: Remove or demote knowledge in a Boltzmann brain. Use when asked to delete, drop, forget, retract, supersede, demote, prune or redact something from a brain, or when a removal was refused by policy.
allowed-tools: Bash(vitruvio:*), Read
---

# Removing knowledge

There is no delete. There are five mechanisms, and choosing the wrong one is the mistake this skill exists to
prevent — because four of them are recoverable and one is not.

| the user means | use | irreversible? |
|---|---|---|
| "this is wrong, take it out" | `retain drop` | no; the bytes remain until `prune` |
| "this replaced that" | `retain supersede` | no; membership is unchanged |
| "this is stale, rank it lower" | `retain demote` | no |
| "reclaim disk" | `retain prune` | yes, and harmless: no retained root needs them |
| "this must not exist anywhere" | `retain redact` | **yes, permanently** |

**Default to `supersede`.** It keeps the record of what was believed, which is most of the value of an auditable
brain, and it is the only mechanism episodic memory has at all.

## Always plan before you drop

```bash
vitruvio retain plan-drop <BLOCK_ID> --memory-type semantic --json
```

**Show the user the cascade before dropping anything**, and quote the number. A drop's cost is not local: excluding
one block excludes everything derived from it. Dropping a canonical source takes every interpretation of it with it.

`retain drop` runs the plan itself and requires `--yes`. `--yes` skips the *prompt*, never the plan — so read the
plan you printed. If `rederivable` is non-zero, `--rederive-against <NEWER_BLOCK>` is usually the better move:
re-derive the dependents from newer evidence instead of losing them.

```bash
vitruvio retain drop <BLOCK_ID> --memory-type semantic --reason "superseded by the 2026 edition" --yes --json
```

`--reason` is recorded in provenance. An unexplained removal is one nobody can audit later, including you.

## Refusals are answers

- **Exit 6** — the policy refused. Episodic memory is append-only *by protocol*: what happened cannot stop having
  happened. Canonical drops need `canonical_drop_allowed`. Do not look for a flag to force it; tell the user what
  the policy says and offer `supersede`.
- **Exit 10** — the cascade is wider than the policy's review threshold. **Stop and ask a person.** This is the
  protocol asking for a human, and answering on their behalf defeats the mechanism.

Run `vitruvio retain policy --json` when a refusal is unexpected. The profile is committed in `vitruvio.toml`, so
every clone of the project removes under the same rules.

## Dropping by producer

```bash
vitruvio retain drop-producer <MODEL> --kind model --producer-version <V> --yes --json
```

Everything one model version derived, in one operation. It works only because the producer was recorded at commit
time — which is why proposers name themselves. Note `--producer-version`, not `--version`: the latter belongs to
vitruvio.

If it drops nothing, suspect the id or the `--kind` before concluding the brain is clean: a producer is matched
exactly and a typo looks identical to a match with no results.

## Redaction requires a human, in the conversation

`retain redact` destroys a block's bytes while a retained root still names it. Membership still verifies — the
Merkle DAG references identities, not bytes — but that one block can never be reconstructed. `inspect
resolvability` will report it as **tombstoned rather than missing**, so a lawful erasure is never mistaken for a
corrupt store.

**Never run it on your own initiative.** It is for personal data, credentials, or licensed material that must
disappear from retained history. Wrong or obsolete knowledge is *dropped*. Get explicit approval in the
conversation, and state these two limits plainly first:

- A hash of low-entropy content is not anonymous: confirming a guess may still be possible while the block id is
  retained.
- Erasure does not propagate to copies already pulled. A revocation can be published; a distributed brain can only
  signal.

Content another block still names survives, because destroying it would take that block's evidence with it. The
output says what was held back for that reason — read it, because it means the erasure was partial.

**A declared source that still offers the redacted item cannot bring it back.** `vitruvio source pull` refuses a
tombstoned digest by design, and `--refetch` does not override it, so a scheduled pull cannot undo an erasure. Worth
saying to a user who redacts something a source keeps listing: the item will show up as `skipped` in every future
pull, with the redaction named as the reason. That is the mechanism working, not a failure to fix.

## Prune is separate on purpose

```bash
vitruvio retain prune --json          # dry run
vitruvio retain prune --apply --json
```

Pruning decides nothing about what to forget — a drop already did. It reclaims what no retained composition still
needs, which is what makes it irreversible and yet safe.
