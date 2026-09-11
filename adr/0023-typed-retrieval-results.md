# ADR-0023: Retrieval results are typed, and a polymorphic payload is a union over the memory type

## Context

ADR-0022 typed lifecycle and completed reconciliation, recorded the wire shape of every other operation, and
deferred retrieval with a reason: `search` and `explain` are the worst of the untyped surface — 117 and 140 recorded
paths — but `matches[].content` is polymorphic across memory types, so typing it honestly is a modelling decision
about the block payload and not a conversion of a return annotation. Typing it dishonestly, as `dict[str, Any]`,
would have added a contract that omits the one part that varies.

Meanwhile the readers kept describing that payload on their own. Four implementations answered "what names this
block": the CLI's evidence renderer, the TUI's query workspace, the diagnostics node builder in the runtime, and
`browse.py`, each with its own field list and its own sentinel. ADR-0013 had already counted the cost: `search` and
`explain` alone are read through some seventy distinct string keys across three stacked layers. And the reference an
agent cites from, `evidence-bundle.md`, documented `locator`, `evidence` and `depth` on a match and `roots` on the
bundle — fields that do not exist at the levels it named — because nothing tied the prose to the shape.

Two facts about the SDK fix what an honest type can say. `Match.content` is `Block.payload()`, which is
`model_dump(mode="json", exclude_none=True)`: a field that is `None` is absent, not `null`; which fields exist depends
on the memory type *and* on the schema version the block was written under — a version-3 semantic block has no
`statement`, a version-1 one has no `scheme`; and the whole payload is `{}` when the block is not resolvable. The
bundle around it, its matches, its sources and the planner's explanation are ordinary full dumps, in which every
field is present and an optional one is `null`.

## Decision

**Two dump regimes, two rules.** A type over a full dump is total, with `X | None` where the model says so. A type
over a payload dump states the vocabulary and the value types, and marks presence with `NotRequired`; the content
types are `total=False` outright, for three independent reasons — the empty payload of an unresolvable block, the
fields that exist in one schema version and not another, and a bundle that mixes versions. What such a type buys is
not presence but *possibility*: `content["statement"]` on a canonical match is a type error, and `content.get("lable")`
is one too.

**The discriminator is on the match.** `MatchResult` is a union of five `TypedDict`s, one per memory type, each with
`memory_type: Literal["..."]` and a `content` of that memory type's vocabulary. It is where the SDK puts the fact,
and comparing it narrows `content`: `if match["memory_type"] == "semantic":` is the whole protocol a reader needs.
The vocabularies live in `vitruvio.runtime.block_result`, apart from the bundle, because browsing projects the same
payloads and will read the same types when its turn comes.

**Provenance is typed to the record.** `record` is a union over `record_type` of the seven records the ledger holds.
Schema version 2 added `assisted_by` to six of them and dropped a derivation's `producer`; a removal stays at
version 1 by protocol, so a verifier can always decode it. Across the union, a key is required only when every
version has it and never leaves it `None` — so `assisted_by` and `producer` are `NotRequired`, and everything else
is what the record class says. The payload actor is its own type, `RecordActorResult`, and not the lifecycle
`ActorResult`: the same three fields under two regimes, `name` absent in one and `null` in the other.

**The explanation is mirrored, not imported.** `ExplanationResult` and its five nested types repeat the planner's
models field by field. Importing `vitruvio.planner` would pull `vitruvio.stats` onto the eager path of
`import vitruvio.runtime`, which `test_import_cost` forbids for a reason that has not changed. A test compares each
mirror with its model — same keys, all required, every nested model mirrored by the type its field names — so the
two cannot drift silently. One place the mirror corrects the model: `estimation_error` is keyed by node id, an
`int`, and `model_dump(mode="json")` renders JSON keys as strings, so the wire type says `dict[str, float]`.

**Two keys are the operation's own.** `plan` exists only when a cost-based planner ran and `diagnostics` only when a
caller asked, so both are `NotRequired` on `SearchResult`. The search `plan` is a hand-flattened subset of the
explanation and its `intent` is the intent's kind alone where `explain` reports the whole intent; both shapes predate
this contract and stay as recorded, described rather than repaired.

**`cast` sits wherever mypy cannot follow the construction, and each one is named rather than counted.** Three
kinds: a pydantic dump (`wire.evidence`, `explain`, the per-operator dumps inside the search plan), a plain
dictionary arriving from another layer (the two index diagnostics, built in `vitruvio.indices`), and a union spread
into a new dictionary. Everything else built by hand in the runtime is a typed literal that mypy checks. The cost is
that a `cast` is a claim nothing verifies, so it is worth naming the sites and not worth asserting there are none --
`wire.snapshot` and `ops.lifecycle.state` already cast a hand-built literal before this slice, and saying otherwise
would have made this record wrong on the day it was written.

**A block's identity has one owner.** Four readers answered "what is this block called" on their own and
disagreed. `browse.identify` is now that rule, named and exported: an episodic block by its summary, a semantic one
by its label or, label-free, by its predicates, a procedural one by its label, a canonical one by its media type, a
provenance block by its record type. It is browse's rule because browse's was the only one that knew the memory type
and the only one tests had pinned. `MatchView` carries it to the CLI's evidence table and the TUI's query workspace;
the diagnostics graph labels its nodes with it. One sentinel, `(unnamed)`, everywhere but the diagram, where two
nodes so named would be indistinguishable and a nameless node keeps its short digest. A bundle carries no
registration origin, so a canonical match is named by its media type where a browse row is named by its file --
stated here rather than papered over.

**A compound match is its single-brain match plus `brains`.** ADR-0015 fixed one shape for both modes and this
writes it down: five compound variants, each a subclass of its single-brain variant with the brains that returned
the block, so the typed renderer that draws a bundle draws a compound section unchanged. Not one match type with
`brains` optional -- a bundle's match never carries it and a compound's always does. `members` keeps each brain's
own summary, roots and plan, as before; `compose`, `grouped` and `fused` build typed literals, with a `cast` at
the two places a union is spread into a new dictionary.

**The recorded shape is the oracle, and it is walked through the type.** Every path recorded for `search` and
`explain` must resolve through the `TypedDict`s — a list descends to its element, a mapping keyed by data admits any
name, `Any` admits anything below it — so a field the suite has observed and the type forgot fails a test. The
corpus was widened in the same commit to observe what it never had: a procedural match with its steps, an episodic
one with its context, a semantic one with relations, and the ordered-index window a time-filtered search shows. The
vector projection, which no fast search selects, is pinned against its type at the index level instead.

**The reference is tested against the recording.** `evidence-bundle.md` now shows the shape `search` returns, and a
test asserts that every path of its example and every field it gives a heading is a recorded path. The
documentation of a contract is part of the contract.

## Consequences

- `search` and `explain` are declared `ResultKind.TYPED` in the catalogue; the conformance test derives that from
  the return annotation, so the module name `retrieval_result` is load-bearing.
- A reader that already narrows on `memory_type` gains a type checker; one that does not keeps working, because the
  payload is the same dictionary it always was. Nothing changed on the wire beyond the paths the widened corpus
  observed for the first time.
- `dict[str, Any]` is not assignable to a `TypedDict`, so a fixture built by hand and passed to a typed parameter
  needs a `cast`. That is the cost of a contract that means something, and it falls on tests rather than on readers.
- A pyboltzmann or planner upgrade that moves a payload field now fails the runtime suite at the mirror test, which
  is preferable to a field that exists in the type and not on the wire.
- Three things a person sees changed, all in human output and none pinned by a test before: a provenance match is
  `registration` in `vitruvio search` where it was `registration record`, and in the TUI where it was
  `(no identifying field)`; a label-free semantic relation is `Relation · classified_as` where both showed the
  sentinel; and the sentinel itself is `(unnamed)` everywhere. Canonical, episodic, labelled semantic and procedural
  matches read as before.
- The readers are typed on the way: `render.bundle` takes a `SearchResult`, the TUI's `_fill` and the four query
  views take the plan and diagnostics types, and a key they misspell is a type error. Hand-built payloads in the
  tests grew the fields every match has.
- `compound_search` and `compound_explain` return `CompoundSearchResult` and `CompoundExplainResult`, declared
  `TYPED` in the catalogue and walked against the recording like the other two. Retrieval is typed end to end; the
  remaining `dict[str, Any]` domains -- authenticity, catalog, retention, sources, install -- are pinned by the
  recording and follow the same idiom when their turn comes.

## What was rejected

**Pydantic result models.** Validating on the way out would make every read pay for a check the operation already
guarantees, and would put a second model between the SDK and the wire. ADR-0022's idiom — a `TypedDict` over the
dictionary that is actually returned — costs nothing at runtime and describes exactly what is there.

**Required content keys.** A canonical block always has a `blob` — when it is resolvable, and when the reader
knows which schema version wrote it. Neither is given at the type level, and a required key that is sometimes absent
is a `KeyError` with a type checker's blessing.

**The discriminator inside `content`.** It would have meant adding a field to the payload, which is a wire change
the recorded shape would have refused, and it would have put the fact in a second place: the match already says
what memory type it holds.

**Importing the planner for its models.** One eager import, and every `import vitruvio.runtime` pays for the
statistics package. The mirror is forty lines and a test.

**A first-string-field scan as the shared rule.** Three of the four readers did that -- `label`, then `summary`,
then `statement`, then `media_type` -- and it cannot name a relation or a record, ranked `goal` above `label` in one
place and nowhere else, and needed a per-reader sentinel. The memory type is known; a rule that ignores it guesses.

**Repairing `plan.intent` while typing it.** It is a string where `explain` has an object, and making them agree
would change a recorded shape in a commit whose promise is that no shape changes.
