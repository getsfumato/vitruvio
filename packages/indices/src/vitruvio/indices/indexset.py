"""Assembling the declared indices, and reporting on them.

Two things happen here that do not belong in any single index.

**Registration order.** The hash-map index goes first on every module, because it necessarily visits every block
and so owns the module-level statistics fragment for free. The SDK's write path iterates in registration order, so
this is all it takes. The merge in :mod:`vitruvio.stats` is order-independent regardless, so this is an
optimisation rather than a correctness requirement -- a statistics layer that broke on a different order would be
one to fix, not to work around.

**Binding to a root.** ``Index.build`` is not given the module's ``MerkleRoot``, so an index cannot stamp itself
with the version it describes. The caller holds the module and can, which is what :meth:`IndexSet.bind` is for --
and it is what lets a later open tell a current index from a stale one without re-reading every block.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from boltzmann.indices.base import IndexKind

from vitruvio.indices.bitmap import BitmapIndex
from vitruvio.indices.btree import BTreeIndex
from vitruvio.indices.graph import GraphIndex
from vitruvio.indices.hash_map import HashMapIndex
from vitruvio.indices.inverted import InvertedIndex
from vitruvio.indices.vector import VectorIndex
from vitruvio.stats import ModuleStats, leaf_fingerprint, merge

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from boltzmann.blocks.memory_type import MemoryType
    from boltzmann.indices.base import Index
    from boltzmann.module.module import Module

    from vitruvio.indices.base import VitruvioIndex
    from vitruvio.indices.queries import Capability
    from vitruvio.kernel import IndexSpec

# Which kinds this build can construct. A declared kind that is absent here is reported rather than silently
# skipped: an index the user asked for and did not get is exactly the thing that must not pass unnoticed.
ENGINES: dict[IndexKind, type[VitruvioIndex]] = {
    IndexKind.HASH_MAP: HashMapIndex,
    IndexKind.BITMAP: BitmapIndex,
    IndexKind.BTREE: BTreeIndex,
    IndexKind.INVERTED: InvertedIndex,
    IndexKind.GRAPH: GraphIndex,
}

REQUIRES_EMBEDDER = frozenset({IndexKind.VECTOR})
"""Kinds that cannot be constructed without a model. Reported rather than skipped when one is unavailable."""

ORDER = (
    IndexKind.HASH_MAP,
    IndexKind.BITMAP,
    IndexKind.BTREE,
    IndexKind.INVERTED,
    IndexKind.GRAPH,
    IndexKind.VECTOR,
)
"""Registration order. Hash map first, so it owns the module-level statistics; vector last, because it is the
expensive one and anything that fails earlier should fail before a model is touched."""


class IndexSet:
    """
    Every index vitruvio holds for one brain, grouped by module.

    Attributes:
        home (Path | None): Where the sidecars live. ``None`` keeps everything in memory.
        unavailable (dict[str, str]): Declared kinds this build cannot construct, and why.
    """

    def __init__(self, home: Path | None = None) -> None:
        """
        Build an empty set.

        Args:
            home (Path | None): Directory for the sidecar files, normally ``<brain>/.vitruvio/indices``.
        """
        self.home = home
        self.unavailable: dict[str, str] = {}
        self._indices: dict[MemoryType, dict[IndexKind, VitruvioIndex]] = {}

    @classmethod
    def from_specs(
        cls,
        specs: Sequence[IndexSpec],
        home: Path | None = None,
        *,
        embedders: Mapping[str, Any] | None = None,
        cache_home: Path | None = None,
    ) -> IndexSet:
        """
        Construct the indices a configuration declares.

        Args:
            specs (Sequence[IndexSpec]): What the configuration asked for.
            home (Path | None): Where the sidecars live.
            embedders (Mapping[str, Any] | None): Named embedders, keyed ``text`` and ``vision``, for the vector
                engine. A vector index declared with no embedder available is recorded in ``unavailable`` rather
                than constructed with a substitute -- a substitute would produce vectors whose tag lies.
            cache_home (Path | None): Where the embedding cache lives. Without it the cache is in-memory, which is
                right for a test and wrong for a brain: ``build`` runs on every commit, so an unpersisted cache means
                re-embedding everything to add one block.

        Returns:
            IndexSet: The constructed set, with anything unavailable recorded rather than dropped.
        """
        built = cls(home)
        for spec in specs:
            name = f"{spec.memory_type.value}.{spec.kind.value}"
            engine = ENGINES.get(spec.kind)
            if engine is None and spec.kind not in REQUIRES_EMBEDDER:
                built.unavailable[name] = f"the {spec.kind.value} engine is not implemented in this build"
                continue

            if spec.kind in REQUIRES_EMBEDDER:
                embedder = (embedders or {}).get(spec.embedder or "text")
                if embedder is None:
                    built.unavailable[name] = (
                        f"no {spec.embedder or 'text'} embedder is configured, so a vector index cannot be built"
                    )
                    continue
                cache = None
                if cache_home is not None:
                    from vitruvio.embeddings import EmbeddingCache

                    cache = EmbeddingCache.for_model(cache_home, embedder.tag.render())
                built.add(VectorIndex(spec.memory_type, home, embedder=embedder, cache=cache))
                continue

            assert engine is not None
            built.add(engine(spec.memory_type, home))
        return built

    def add(self, index: VitruvioIndex) -> None:
        """
        Register one index.

        Args:
            index (VitruvioIndex): The index.
        """
        self._indices.setdefault(index.memory_type, {})[index.KIND] = index

    def get(self, memory_type: MemoryType, kind: IndexKind) -> VitruvioIndex | None:
        """
        One index, or ``None`` if it was not registered.

        Args:
            memory_type (MemoryType): Which module.
            kind (IndexKind): Which kind.

        Returns:
            VitruvioIndex | None: The index.
        """
        return self._indices.get(memory_type, {}).get(kind)

    def for_module(self, memory_type: MemoryType) -> list[VitruvioIndex]:
        """
        Every index on one module, in registration order.

        Args:
            memory_type (MemoryType): Which module.

        Returns:
            list[VitruvioIndex]: The indices, hash map first.
        """
        held = self._indices.get(memory_type, {})
        return [held[kind] for kind in ORDER if kind in held]

    def as_brain_indices(self) -> dict[MemoryType, list[Index]]:
        """
        The mapping ``Brain(indices=...)`` takes.

        Returns:
            dict[MemoryType, list[Index]]: Indices by module, in registration order.
        """
        return {memory_type: list(self.for_module(memory_type)) for memory_type in self._indices}

    def bind(self, modules: dict[MemoryType, Module]) -> None:
        """
        Tell each index which module root it describes.

        Args:
            modules (dict[MemoryType, Module]): The brain's modules, which carry the roots ``build`` was not given.
        """
        for memory_type, module in modules.items():
            for index in self.for_module(memory_type):
                index.bind(str(module.root))

    def statistics(self, modules: dict[MemoryType, Module] | None = None) -> dict[MemoryType, ModuleStats]:
        """
        Merge every index's fragment into one catalogue per module.

        Args:
            modules (dict[MemoryType, Module] | None): The modules, for stamping the root and computing the
                current leaf fingerprint. Without them the result is unstamped, and the planner treats an
                unstamped catalogue pessimistically.

        Returns:
            dict[MemoryType, ModuleStats]: The merged statistics.
        """
        catalogue: dict[MemoryType, ModuleStats] = {}
        for memory_type in self._indices:
            indices = self.for_module(memory_type)
            if not indices:
                continue
            root: str | None = None
            fingerprint: str | None = None
            module = (modules or {}).get(memory_type)
            if module is not None:
                root = str(module.root)
                readable = [str(identity) for identity in module.block_ids if module.store.is_resolvable(identity)]
                fingerprint = leaf_fingerprint(readable)
            catalogue[memory_type] = merge(
                memory_type.value,
                [index.fragment() for index in indices],
                root=root,
                fingerprint=fingerprint,
            )
        return catalogue

    def capabilities(self, modules: dict[MemoryType, Module] | None = None) -> dict[MemoryType, list[Capability]]:
        """
        What each index can currently answer.

        The planner reads this rather than assuming an index is usable. An index that is registered but empty is
        reported as ``empty`` and excluded, because consulting one produces no candidates and a confident nothing.

        Args:
            modules (dict[MemoryType, Module] | None): For checking each index's root binding.

        Returns:
            dict[MemoryType, list[Capability]]: Capabilities by module.
        """
        report: dict[MemoryType, list[Capability]] = {}
        for memory_type in self._indices:
            module = (modules or {}).get(memory_type)
            root = str(module.root) if module is not None else None
            report[memory_type] = [index.capability(root=root) for index in self.for_module(memory_type)]
        return report

    def flush(self) -> list[Path]:
        """
        Persist every index that has somewhere to go.

        Returns:
            list[Path]: The files written.
        """
        written: list[Path] = []
        for memory_type in self._indices:
            for index in self.for_module(memory_type):
                path = index.flush()
                if path is not None:
                    written.append(path)
        return written

    def report(self) -> list[dict[str, Any]]:
        """
        A row per index, for ``vitruvio index list``.

        Returns:
            list[dict[str, Any]]: What each index is, how much it holds, and where it lives.
        """
        rows: list[dict[str, Any]] = []
        for memory_type in sorted(self._indices, key=lambda item: item.value):
            for index in self.for_module(memory_type):
                path = index.path
                rows.append(
                    {
                        "memory_type": memory_type.value,
                        "kind": index.KIND.value,
                        "rebuildable": index.REBUILDABLE,
                        "population": index.population,
                        "model_tag": index.model_tag,
                        "bound_root": index.bound_root,
                        "engine": index.header().engine,
                        "path": str(path) if path else None,
                        "size_bytes": path.stat().st_size if path and path.is_file() else 0,
                    }
                )
        return rows

    def collect(self, keep: set[str]) -> list[Path]:
        """
        Delete sidecar files that no longer belong to a declared index.

        Args:
            keep (set[str]): ``<memory_type>.<kind>`` names to preserve.

        Returns:
            list[Path]: The files removed.
        """
        if self.home is None or not self.home.is_dir():
            return []
        removed: list[Path] = []
        for path in sorted(self.home.glob(f"*{'.vidx'}")):
            if path.name.removesuffix(".vidx") not in keep:
                path.unlink()
                removed.append(path)
        return removed
