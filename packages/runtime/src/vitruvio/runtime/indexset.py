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
    from vitruvio.indices.format import Header


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

    return build_indices(config.project.indices, home=indices_home(config), config=config)
