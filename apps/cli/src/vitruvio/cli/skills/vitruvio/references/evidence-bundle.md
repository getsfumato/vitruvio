# The evidence bundle

What a search returns. Every field, and — more usefully — what each one does *not* mean.

```json
{
  "matches": [
    {
      "block_id": "sha256:9fcecd9c57...",
      "memory_type": "semantic",
      "score": "1.00",
      "content": {"kind": "fact", "label": "Serie de Fourier", "statement": "..."},
      "resolvable": true,
      "verified": true,
      "locator": "lines:1-5",
      "evidence": ["sha256:fe6f2cdb9c..."],
      "superseded_by": null,
      "depth": 0
    }
  ],
  "roots": {"semantic": "sha256:95fd781b61...", "canonical": "sha256:fd522c4afb..."},
  "truncated": false,
  "all_verified": true,
  "degradations": []
}
```

## `roots`

The Merkle root of each module the answer came from. This is what makes an answer *citable*: quoting a block
without the root it was verified against is quoting something nobody can check later. If you are producing a
durable citation, include the root.

## `score`

**Agreement between retrieval strategies.** Not a probability, not a confidence, not a relevance guarantee.

Scores come from weighted reciprocal-rank fusion, because the underlying signals are not comparable in principle —
unbounded corpus-dependent term frequencies, cosine similarity in [-1,1], an exact match's point mass, ordinal
graph distance. A high score means several independent strategies agreed; a low one means only one did.

It is a **string** in the JSON, at the protocol's precision. Do not parse it to a float. The final ordering was
decided on the full-precision value before rendering, so the ranking is more precise than the displayed score
suggests — two matches showing `1.00` are not tied.

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
discard is recorded in `degradations` as `verification_failed`.

So: a short bundle plus a `verification_failed` degradation is corruption, and belongs in exit-5 territory rather
than in an answer.

## `superseded_by`

Present means a later block took precedence. The superseded block is still a member and still proves into the root —
only accessibility changed. Cite the successor, or say explicitly that you are quoting a superseded claim.

Superseded blocks are held back by default. Seeing one means it was asked for.

## `locator` and `evidence`

`locator` points *into* the source — `"chunk:3#1600-3200"`, `"lines:40-58"`, `"[page 3]"`. `evidence` is the list of
canonical blocks this one cites, and it is never empty for a derived block.

Together they are how a citation becomes checkable: `evidence` says which document, `locator` says where in it.
Quote both.

## `depth`

How many graph hops from a direct match. `0` is a direct hit; higher came in through relation expansion. Expansion
competes with direct matches as its own ranked list rather than overwriting them, so a depth-2 match outranking a
depth-0 one is possible and meaningful — but worth mentioning when you cite it.

## `degradations`

Why the answer might be worse than it could be. `stale_statistics`, `index_absent`, `model_mismatch`,
`embedder_unavailable`, `recall_floor_lowered`, `verification_failed`. An empty list is the clean case.

If you are about to state that the brain has no knowledge of something, read this list first: `index_absent` plus a
lexical-only plan is a very different claim from a clean exhaustive search.
