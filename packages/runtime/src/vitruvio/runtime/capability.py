"""How much of a brain an operation needs standing up.

Its own module, importing nothing, because two things need it and only one of them may be expensive.
:mod:`vitruvio.runtime.assembly` builds brains and so imports the SDK; :mod:`vitruvio.runtime.operation_catalogue`
only *declares* which capability each operation asks for, and is read by the facade generator, the conformance
tests and the documentation. Putting the enum beside the builder would make declaring a fact cost the same as
performing it.
"""

from __future__ import annotations

from enum import IntEnum


class Capability(IntEnum):
    """
    How much of a brain an operation needs standing up.

    Ordered, so that ``capability >= Capability.RETRIEVE`` is a meaningful test.
    """

    INSPECT = 0
    """Read the pointer, the snapshot, the modules. No index, no model."""
    BROWSE = 1
    """Read structural relationships through provenance's rebuildable hash-map index."""
    RETRIEVE = 2
    """Query. Registers the configured indices; the embedder is still resolved lazily."""
    WRITE = 3
    """Commit, drop, publish. Adds the retention policy and the validation gate."""


__all__ = ["Capability"]
