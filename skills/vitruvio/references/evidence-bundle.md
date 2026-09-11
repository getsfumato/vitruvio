# The evidence bundle

What a search returns. Every field, and — more usefully — what each one does *not* mean. There is no `answer` field
and there never will be: the bundle is evidence, and the prose is yours.

```json
{
  "matches": [
    {
      "block_id": "sha256:9fcecd9c57...",
      "memory_type": "semantic",
      "score": "1.00",
      "content": {
        "kind": "fact",
        "label": "Serie de Fourier",
        "statement": "Descompone una funcion periodica en senos y cosenos.",
        "evidence": ["sha256:fe6f2cdb9c..."]
      },
      "sources": [{"block_id": "sha256:fe6f2cdb9c...", "locator": "lines:1-5"}],
      "verified": true,
      "resolvable": true,
      "superseded_by": null
    }
  ],
  "verified_against": {"semantic": "sha256:95fd781b61...", "canonical": "sha256:fd522c4afb..."},
  "truncated": false,
  "authorship": {
    "state": "unsigned",
    "snapshot": "sha256:1c0e6d2a9b...",
    "key": null,
    "subject": null,
    "trust_root": null,
    "pinned": false
  },
  "all_verified": true,
  "plan": {
    "signature": "TermScan(semantic)",
    "intent": "lookup",
    "indices_consulted": {"semantic": ["bm25"]},
    "indices_available": {"semantic": ["bm25", "hash_map"]},
    "operators": [],
    "est_cost_us": 120.0,
    "est_recall": 0.98,
    "degradations": []
  }
}
```

## `content`

The block's payload, as the protocol stores it. Which keys exist follows from `memory_type`, and from nothing else
in the match:

| `memory_type` | keys |
|---|---|
| `canonical` | `blob`, `media_type`, `size`, `normalized_view` |
| `episodic` | `summary`, `occurred_at`, `ended_at`, `context`, `participants`, `outcome`, `evidence`, `tags`, `content` |
| `semantic` | `kind`, `label`, `statement`, `subject`, `evidence`, `relations`, `aliases`, `content` before schema version 3; `kind`, `scheme`, `label`, `exclusive`, `evidence`, `relations` from it |
| `procedural` | `label`, `goal`, `steps`, `preconditions`, `success_criteria`, `subject`, `evidence`, `content` |
| `provenance` | `record`, whose `record_type` is `registration`, `derivation`, `normalization`, `supersession`, `demotion`, `validation` or `removal` |

Three rules, and every reader needs all three. A key whose value would be empty is **absent, never `null`**. A block
that is not `resolvable` has an empty `content`. And a semantic relation written by the catalog may carry no `label`
at all — its identity is the predicate on its `relations`. Read `content` with `.get`, never by position or by
assuming a key.

**What a match is called** comes from one rule in the runtime, the same one `inspect blocks` and `vitruvio browse`
apply to a row: an episodic block by its `summary`, a semantic one by its `label` or, for a label-free relation, by
its predicates as `Relation · classified_as`, a procedural one by its `label`, a canonical one by its `media_type`,
and a provenance block by its `record_type`. When nothing names the block, every interface says `(unnamed)`.

One difference from a browse row, and it is deliberate: a canonical **match** is named by its `media_type`, where a
browse **row** of the same block is named by its file. The file name lives in the registration record, which a
browse listing reads and a bundle does not carry — so do not expect `application/pdf` here and `notes.pdf` there to
be two blocks. ADR-0023 records why this is stated rather than papered over.

`evidence` inside a derived block's `content` names the canonical blocks it cites; `sources` below is the same
citation with a locator. For a canonical block, `blob` is the content address of its bytes — pass it to
`vitruvio inspect content` — and `normalized_view`, when present, names the extracted text.

## `score`

**Agreement between retrieval strategies.** Not a probability, not a confidence, not a relevance guarantee.

Scores come from weighted reciprocal-rank fusion, because the underlying signals are not comparable in principle —
unbounded corpus-dependent term frequencies, cosine similarity in [-1,1], an exact match's point mass, ordinal
graph distance. A high score means several independent strategies agreed; a low one means only one did.

It is a **string** in the JSON, at the protocol's precision. Do not parse it to a float. The final ordering was
decided on the full-precision value before rendering, so the ranking is more precise than the displayed score
suggests — two matches showing `1.00` are not tied.

Relation expansion competes with direct matches as its own ranked list rather than overwriting them. A match that
came in through a graph hop may outrank a direct hit, and the bundle does not say which is which: cite what the
match holds, not how it was reached.

## `truncated`

`true` means **there may be more**, and vitruvio's definition is deliberately stricter than a plain "the limit was
reached": it is true whenever a candidate that passed every filter was discarded, *including* one cut by a
generator's `k`.

Concretely: a vector probe with `k=40` over 500 masked vectors sets `truncated: true` even if it returned 12
matches, because 460 vectors were never looked at. This is the only defensible reading of "there could be more".

**Check this field before saying the brain does not contain something.**

## `resolvable`

`false` means the block is a **verifiable member whose content cannot be read**. Two legitimate causes: a selective
install did not fetch that module, or the block was redacted. It is *not* corruption — `inspect resolvability`
distinguishes `tombstoned` (redacted) from `missing` (not installed).

You may cite that such a block exists and what module it belongs to. You may not quote its content, and you must not
infer content from its label.

## `verified`

Always `true` for anything returned. A block that fails membership, inclusion-proof or store-hash verification is
**discarded**, never returned with a flag — returning it would make corruption look like a low-quality result. The
discard is recorded in `plan.degradations` as `verification_failed`.

So: a short bundle plus a `verification_failed` degradation is corruption, and belongs in exit-5 territory rather
than in an answer.

## `superseded_by`

Present means a later block took precedence. The superseded block is still a member and still proves into the root —
only accessibility changed. Cite the successor, or say explicitly that you are quoting a superseded claim.

Superseded blocks are held back by default. Seeing one means it was asked for.

## `sources`

The canonical evidence the block cites, one entry per cited block. `block_id` says which document; `locator` points
*into* it — `"chunk:3#1600-3200"`, `"lines:40-58"`, `"[page 3]"` — and is `null` when the citation names the whole
document. Together they are how a citation becomes checkable. Quote both.

A canonical block cites nothing, so its `sources` is empty; a derived block's is never empty.

## `verified_against`

The Merkle root of each module the answer came from, keyed by memory type. This is what makes an answer *citable*:
quoting a block without the root it was verified against is quoting something nobody can check later. If you are
producing a durable citation, include the root.

## `authorship`

Who assembled the brain the evidence came from, and whether that key was authorized: `state` is `authorized`,
`unsigned`, `attributable` or `unauthorized`, `snapshot` is the version it was evaluated for, and `key`, `subject`
and `trust_root` name the signer when there is one. `pinned` says whether the consumer has pinned that trust root.

It is reported apart from `verified` on purpose. `verified` says the bytes are intact and members of the installed
snapshot; `authorship` says who signed that snapshot. "Intact, and signed by an authorized key" and "intact,
provenance unknown" are different claims, and a bundle that folded them together could express neither.

## `plan`

What the planner did, present whenever a cost-based planner ran. `indices_consulted` against `indices_available` is
the first thing to read when a search misses: an index that exists and was not chosen is a different situation from
one that does not exist. `intent` is the query's kind as the planner classified it; `signature` and `operators` are
the physical plan; `est_cost_us` and `est_recall` are what it expected of itself.

`degradations` is why the answer might be worse than it could be: `stale_statistics`, `index_absent`,
`model_mismatch`, `embedder_unavailable`, `recall_floor_lowered`, `verification_failed`. An empty list is the clean
case. If you are about to state that the brain has no knowledge of something, read this list first: `index_absent`
plus a lexical-only plan is a very different claim from a clean exhaustive search.

`vitruvio query explain` returns the same plan in full, with every alternative the planner considered and why each
was rejected.

## `diagnostics`

Present only when a caller asks for it, which `vitruvio browse` does for its query workspace and `vitruvio search`
never does: the graph, vector and ordered-index views of the indices the plan chose, projected for a person to
look at. It is a picture of the retrieval, not part of the evidence, and nothing in it is citable.
