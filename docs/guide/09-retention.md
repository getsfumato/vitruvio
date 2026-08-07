# 9. Retention: five ways to remove something

There is no delete. There are five mechanisms, and four of them are recoverable.

| the intent | the command | irreversible? |
|---|---|---|
| this is wrong | `retain drop` | no — the bytes remain until `prune` |
| this replaced that | `retain supersede` | no — membership unchanged |
| this is stale | `retain demote` | no |
| reclaim disk | `retain prune` | yes, and harmless |
| this must not exist | `retain redact` | **yes, permanently** |

`retain policy` shows what the brain in front of you permits. The profile lives in `vitruvio.toml`, so every clone
removes under the same rules.

## Plan before you drop

```bash
vitruvio retain plan-drop <BLOCK_ID> --memory-type semantic
vitruvio retain drop <BLOCK_ID> --memory-type semantic --reason "superseded by the 2026 edition" --yes
```

A drop's cost is not local: excluding one block excludes everything derived from it, and dropping a canonical source
takes every interpretation of it with it. `drop` computes and prints the plan itself; `--yes` skips the *prompt*, never
the plan.

If the plan reports `rederivable`, `--rederive-against <NEWER_BLOCK>` is usually the better move: re-derive the
dependents from newer evidence instead of losing them.

`--reason` is recorded in provenance. An unexplained removal is one nobody can audit later, including you.

## Blocks are not mutated

A drop rebuilds the module's Merkle DAG over the survivors and produces a new root. The old blocks still exist; the
composition changed. Consumers of the old root are unaffected until they pull, which is what makes distribution and
retention compose at all.

## Refusals are answers

- **Exit 6** — the policy refused. Episodic memory is append-only *by protocol*: what happened cannot stop having
  happened. Canonical drops need `canonical_drop_allowed`. There is no flag to force it; `supersede` is the path.
- **Exit 10** — the cascade exceeded the policy's review threshold. Stop and ask a person. Answering on their behalf
  defeats the mechanism.

## Supersede is usually the right answer

```bash
vitruvio retain supersede <NEW> --supersedes <OLD> --memory-type semantic --reason "better wording"
vitruvio retain demote <BLOCK_ID> --memory-type semantic --reason stale
```

The superseded block stays in the composition and keeps proving into the root; only accessibility changes, so search
holds it back unless asked. That keeps the record of what was believed, which is most of the value of an auditable
brain.

Demotion is recorded in the ledger rather than on the block, because a block is immutable: accessibility as a *field*
would change the block id and make a demoted block a different block.

## Dropping by producer

```bash
vitruvio retain drop-producer <MODEL> --kind model --producer-version <V> --yes
```

Everything one model version derived, in one operation. It works only because the producer is recorded at commit time
rather than inferred afterwards. Note `--producer-version`: `--version` belongs to vitruvio itself.

## Redaction is different in kind

```bash
vitruvio retain redact <BLOCK_ID> --memory-type semantic --reason "personal data"
```

It destroys a block's bytes while a retained root still names it. Membership still verifies — the Merkle DAG references
identities, not bytes — but that one block can never be reconstructed. `inspect resolvability` reports it as
**tombstoned rather than missing**, so a lawful erasure is never mistaken for a corrupt store.

Refused by default: the policy must name `redactable_media_types`. It is for personal data, credentials, or licensed
material that must disappear from retained history. Wrong or obsolete knowledge is *dropped*.

Two limits, worth stating to whoever asked:

- A hash of low-entropy content is not anonymous: confirming a guess may still be possible while the block id is kept.
- Erasure does not propagate to copies already pulled. A revocation can be published; a distributed brain can only
  signal.

Content another block still names survives, because destroying it would take that block's evidence with it — and that
block would stay a resolvable member, so nothing would report the loss. The output says what was held back.

## Next

[10. Publishing](10-publishing.md)
