# ADR-0009: Retention, And Why There Are Five Mechanisms

**Decision status:** Accepted, implemented in M10.

## Context

Removal is where a knowledge protocol earns or loses trust. The paper gives it five distinct mechanisms — drop,
supersede, demote, prune, redact — and the obvious product decision is to hide that behind one friendly `delete`.

That would be wrong, and not for purist reasons. Four of the five are recoverable and one is not; two change membership
and two change only accessibility; one is refused outright for episodic memory. A single `delete` has to pick one of
those behaviours and be silently wrong about the others.

## Decision

### The five stay distinct, and the CLI names the trade-off

| mechanism | changes | reversible |
|---|---|---|
| `drop` | composition, and cascades through provenance | bytes remain until `prune` |
| `supersede` | accessibility only; membership untouched | yes |
| `demote` | ranking only, recorded in the ledger | yes |
| `prune` | reclaims blobs no retained root names | no, and harmless |
| `redact` | destroys bytes a retained root still names | **no** |

`supersede` is the default recommendation, in the CLI help and in the skill. It keeps the record of what was believed,
which is most of the value of an auditable brain, and it is the *only* mechanism episodic memory has — because what
happened cannot stop having happened.

### Plan before drop, structurally

`retain drop` computes the cascade itself, prints it, and requires `--yes`. `--yes` skips the *prompt*, never the plan.

Two details are load-bearing. `drop` re-runs the plan rather than trusting one the caller was handed, because between
the plan a caller read and the drop it authorised the composition may have moved. And in `--json` mode the confirmation
**refuses** rather than prompting: prompting into a pipe that will never answer, or reading consent from the absence of
a terminal, are both worse than an error that names `--yes`.

### Refusals carry their own exit codes

- **Exit 6** — the policy refused, and it is terminal. Episodic memory is append-only; canonical drops need
  `canonical_drop_allowed`; redaction needs `redactable_media_types`. The message names `supersede` and `demote` as the
  alternatives rather than a flag that would force it, because there is no such flag by design.
- **Exit 10** — the cascade exceeded the policy's review threshold. This is not an error; it is the protocol asking for
  a human, and it gets its own code so an agent cannot answer on the human's behalf without noticing.

### `--producer-version`, not `--version`

Found by running it. cyclopts owns `--version` at the app level, so `retain drop-producer m --version 1` printed
vitruvio's own version and exited zero — the drop silently never ran, and the output looked like a successful command.
Renaming it is the whole fix, and the collision is worth remembering for any future command that wants a version of
something other than vitruvio.

### Redaction is arranged to feel heavy

Refused unless the policy names redactable media types. Requires a reason — an unexplained destruction of evidence is
indistinguishable from an attack on the record. Requires confirmation. And the skill instructs an agent never to run it
on its own initiative, with the two limits stated to the user first: a hash of low-entropy content is not anonymous, and
erasure does not propagate to copies already pulled.

Content another block still names **survives**, because bytes are addressed by their hash and destroying them would take
the other block's evidence with it — while that block stayed a resolvable member, so nothing would report the loss. The
output says what was held back, which means a partial erasure is visible rather than assumed complete.

## Consequences

- Verified end to end by running the CLI: a semantic drop rebuilt the module root and the brain still verified; a
  canonical drop was refused with exit 6 after printing its 4-block cascade; superseding removed a block from search
  results while it stayed in the composition; `drop-producer pipeline:vitruvio-structure@1` took both derived blocks;
  and a redaction left the module reporting **1 tombstoned, 0 missing** with `brain verify` still passing.
- That last line is the point of the whole design: a lawful erasure and a corrupt store are different states, and
  `inspect resolvability` distinguishes them.
- `prune` stays a dry run by default, matching the SDK: the safe direction is the one you can repeat.
