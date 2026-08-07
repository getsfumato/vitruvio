# 7. Embeddings and the vector index

```bash
vitruvio config embedder list
vitruvio config embedder test "una funcion periodica"
vitruvio config set embedding.text.uri "local-st:intfloat/multilingual-e5-base"
```

| provider | uri | extra | modalities |
|---|---|---|---|
| `hashing` | `hashing:bow` | — | text — **the default**, and its tag says `hashing/bow` so nobody mistakes it for semantics |
| `fake` | `fake:deterministic` | — | text, image — sha256-derived, bit-exact, for tests |
| `local-st` | `local-st:intfloat/multilingual-e5-base` | `[local]` | text — multilingual, because ES and EN belong in one space |
| `local-siglip` | `local-siglip:google/siglip-base-patch16-224` | `[vision]` | text, image |
| `openai` / `voyage` / `cohere` | `openai:text-embedding-3-large` | `[api]` | text |

## Two spaces, one index

`Space.TEXT` for text, `Space.MULTIMODAL` (SigLIP's image tower) for images. A text query is embedded into **both** and
both are searched; results fuse by rank with the space visible on every hit.

Not one SigLIP space for everything: its text tower is trained on alt-text of at most 64 tokens and measurably degrades
pure text retrieval, which is most of the traffic. Not two indices: the protocol has one vector index per module and
packs one travelling layer.

The honest consequence is that there is **no score comparable across modalities**, and that is exactly why fusion is by
rank rather than by score.

## The model tag, and why a mismatch is not a degradation

```
local-st/intfloat--multilingual-e5-base@a1b2c3d4#d768,f16,l2,mean,e5-qp,none,proj1,chunk1
```

Everything that changes *where a vector lands in the space* is in there: provider, model, revision, dimensions, dtype,
normalization, pooling, prompts, preprocessing, projection, chunker. A different chunker embeds different strings, so a
consumer must reject it as firmly as a different model.

A mismatched tag means the index is **never consulted**. Not degraded — *wrong*: the two spaces have no relationship,
so the cosines would be noise that silently poisons fusion, which is worse than having no index at all. `explain` names
which field differs.

## The embedding cache is not optional

The SDK rebuilds every index on every commit *and* on every open. Without a cache that is a full re-embed each time.

The cache lives at `.vitruvio/embeddings/<hash of model tag>.sqlite` and is keyed by the hash of the **string actually
embedded** rather than by block id. So two blocks that project identically share one vector, re-registering identical
content is free, and editing a field that is not projected costs nothing. A commit adding 5 blocks to a module of
50 000 costs 5 embeddings.

## Chunking is by characters

1600 characters, 200 overlap, preferring a blank line, then a sentence end, then a word boundary.

Not by tokens, and this is a correctness decision rather than a preference: a token-based chunker depends on which
tokenizer happens to be installed, so chunk boundaries — and with them the cache keys and the identity of every vector
— would differ between an install with `[vision]` and a full one.

The payoff is the locator: `chunk:3#1600-3200`. Citations point at spans, not at whole PDFs.

## Why the vector layer travels

Embedding vectors and HNSW structure are not byte-reproducible across machines, and no design fixes it. So the layer is
published inside the artifact rather than rebuilt, and vitruvio refuses to publish an empty one: an artifact whose layer
claims a vector index and carries none is worse than one that omits the layer, because a consumer can detect an absence
and cannot detect an emptiness.

Round-trip tests therefore assert equality of *results*, never of bytes. `threads > 1` is allowed and HNSW internals
are never asserted.

## Next

[8. Ingest](08-ingest.md)
