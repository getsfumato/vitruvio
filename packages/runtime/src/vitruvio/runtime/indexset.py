"""Turning the declared index set in ``vitruvio.toml`` into instances the SDK can register.

The wiring lives in the runtime rather than in ``vitruvio.indices`` because it needs a
:class:`~vitruvio.kernel.ResolvedConfig`, and an index engine that knows about configuration is an index
engine that cannot be tested without one.

This is also the single point where a heavy import happens. ``vitruvio.runtime.assembly`` imports this module
inside a function, so an ``INSPECT``-capability command never loads an index engine, and never loads torch.

It owns **where the indices are**, too, which is a smaller fact than it sounds and was previously spread out.
``config.derived / "indices"`` was written literally in six places across two unrelated domains: the index
operations, which build and verify through ``IndexSet``, and ``lifecycle.info``, which only wants to know whether a
vector layer is on disk and reads the sidecar headers to find out. Those two share no API -- only the directory --
so neither is the right place to put it, and having one call the other would be an edge for the sake of a path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.indices.base import Index

from vitruvio.kernel import ResolvedConfig

if TYPE_CHECKING:
    from boltzmann.brain import Brain
    from boltzmann.module.module import Module

    from vitruvio.indices import IndexSet
    from vitruvio.indices.format import Header
    from vitruvio.indices.vector import VectorIndex


def indices_home(config: ResolvedConfig) -> Path:
    """
    Where this brain's index sidecars live.

    Args:
        config (ResolvedConfig): The resolved configuration.

    Returns:
        Path: The directory. Not created here -- the index set creates it when it flushes, and a read must not
        bring a directory into being as a side effect of asking about it.
    """
    return config.derived / "indices"


def sidecar_headers(config: ResolvedConfig) -> list[tuple[Path, Header]]:
    """
    Every index sidecar on disk, with its header and nothing else read.

    No embedder is constructed and no model is loaded: the header carries the kind, the module, the root it was built
    against, the population and the model tag, which is everything needed to answer "would a publish include this",
    "does this describe the installed composition" and "was this built with the configured embedder". That is why
    this reads files rather than going through :func:`index_set` -- building the set to answer a question about it
    would construct the engine the question is trying to avoid, and would refuse the very sidecar a mismatch check
    wants to report.

    A file that is not a readable sidecar is skipped rather than raised on: the question is about what is there.

    Args:
        config (ResolvedConfig): The resolved configuration.

    Returns:
        list[tuple[Path, Header]]: Each sidecar's path and header, in path order.
    """
    from vitruvio.indices import format as envelope

    found: list[tuple[Path, Header]] = []
    for path in sorted(indices_home(config).glob(f"*{envelope.SUFFIX}")):
        try:
            read = envelope.read(path)
        except envelope.IndexFormatError:
            continue
        if read is not None:
            found.append((path, read[0]))
    return found


def travelling_on_disk(config: ResolvedConfig) -> list[str]:
    """
    Which modules have a non-empty vector index persisted, by reading the sidecar headers.

    Args:
        config (ResolvedConfig): The resolved configuration.

    Returns:
        list[str]: Memory types with a non-empty vector index on disk.
    """
    return [
        header.memory_type for _, header in sidecar_headers(config) if header.kind == "vector" and header.population
    ]


def index_set(config: ResolvedConfig, *, local_query: bool = False) -> dict[MemoryType, list[Index]]:
    """
    Build every index the configuration declares, grouped by memory type.

    Order within a memory type matters and is not incidental: the hash-map index is registered first because
    it necessarily visits every block, so it is the one that can compute the module-level statistics fragment
    during a pass it was making anyway. The SDK's write path iterates in registration order.

    Args:
        config (ResolvedConfig): Names which indices, on which modules, with which embedder.

    Returns:
        dict[MemoryType, list[Index]]: The index set, ready for ``Brain(indices=...)``.
    """
    return create_index_set(config, local_query=local_query).as_brain_indices()


def create_index_set(config: ResolvedConfig, *, local_query: bool = False) -> IndexSet:
    """Resolve runtimes here so engines never decide whether a local fallback may replace shared vectors.

    Query vectors use a separate namespace, including validated copies of a published layer. A read can therefore
    refresh its fallback or migrate an old tag without changing the canonical sidecar used for publication.
    """
    import hashlib

    from vitruvio.embeddings import EmbedderUnavailableError, resolve
    from vitruvio.indices import IndexSet
    from vitruvio.kernel import DEFAULT_TEXT_EMBEDDER

    text = config.text_embedder if local_query else config.project.text_embedder
    embedders = {}
    for name, spec in (("text", text), ("vision", config.project.vision_embedder)):
        if spec is None:
            continue
        try:
            candidate = resolve(spec)
        except EmbedderUnavailableError:
            candidate = None
        if local_query and name == "text" and (candidate is None or not candidate.available):
            candidate = resolve(DEFAULT_TEXT_EMBEDDER)
        if candidate is not None:
            embedders[name] = candidate
    home = indices_home(config)
    vector_homes = (
        {
            name: home / "local" / hashlib.sha256(embedder.tag.render().encode()).hexdigest()[:16]
            for name, embedder in embedders.items()
        }
        if local_query
        else None
    )
    return IndexSet.from_specs(
        config.project.indices,
        home,
        embedders=embedders,
        cache_home=config.derived / "embeddings",
        vector_homes=vector_homes,
    )


def prepare_query_indices(brain: Brain, config: ResolvedConfig) -> None:
    """Restore published vectors or refresh a local fallback without writing a canonical sidecar on a read.

    The snapshot's digest is authoritative for a travelling layer; a manifest annotation alone is not. Local
    fallback freshness follows the module root so a second query after a commit sees the new blocks.
    """
    from vitruvio.indices import VectorIndex

    snapshot = brain.snapshot()
    for memory_type in snapshot.installed:
        module = brain.module(memory_type)
        reference = snapshot.modules[memory_type]
        for vector in brain.indices.get(memory_type, ()):
            if not isinstance(vector, VectorIndex):
                continue
            if not vector.population:
                canonical = indices_home(config) / f"{memory_type.value}.vector.vidx"
                if canonical.is_file():
                    _restore_query_vectors(vector, canonical.read_bytes(), module)
                if (
                    not vector.population
                    and reference.index_digest
                    and brain.store.is_resolvable(reference.index_digest)
                ):
                    data = brain.store.get_bytes(reference.index_digest)
                    _restore_query_vectors(vector, data, module, published_model=reference.embedding_model)
            fallback = vector.embedder.tag.is_fallback and (
                not config.project.text_embedder.is_fallback
                or (reference.embedding_model is not None and reference.embedding_model != vector.expected_model_tag)
            )
            if fallback and (not vector.population or vector.bound_root != str(module.root)):
                blocks = [module.get(identity) for identity in module.block_ids if module.store.is_resolvable(identity)]
                vector.build(blocks, module.store)
                vector.bind(str(module.root))


def _restore_query_vectors(
    vector: VectorIndex, data: bytes, module: Module, *, published_model: str | None = None
) -> None:
    """A renamed legacy tag needs passage evidence; an unchanged tag retains its existing compatibility contract."""
    from vitruvio.indices import format as envelope
    from vitruvio.indices.vector import IndexModelMismatchError

    try:
        header, _ = envelope.decode(data)
        if published_model is not None and header.model_tag != published_model:
            return
        if header.merkle_root is not None and header.merkle_root != str(module.root):
            return
        try:
            vector.load(data)
        except IndexModelMismatchError:
            blocks = [module.get(identity) for identity in module.block_ids if module.store.is_resolvable(identity)]
            vector.restore_legacy(data, blocks, module.store)
    except envelope.IndexFormatError:
        return
