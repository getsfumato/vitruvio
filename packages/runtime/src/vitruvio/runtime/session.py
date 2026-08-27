"""The opened brain, and the one cache every operation shares.

Split out of ``BrainService`` so that the operations can live in :mod:`vitruvio.runtime.ops` without each of them
carrying its own idea of what "the brain" is. It holds the two pieces of state the service ever had -- the resolved
configuration, and one ``Brain`` per capability -- and nothing else.

**One session, shared by every operation.** This is the part that has to stay true. Every WRITE operation executes
through :meth:`BrainSession.write`, which observes the durable head and clears the cache when it moves. That
invalidation only works if every operation reads through the same session, so:

    An operations object may hold the session. It may never hold a ``Brain``.

A cached ``Brain`` on an operations object is a copy the session cannot reach, which makes
:meth:`BrainSession.invalidate` a lie in exactly the case it exists for. There is a test for it.

**Not the only door to a brain, and deliberately not.** ``lifecycle.init`` and ``projects.add_brain`` call
``open_brain`` directly: the first with ``create=True``, the second over a *different* ``ResolvedConfig`` for the
brain being added. Neither is a brain this session owns, so neither belongs here -- a ``session.open(create=True)``
would only make it easy to write the version that caches a brain under the wrong configuration.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from boltzmann.brain import HEAD_POINTER, Brain

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime.assembly import Capability, open_brain
from vitruvio.runtime.mapping import translated


class BrainSession:
    """
    Which brain, opened how much, memoized once.

    Attributes:
        config (ResolvedConfig): Which brain, who as, under what policy.
    """

    def __init__(self, config: ResolvedConfig) -> None:
        """
        Hold a configuration without opening anything.

        No brain is opened here. Each operation opens at its own capability, so constructing a session is free and
        a read never pays for a write's machinery.

        Args:
            config (ResolvedConfig): The resolved configuration.
        """
        self.config = config
        self._cache: dict[Capability, Brain] = {}

    def brain(self, capability: Capability = Capability.INSPECT) -> Brain:
        """
        The opened brain, memoized per capability.

        Args:
            capability (Capability): How much to stand up.

        Returns:
            Brain: The brain.
        """
        if capability not in self._cache:
            with translated():
                self._cache[capability] = open_brain(self.config, capability)
        return self._cache[capability]

    def invalidate(self) -> None:
        """
        Forget every opened brain.

        Everything opened before a head change describes the composition that was just replaced, and answering a
        later question from it would report stale state. Callers normally use :meth:`write`, which decides whether
        invalidation is necessary from the durable pointer.
        """
        self._cache.clear()

    @contextmanager
    def write(self) -> Iterator[Brain]:
        """Execute with the WRITE brain and keep every cached capability coherent.

        The durable head pointer is the authority, rather than a result type or a caller-provided hint. That makes
        duplicate registrations and store-only writes free of unnecessary reopenings, while any operation that
        really advances the composition invalidates every view automatically. The comparison runs even when code
        after the pointer move raises, because a failed renderer must not leave a successful commit hidden behind a
        stale INSPECT or RETRIEVE cache.

        Yields:
            Brain: The session-owned WRITE-capability brain.
        """
        brain = self.brain(Capability.WRITE)
        before = brain.store.read_pointer(HEAD_POINTER)
        try:
            yield brain
        finally:
            if brain.store.read_pointer(HEAD_POINTER) != before:
                self.invalidate()
