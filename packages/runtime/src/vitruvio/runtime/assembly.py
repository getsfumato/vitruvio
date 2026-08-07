"""Building a ``Brain`` from a resolved configuration, gated by what the command actually needs.

This module exists because of one line in the SDK: ``Brain.__init__`` calls ``self.rebuild_indices()``
(``brain.py:225``). Every construction of a brain rebuilds every registered index over the whole composition.
Register a vector index and ``vitruvio brain state`` -- a command that reads a pointer file -- constructs an
embedder, which imports sentence-transformers, which imports torch.

So indices are registered per *capability*, and the embedder is resolved lazily even then:

* ``INSPECT`` registers nothing. ``brain state``, ``brain verify``, ``inspect *`` and ``dist manifest`` never
  touch an index or a model.
* ``RETRIEVE`` registers the configured indices. A query needs them.
* ``WRITE`` adds the retention policy and the validation gate.

The capability is declared by each operation rather than guessed here, because "does this command need an
index" is a fact about the command.
"""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING

from boltzmann.brain import Brain
from boltzmann.store.oci_layout import OciLayoutStore

from vitruvio.ingest import bootstrap as bootstrap_pipelines
from vitruvio.kernel import BrainNotFoundError, is_layout

if TYPE_CHECKING:
    from pathlib import Path

    from boltzmann.blocks.memory_type import MemoryType
    from boltzmann.blocks.provenance import Actor
    from boltzmann.indices.base import Index
    from boltzmann.query.planner import QueryPlanner

    from vitruvio.kernel import ResolvedConfig


class Capability(IntEnum):
    """
    How much of a brain an operation needs standing up.

    Ordered, so that ``capability >= Capability.RETRIEVE`` is a meaningful test.
    """

    INSPECT = 0
    """Read the pointer, the snapshot, the modules. No index, no model."""
    RETRIEVE = 1
    """Query. Registers the configured indices; the embedder is still resolved lazily."""
    WRITE = 2
    """Commit, drop, publish. Adds the retention policy and the validation gate."""


def build_indices(config: ResolvedConfig, capability: Capability) -> dict[MemoryType, list[Index]] | None:
    """
    Construct the index set this capability calls for.

    Returns ``None`` for ``INSPECT``, which is not the same as an empty mapping: it is what tells the SDK there
    is nothing to rebuild, so opening a brain stays a pointer read.

    Args:
        config (ResolvedConfig): The resolved configuration, naming which indices and which embedders.
        capability (Capability): What the operation needs.

    Returns:
        dict[MemoryType, list[Index]] | None: The index set, or ``None`` when none should be registered.
    """
    if capability < Capability.RETRIEVE:
        return None

    # Imported here, not at module scope: this is the line that pulls in usearch and the index engines, and an
    # INSPECT-capability command must not pay for it.
    from vitruvio.runtime.indexset import index_set

    return index_set(config)


def open_brain(config: ResolvedConfig, capability: Capability = Capability.INSPECT, *, create: bool = False) -> Brain:
    """
    Open the configured brain at the given capability.

    Args:
        config (ResolvedConfig): Which brain, who as, under what policy.
        capability (Capability): How much to stand up.
        create (bool): Whether to create the layout if it is absent. Only ``brain init`` passes ``True``.

    Returns:
        Brain: The opened brain, at whichever snapshot the directory was left on.

    Raises:
        BrainNotFoundError: If the path is not a layout and ``create`` is false.
        ActorUnknownError: If a write capability was requested with no actor identity resolvable.
    """
    path: Path = config.brain
    if not create and not is_layout(path):
        detail = "does not exist" if not path.exists() else "is not an OCI layout"
        raise BrainNotFoundError(
            f"{path} {detail}, so it is not a brain",
            hint=f"run `vitruvio brain init {path}` to create one",
        )

    # `actor()` raises when nothing resolves. For a read that is too strict -- inspecting someone else's brain
    # is legitimate and attributes nothing -- so a placeholder stands in, and it is never written anywhere: no
    # INSPECT or RETRIEVE operation reaches a code path that records provenance.
    actor = config.actor() if capability >= Capability.WRITE else _reader_actor(config)

    # Registered here rather than as an import side effect. Which pipelines exist decides whether a normalized view
    # can be produced *and* whether an existing one can be reproduced, so the set must not depend on which modules
    # happened to be imported first -- that would make reproducibility a function of import order. Idempotent, and
    # cheap: six singletons into a dict.
    bootstrap_pipelines()

    return Brain(
        OciLayoutStore(path, create=create),
        actor=actor,
        policy=config.policy(),
        planner=_planner(config, capability),
        indices=build_indices(config, capability),
    )


def _reader_actor(config: ResolvedConfig) -> Actor:
    """
    An actor for read-only work, falling back to an explicit placeholder.

    Args:
        config (ResolvedConfig): The resolved configuration.

    Returns:
        Actor: The configured actor, or a ``reader`` placeholder whose kind says what it is.
    """
    from boltzmann.blocks.provenance import Actor, ActorKind

    spec = config.project.actor
    if spec.id:
        return Actor(id=spec.id, kind=spec.kind, name=spec.name)
    return Actor(id="vitruvio:reader", kind=ActorKind.SERVICE, name="read-only session")


def _planner(config: ResolvedConfig, capability: Capability) -> QueryPlanner | None:
    """
    The query planner, when the operation retrieves.

    Args:
        config (ResolvedConfig): Carries the planner's knobs.
        capability (Capability): What the operation needs.

    Returns:
        QueryPlanner | None: A planner, or ``None`` -- in which case the SDK falls back to its linear scan,
        which is correct and slow, and is exactly what an unplanned read should get.
    """
    if capability < Capability.RETRIEVE:
        return None

    from vitruvio.planner import build_planner
    from vitruvio.stats import load

    # Loaded from disk rather than recomputed: the catalogue is written by `index build`, and a query should not pay
    # to re-derive it. A missing or stale entry is not an error -- the planner reads freshness and estimates
    # pessimistically -- which is why `load` returns None rather than raising.
    statistics = {}
    for path in sorted((config.derived / "stats").glob("*.json")):
        loaded = load(path)
        if loaded is not None:
            statistics[loaded.memory_type] = loaded

    return build_planner(config.project.planner, statistics=statistics)
