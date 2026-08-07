"""Index engines for the Boltzmann protocol: six kinds behind one ``Index`` seam.

The SDK ships the ``Index`` Protocol and no implementation, which is the gap this package fills. Five kinds are
deterministic functions of the blocks and can be rebuilt by any client; the vector index is the exception, because
rebuilding it needs a model, so it travels inside the published artifact.

``Index.build`` is a full rebuild on every commit *and* on every open, so incrementality is an internal
optimisation behind that contract rather than a different method. And every index reports its ``population``,
because an empty index does not announce itself: a planner consulting one gets no candidates and reports a
confident nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vitruvio.indices.base import OrdinalTable, VitruvioIndex
from vitruvio.indices.bitmap import BitmapIndex
from vitruvio.indices.btree import BTreeIndex
from vitruvio.indices.format import Header, IndexFormatError, IndexStaleError
from vitruvio.indices.graph import FederatedGraphView, GraphIndex
from vitruvio.indices.hash_map import HashMapIndex
from vitruvio.indices.indexset import ENGINES, REQUIRES_EMBEDDER, IndexSet
from vitruvio.indices.inverted import InvertedIndex
from vitruvio.indices.projection import (
    PROJECTION_ID,
    Edge,
    EdgeKind,
    Facet,
    IdentityKey,
    OrderedKey,
    Projection,
    fold,
    project,
)
from vitruvio.indices.queries import (
    BuildDelta,
    Capability,
    Combine,
    FacetClause,
    FacetQuery,
    Hit,
    IdQuery,
    Order,
    RangeQuery,
    Results,
    TermQuery,
    TraversalQuery,
    VectorQuery,
)
from vitruvio.indices.testing import MemoryContent, blob_id, block_id, content_over
from vitruvio.indices.text import Analysis, analyze, analyzer_id, query_groups, query_terms, tokenize
from vitruvio.indices.vector import CHUNKER_ID, IndexModelMismatchError, VectorIndex, chunk

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from typing import Any

    from boltzmann.blocks.memory_type import MemoryType
    from boltzmann.indices.base import Index

    from vitruvio.kernel import IndexSpec, ResolvedConfig

__all__ = [
    "CHUNKER_ID",
    "ENGINES",
    "REQUIRES_EMBEDDER",
    "PROJECTION_ID",
    "BTreeIndex",
    "BitmapIndex",
    "BuildDelta",
    "Capability",
    "Combine",
    "Edge",
    "EdgeKind",
    "Facet",
    "FacetClause",
    "FacetQuery",
    "Analysis",
    "FederatedGraphView",
    "GraphIndex",
    "HashMapIndex",
    "InvertedIndex",
    "Header",
    "Hit",
    "IdQuery",
    "IdentityKey",
    "IndexFormatError",
    "IndexSet",
    "IndexModelMismatchError",
    "IndexStaleError",
    "MemoryContent",
    "Order",
    "OrderedKey",
    "OrdinalTable",
    "Projection",
    "RangeQuery",
    "Results",
    "TermQuery",
    "TraversalQuery",
    "VectorQuery",
    "VectorIndex",
    "VitruvioIndex",
    "blob_id",
    "block_id",
    "build_index_set",
    "build_indices",
    "analyze",
    "chunk",
    "analyzer_id",
    "content_over",
    "fold",
    "project",
    "query_groups",
    "query_terms",
    "tokenize",
]


def build_index_set(
    specs: Sequence[IndexSpec],
    *,
    home: Path | None = None,
    config: ResolvedConfig | None = None,
) -> IndexSet:
    """
    Construct the declared indices as a set, keeping the ones this build cannot make visible.

    Args:
        specs (Sequence[IndexSpec]): What the configuration declared.
        home (Path | None): Where sidecars live, normally ``<brain>/.vitruvio/indices``.
        config (ResolvedConfig | None): Carries the embedder configuration a vector index will need.

    Returns:
        IndexSet: The set, with unsupported kinds recorded in ``unavailable``.
    """
    embedders: dict[str, Any] = {}
    cache_home: Path | None = None
    if config is not None:
        from vitruvio.embeddings import EmbedderUnavailableError
        from vitruvio.embeddings import resolve as resolve_embedder

        cache_home = config.derived / "embeddings"
        for name, spec in (("text", config.project.text_embedder), ("vision", config.project.vision_embedder)):
            if spec is None:
                continue
            try:
                embedders[name] = resolve_embedder(spec)
            except EmbedderUnavailableError:
                # Left out rather than substituted. A vector index built with a stand-in would carry a tag that lies
                # about where its vectors came from, and the tag is the only thing stopping a consumer from ranking on
                # noise. `from_specs` records the omission, so it is visible in `index list`.
                continue

    return IndexSet.from_specs(specs, home, embedders=embedders, cache_home=cache_home)


def build_indices(
    specs: Sequence[IndexSpec],
    *,
    home: Path,
    config: ResolvedConfig | None = None,
) -> dict[MemoryType, list[Index]]:
    """
    The mapping ``Brain(indices=...)`` takes.

    Args:
        specs (Sequence[IndexSpec]): What the configuration declared.
        home (Path): Where sidecars live.
        config (ResolvedConfig | None): For the embedder a vector index needs.

    Returns:
        dict[MemoryType, list[Index]]: Indices by module, in registration order.
    """
    return build_index_set(specs, home=home, config=config).as_brain_indices()
