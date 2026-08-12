"""Fragments: what one index contributes, and how the pieces are merged and persisted.

An index computes its statistics **during the pass it is already making**. ``Index.build`` is a full rebuild on
every commit, so it walks every block anyway; counting document frequencies or facet cardinalities while doing so
costs nothing extra, and computing them in a second pass would double the most expensive part of a write.

That leaves one problem. Some statistics -- block count, average size, the leaf fingerprint -- belong to the
module rather than to any index. Two things make that safe:

* Registration order puts the hash-map index first, and it necessarily visits every block, so it owns the
  module-level fragment.
* :func:`merge` is **total and order-independent** anyway. If the module-level fragment is missing, the caller
  recomputes it. Ordering is an optimisation, never a correctness requirement -- a statistics layer that breaks
  when an index is registered in a different order is a statistics layer that will break.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vitruvio.stats.catalog import (
    ColumnStats,
    Freshness,
    GraphStats,
    ModuleStats,
    StatsVersion,
    TermStats,
    TimeStats,
    VectorStats,
)


def leaf_fingerprint(identities: Iterable[str]) -> str:
    """
    Fingerprint the set of block identities an index was actually handed.

    Sorted before hashing, so the fingerprint depends on the *set* and not on iteration order -- which the SDK
    does not promise and which would otherwise make every rebuild look like a change.

    Args:
        identities (Iterable[str]): The ``sha256:...`` identities indexed.

    Returns:
        str: A hex digest.
    """
    joined = "\n".join(sorted(identities)).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


class StatsFragment(BaseModel):
    """
    What one index measured.

    Every field is optional because every index measures a different slice: a bitmap index fills ``columns``, an
    inverted index fills ``terms``, and only the module-level fragment fills ``cardinality``.

    Attributes:
        kind (str): Which index produced this.
        memory_type (str): Which module it indexes.
        indexed (int): How many blocks it saw.
        fingerprint (str): The leaf fingerprint of what it saw.
        module_level (bool): Whether this fragment carries the module-wide numbers.
        cardinality (int): Blocks in the composition. Module-level only.
        resolvable_count (int): Blocks whose bytes could be read. Module-level only.
        average_block_bytes (float): Mean serialized size. Module-level only.
        columns (dict[str, ColumnStats]): Per-field distributions.
        time (dict[str, TimeStats]): Per-ordered-key distributions.
        terms (TermStats | None): The lexical view.
        graph (GraphStats | None): The relation view.
        vectors (dict[str, VectorStats]): Per-space vector views.
        model_tag (str | None): The embedding model, when this came from a vector index.
        built_at (str): RFC3339.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    memory_type: str
    indexed: int = 0
    fingerprint: str = ""
    module_level: bool = False
    cardinality: int = 0
    resolvable_count: int = 0
    average_block_bytes: float = 0.0
    columns: dict[str, ColumnStats] = Field(default_factory=dict)
    time: dict[str, TimeStats] = Field(default_factory=dict)
    terms: TermStats | None = None
    graph: GraphStats | None = None
    vectors: dict[str, VectorStats] = Field(default_factory=dict)
    model_tag: str | None = None
    built_at: str = ""


def merge(
    memory_type: str,
    fragments: Sequence[StatsFragment],
    *,
    root: str | None = None,
    fingerprint: str | None = None,
) -> ModuleStats:
    """
    Combine every index's fragment into one module view, and stamp it with the version it describes.

    Order-independent by construction: later fragments fill fields earlier ones left empty, and no field is
    contributed by two index kinds. Where the module-level numbers are missing entirely, the result carries zeros
    and ``freshness`` says absent -- which the planner reads as "estimate pessimistically" rather than as
    "nothing matches".

    Args:
        memory_type (str): Which module.
        fragments (Sequence[StatsFragment]): What the indices measured.
        root (str | None): The module's current Merkle root, which only the caller knows.
        fingerprint (str | None): The module's current leaf fingerprint. Compared against the fragments' own, so
            an index built against an older composition is detected.

    Returns:
        ModuleStats: The merged catalogue.
    """
    if not fragments:
        return ModuleStats(memory_type=memory_type, freshness=Freshness.absent())

    columns: dict[str, ColumnStats] = {}
    time: dict[str, TimeStats] = {}
    vectors: dict[str, VectorStats] = {}
    terms: TermStats | None = None
    graph: GraphStats | None = None
    module_level: StatsFragment | None = None
    model_tag: str | None = None
    built_at = ""

    for fragment in fragments:
        columns.update(fragment.columns)
        time.update(fragment.time)
        vectors.update(fragment.vectors)
        terms = fragment.terms or terms
        graph = fragment.graph or graph
        model_tag = fragment.model_tag or model_tag
        built_at = max(built_at, fragment.built_at)
        if fragment.module_level:
            module_level = fragment

    observed = fingerprint or (module_level.fingerprint if module_level else fragments[0].fingerprint)
    version = StatsVersion(
        root=root,
        leaf_fingerprint=module_level.fingerprint if module_level else fragments[0].fingerprint,
        built_at=built_at,
        index_kinds=tuple(sorted({fragment.kind for fragment in fragments})),
        model_tag=model_tag,
    )
    freshness = version.freshness_against(root, observed) if root is not None else Freshness.fresh()

    return ModuleStats(
        memory_type=memory_type,
        version=version,
        freshness=freshness,
        cardinality=module_level.cardinality if module_level else 0,
        resolvable_count=module_level.resolvable_count if module_level else 0,
        average_block_bytes=module_level.average_block_bytes if module_level else 0.0,
        columns=columns,
        time=time,
        terms=terms or TermStats(),
        graph=graph or GraphStats(),
        vectors=vectors,
    )


def _canonical(payload: dict[str, Any]) -> bytes:
    """Sorted-key JSON, so a stats file is diffable and a golden test over one is stable."""
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")


def save(stats: ModuleStats, path: Path) -> Path:
    """
    Persist a merged catalogue, atomically.

    Args:
        stats (ModuleStats): What to write.
        path (Path): Where. Parent directories are created.

    Returns:
        Path: The file written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(_canonical(stats.model_dump(mode="json")))
    temporary.replace(path)
    return path


def load(path: Path) -> ModuleStats | None:
    """
    Read a persisted catalogue, tolerating a corrupt or older one.

    A malformed statistics file is never fatal: statistics are derived, so the answer is to rebuild rather than
    to fail. Returning ``None`` makes the caller estimate pessimistically, which is the safe direction.

    Args:
        path (Path): The file.

    Returns:
        ModuleStats | None: The catalogue, or ``None`` if it could not be read.
    """
    if not path.is_file():
        return None
    try:
        return ModuleStats.model_validate_json(path.read_bytes())
    except Exception:
        return None
