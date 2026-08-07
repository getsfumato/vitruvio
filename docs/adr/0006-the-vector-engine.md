# ADR-0006: The Vector Engine, And What Makes A Dump Publishable

**Decision status:** Accepted, implemented in M5.

## Context

Five of the six index kinds are deterministic functions of the blocks: any client rebuilds them and gets the same
thing. The vector index is the one that is not. Embedding vectors and HNSW structure are **not** reproducible byte for
byte across machines — BLAS kernel selection, non-associative float reduction, insertion order, RNG — and no design
fixes that.

That is precisely why the protocol has a `TravellingIndex`: the vector layer ships inside the artifact instead of being
rebuilt. Which turns the engine choice into a serialization question as much as a search question, and makes one
failure mode the thing to design against: an artifact whose layer *claims* a vector index and carries an empty one. A
consumer can detect an absence; it cannot detect an emptiness.

## Decision

### usearch

It serializes to bytes, which is what makes it fit `TravellingIndex.dump/load` at all. `metric="cos"`, `dtype="f16"`
(1.5 KB per vector at under 0.5% recall loss), `connectivity=16`, expansion 128/64.

### An explicit key table, never truncation

usearch keys are `uint64` and a `BlockId` is 256 bits, so truncating is not an option — it is a silent collision.
`rows: dict[int, VectorRow]` maps each key to `(block_id, space, chunk_index, span, source_digest, cache_key)` with a
monotonic counter and keys that are never recycled. **The table travels inside the same bytes as the HNSW buffers**, so
publishing something we do not hold is not expressible.

### The model tag is composite, and composed by the index

Everything that changes *where a vector lands in the space* is in the tag: provider, model, revision, dims, dtype,
normalization, pooling, prompts, preprocessing, **projection and chunker**. A different chunker embeds different
strings, so a consumer must reject it exactly as firmly as a different model.

But the projection and the chunker are decisions of whatever *calls* the embedder — an embedder does not know what text
it will be handed — so `VectorIndex` composes them in rather than the embedder claiming them. Equality is exact string
equality, which is what the SDK checks; `parse()` exists so the CLI can say **which field** differs.

A mismatched tag means the index is never consulted. Not degraded — *wrong*: the two spaces have no relationship, so the
cosines would be noise that silently poisons fusion, which is worse than having no index at all.

### Chunking is by characters, not tokens

`max_chars=1600`, `overlap=200`, preferring a blank line, then a sentence end, then a word boundary. A token-based
chunker would depend on which tokenizer happened to be installed, so the chunk boundaries — and with them the cache
keys and the identity of every vector — would differ between an install with `[vision]` and a full one. The payoff is
`VectorHit.locator` (`"chunk:3#1600-3200"`): citations point at spans, not at whole PDFs.

### An embedding cache is not optional

The SDK rebuilds every index on every commit *and* on every open. Without a cache that is a full re-embed per commit.
SQLite in WAL mode at `<brain>/.vitruvio/embeddings/<sha256(model_tag)[:16]>.sqlite`, keyed by
`sha256(model_tag ‖ space ‖ modality ‖ role ‖ content_key)` where `content_key` hashes the **string actually embedded**
rather than the block id. So two blocks that project identically share a vector, re-registering identical content is
free, and editing a field that is not projected costs nothing. A commit adding 5 blocks to a module of 50 000 costs 5
embeddings.

### Two spaces, one file, one object

`Space.TEXT` (e5) for text and `Space.MULTIMODAL` (SigLIP's image tower) for images. A text query is embedded into both
and both are searched; results fuse by rank with `VectorHit.space` visible.

Not one SigLIP space for everything: its text tower is trained on alt-text of ≤64 tokens and measurably degrades pure
text retrieval, which is ~95% of the traffic. Not two indices: `IndexKind.VECTOR` is one per module and only one
travelling layer is packed. The honest consequence is that there is no score comparable *across* modalities, which is
exactly why fusion is by rank.

### Refusing to publish an empty index

`flush()` refuses to write an empty index and deletes any file that would have held one. `dump()` is gated by the same
refusal check. And because `Brain._vouched` is only populated by `_build` on a non-rebuildable index or by
`_load_index`, a perfectly current index loaded from vitruvio's own sidecar would be omitted from `pack()` with nothing
said — so `vitruvio.runtime.vouch` closes that gap, in the one place allowed to touch an SDK private, and
**un-vouches** rather than merely reporting when an index turns out unpublishable. The upstream ask
(`Brain.vouch(memory_type)`) is documented in that module; it would delete it.

## Consequences

- **Round-trip equality of results, never of bytes.** `dump()/load()` is asserted by search behaviour. HNSW internals
  are never asserted and `threads > 1` is allowed, because byte-level reproducibility here is not achievable and
  pretending otherwise would only produce a flaky suite.
- Removals above 30% trigger `_compact()`, which rebuilds from the embedding cache: no model calls, no network.
- Measured `recall@10 = 1.00` on the six ground-truth queries of the synthetic corpus, at roughly 4.5 ms each.
- The protocol guarantees **verifiability of blocks, not identical ranking**, and this ADR is why. Two clients that
  ship the same brain agree on every block and every root; they may order the tenth and eleventh result differently.
