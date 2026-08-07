"""Turning the declared index set in ``vitruvio.toml`` into instances the SDK can register.

The wiring lives in the runtime rather than in ``vitruvio.indices`` because it needs a
:class:`~vitruvio.kernel.ResolvedConfig`, and an index engine that knows about configuration is an index
engine that cannot be tested without one.

This is also the single point where a heavy import happens. ``vitruvio.runtime.assembly`` imports this module
inside a function, so an ``INSPECT``-capability command never loads an index engine, and never loads torch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from boltzmann.blocks.memory_type import MemoryType
    from boltzmann.indices.base import Index

    from vitruvio.kernel import ResolvedConfig


def index_set(config: ResolvedConfig) -> dict[MemoryType, list[Index]]:
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
    from vitruvio.indices import build_indices

    return build_indices(config.project.indices, home=config.derived / "indices", config=config)
