# ADR-0025: A brain shares vector identity and each machine chooses a runtime

**Decision status:** Accepted.

## Context

A vector index travels with a published brain. Its model tag has to name the embedding space that produced the
vectors, but different machines may reach that space through OpenAI, OpenRouter, Ollama, a compatible gateway, or
local weights. Some machines cannot run the published model at all. Using an unrelated model against those vectors
would return plausible but wrong rankings; rebuilding them merely to make local queries work would replace the
published layer.

## Decision

The project configuration declares the shared embedding model. A brain-specific choice in local XDG state selects
the runtime that can reach it and stores no credentials. The CLI records that choice; skills guide a person through
the choice after inspecting the published model tag. A compatible choice inherits the shared model identity,
revision, and dimensions unless those are supplied explicitly. The model tag names the canonical model identity,
not the transport endpoint. Querying an imported vector layer also checks a small set of stored reference
embeddings before trusting the runtime.

When no compatible runtime is available, retrieval builds a deterministic hashing/BOW index in a local namespace.
That index answers only this machine's queries. Writes and publication construct indices from the shared model
declaration; the local query choice cannot become a travelling vector layer. If the shared model cannot be built,
its vector index is absent from the write configuration rather than silently replaced. Structural indices remain
available. The fallback does not alter the imported sidecar.

Runtime assembly owns fallback selection and storage namespaces; index engines receive resolved embedders and
paths. The selection command is a service operation so each interface uses the same validation and inheritance.
Explicit index builds use the shared WRITE view, and invalidate cached query views when the rebuild completes.

An unchanged pre-reference model tag retains its former compatibility contract and acquires reference probes on
first use. An old runtime-prefixed tag can be copied to its canonical model identity only when the other tag fields
match and up to three stored passage vectors agree within 1e-4. Retrieval reads travelling payloads by the digest
bound in the snapshot, checks the recorded model, and saves migrations locally. This bounds migration cost while
refusing ambiguous aliases. Changing the shared model and explicitly reindexing replaces its canonical sidecar.

Installing is an opening mode under WRITE authorization, not an extra capability level. It registers no indices
and no planner, so pulling does not construct the outgoing model. The installed structural indices are restored
lazily on the next indexed open; `inspect doctor` can report `indices.unbuilt` immediately after installation.

## Consequences

- Semantic ranking is unavailable until this machine can run the declared model and pass the reference probe.
- A local hashing index costs disk space and build time, but it can be discarded without changing the brain.
- Changing the shared model identity or revision is a project decision and requires an explicit reindex.
- The chosen runtime and credentials remain local; publishing a brain does not distribute access to a provider.

## Rejected option

Treating every runtime name as the model identity would make the same vectors appear incompatible across gateways.
Publishing a hashing rebuild as a substitute would erase the meaning of a semantic layer, so it is confined to
retrieval.
