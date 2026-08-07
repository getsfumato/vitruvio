# 1. What a brain is

A **Boltzmann brain** is a directory that is also an OCI image layout. Inside it:

```
demo/
├── oci-layout  index.json  blobs/sha256/...   # the layout: the SDK's, and portable
├── boltzmann/                                 # compositions, snapshots, the head pointer
└── .vitruvio/                                 # everything vitruvio derives. Deleting it costs time, never knowledge.
    ├── indices/  stats/  embeddings/
    └── calibration.json  estimation.jsonl
vitruvio.toml                                  # beside the brain, and committed
```

Vitruvio adds exactly one file and one directory, both outside the layout, so `oras cp` and every other OCI tool keep
working on a brain vitruvio has touched.

## Five modules, and why the count is fixed

| module | holds | removal |
|---|---|---|
| canonical | the evidence itself, as registered, plus deterministic normalized views | drop (policy-gated) |
| episodic | what happened, when | **supersede only** — append-only by protocol |
| semantic | facts, definitions, claims | drop |
| procedural | goals and ordered steps | drop |
| provenance | who derived what from what, and everything that was removed | written by the protocol |

Each module has its **own Merkle DAG and its own root**. That is what makes a selective install coherent: you can
take semantic memory and leave a canonical layer of gigabytes behind, and what you took still verifies on its own.

## Registration is not assertion

Registering a source does **not** say it is true. Canonical memory asserts one thing: *this evidence was incorporated
and preserved*. Every interpretation of it is a separate block that cites it through provenance.

That separation is what makes a brain re-interpretable. When the models improve, you re-derive the interpretations
against the same evidence and supersede the old ones — and the record of what was previously believed survives.

## Three hash levels, deliberately not one type

- **BlockId** — the identity of one block, over its canonical bytes.
- **MerkleRoot** — the identity of a module's whole composition.
- **OciDigest** — the identity of a blob or manifest in the layout.

They are distinct types throughout, in the SDK and in vitruvio, and the type checker enforces it. Collapsing them
into "a hash" is how a client comes to compare a block id against a root and conclude something false.

## Next

[2. Install and first brain](02-install-and-first-brain.md)
