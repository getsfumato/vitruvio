"""The opened brain, and the one cache every operation shares.

Split out of ``BrainService`` so that the operations can live in :mod:`vitruvio.runtime.ops` without each of them
carrying its own idea of what "the brain" is. It holds the two pieces of state the service ever had -- the resolved
configuration, and one ``Brain`` per capability -- and nothing else.

**One session, shared by every operation.** This is the part that has to stay true. ``install`` clears the cache
after a pull, because the brain a ``plan_pull`` memoized describes the *old* head and answering from it afterwards
would report the state that was just replaced. That invalidation only works if every operation reads through the
same session, so:

    An operations object may hold the session. It may never hold a ``Brain``.

A cached ``Brain`` on an operations object is a copy the session cannot reach, which makes
:meth:`BrainSession.invalidate` a lie in exactly the case it exists for. There is a test for it.

**Not the only door to a brain, and deliberately not.** ``lifecycle.init`` and ``projects.add_brain`` call
``open_brain`` directly: the first with ``create=True``, the second over a *different* ``ResolvedConfig`` for the
brain being added. Neither is a brain this session owns, so neither belongs here -- a ``session.open(create=True)``
would only make it easy to write the version that caches a brain under the wrong configuration.
"""

from __future__ import annotations

from boltzmann.brain import Brain

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

        Called after an operation replaces the head from underneath a brain this session already handed out --
        ``install.pull`` is the one that does it. Everything opened before that describes the composition that was
        just replaced, and answering a later question from it would report the state the pull overwrote.
        """
        self._cache.clear()
