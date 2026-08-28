# ADR-0015: Compound Retrieval Across A Project's Brains, And Why It Fuses By Rank

**Decision status:** Accepted.

## Context

ADR-0010 made a project a set of named brains under one configuration -- a subject per brain, a client per brain,
a metric per brain -- and recommended it as the shape to keep knowledge in. Every retrieval operation then addressed
exactly one of them. The case that forced this record is a project of three metric brains and the question "what do
`metrica-a` and `metrica-b` say about selection bias", which had no command: two searches and two bundles whose
scores could not be compared, because `fusion.normalize` rescales every bundle so *its* best match reads `1.00`.

Composing them raises four questions the SDK does not answer -- it has no notion of a brain inside an
`EvidenceBundle`, and its models are frozen -- and that vitruvio therefore had to.

## Decision

### 1. A compound is scoped to one project, by accepting names only

`--brains` takes the names the project declares. A path is refused with the same error a name from another project
gets, and the hint lists the project's names. Composing across projects is thus not forbidden by a check but
inexpressible: the vocabulary a compound accepts is the project's.

This is also why the flag is `--brains` and not a repeated `--brain`: the singular is the meta app's global option
and selects one brain for forty other commands, and a compound is about the project rather than about any brain in
it. The command resolves with `require_brain=False` and `require_layout=False`, so a stale global `--brain` cannot
break a command that ignores it.

### 2. Every brain answers on its own; composition is a rule applied afterwards

Each member is opened through its own `BrainSession` over a configuration derived from the project's by
`model_copy` -- same actor, policy and planner calibration, a different layout -- and `RetrievalOps.search` runs in
it unchanged. Composition happens over the dictionaries `wire.evidence` produced, in a stateless helper
(`runtime/cross_brain.py`) that opens nothing. There is no compound planner and no compound plan; `compound explain`
returns one explanation per brain, side by side.

The fan-out lives in the runtime (`ops/compound.py`), not in the CLI where `dist push --all` put its loop, for the
reason `pull_all` gave first: which failures are fatal and what a skipped brain looks like must be answered once, so
the MCP server does not answer them again. The operations object holds sessions for the duration of one call and
never a `Brain`, which keeps ADR-0013's rule intact.

### 3. Grouped by default; fused by rank on request; never sorted by score

Grouped output returns every brain's ranking intact, one after the other, with a one-entry `brains` list on each
match. It is the default because it claims nothing the bundles did not: two `1.00` from two brains are not a tie.

`--fuse` applies reciprocal-rank fusion across brains -- the rule ADR-0005 already chose across generators inside a
brain, with the same `K = 60` and the same "absence contributes zero" -- and rescales the top to `1.00`. Ranks are
comparable across brains where scores are not. A block is the hash of its content, so a block two brains hold is
one block, and it accumulates from both: that is the cross-brain signal the feature exists to surface, and it falls
out of using the identity the protocol already gives rather than anything added.

The block dictionary in a fused match is the first brain's. Per-brain state that could differ -- `superseded_by`,
`resolvable` -- is therefore the first brain's too, and `brains[]` is where a reader finds out which brains said so.

### 4. Roots stay per brain; one payload shape for both modes

`verified_against` lives under `members[]` and is never merged upward. Two brains holding semantic memory have two
semantic roots, and a citation has to name the one it verified against; a merged dictionary keyed by memory type
would have to drop one. `truncated` and `all_verified` aggregate at the top level as `any` and `all`, and stay per
member beneath.

The payload has the same keys in both modes so a consumer branches on `fused` and nothing else. It is a dictionary
built at the runtime seam, not an `EvidenceBundle`: the SDK's model is frozen, forbids extra fields, and has no
brain in it, and the runtime seam is already the dictionary.

### 5. The choosing is a skill, not a prompt

The command is non-interactive, like every command but `browse` (ADR-0002): without `--brains` or `--all` it refuses
with the names in the hint. The interactive part -- which project, which brains, which question -- is the
`vitruvio-compound` skill, where an agent asks the user through its own means. A CLI prompt would have been a second
selection mechanism with rules the flags do not share, which ADR-0012 §7 already refused for the browser's picker.

## Consequences

- One shape to learn: grouped output reuses the single-brain `bundle` renderer per brain, so a compound section and
  a `search` result are the same table.
- A compound of *n* brains costs *n* index rebuilds. Stated in the guide rather than hidden; parallel opening is
  left for when it is measured to matter.
- `explain` gained its first test anywhere in the suite, through `compound explain`.
- The command count moved to sixteen groups and ninety-three commands, and the pinned counts in
  `test_docs_promises.py` moved with it.
- pyboltzmann is pinned to `0.6.0` on the branch that introduced this: `0.7.0` was released the same week, and a
  feature built against a moving SDK is a feature nobody can bisect.

## What was rejected

- **Declared compounds in `vitruvio.toml`** (`[compounds.x] brains = [...]`). Reproducible, but a second thing to
  keep in step with `[brains]`, and the invocation already states its whole context. It can be added later without
  changing what `--brains` means.
- **Sorting a concatenation by score.** Wrong for the reason above, and wrong in the direction that hides it: it
  would look like a ranking.
- **A merged `EvidenceBundle`.** Frozen, brain-less, and `verified_against` keyed by memory type would collide.
- **Interleaving by rank** (round-robin). Predictable, but it rewards nothing and so surfaces no agreement.
- **An interactive picker in the CLI.** See §5.
