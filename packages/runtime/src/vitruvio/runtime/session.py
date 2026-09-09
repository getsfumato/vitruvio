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

**One session is one coherence scope, and it may be driven from several threads.** The rule above says what the
session forgets; it says nothing about who is still reading. A CLI command owns a session for its own duration and
never meets itself, but the TUI holds one for the whole run and drives it from worker threads whose exclusivity
groups do not span reads and writes -- classifying a source while the detail pane reads is two threads on this
cache. So:

* the cache is guarded, because ``open_brain`` rebuilds every registered index and two threads racing it produced
  a second brain that the session never held and therefore could never invalidate;
* invalidation bumps a generation, and :meth:`pinned` refuses a read whose composition was replaced while it ran.
  A reference already handed out cannot be revoked, but its answer can be refused;
* :meth:`write` admits one writer and refuses the second, where "one writer" is a task when a loop is running
  and a thread otherwise. Queueing is the wrong answer for operations that block on a registry: a caller parked
  behind a push has no way to learn it is waiting, and a refusal it can retry does tell it.

**Not the only door to a brain, and deliberately not.** ``lifecycle.init`` and ``projects.add_brain`` call
``open_brain`` directly: the first with ``create=True``, the second over a *different* ``ResolvedConfig`` for the
brain being added. Neither is a brain this session owns, so neither belongs here -- a ``session.open(create=True)``
would only make it easy to write the version that caches a brain under the wrong configuration.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from boltzmann.brain import HEAD_POINTER, Brain

from vitruvio.kernel import ResolvedConfig, SessionBusyError, StaleBrainError
from vitruvio.runtime.assembly import Capability, open_brain
from vitruvio.runtime.mapping import translated


def _writer() -> tuple[int, int]:
    """
    What counts as one writer: the running task, or the thread when there is none.

    A thread alone is the wrong unit. ``push_async`` and ``pull_async`` hold the write across a registry round
    trip, and two tasks awaiting on one event loop share a thread identity -- so a thread-reentrant lock would
    read the second as a nested call and admit it. ``sys.modules`` rather than an import: ``test_import_cost``
    keeps ``asyncio`` out of ``import vitruvio.runtime``, and a loop cannot be running if it was never imported.

    Returns:
        tuple[int, int]: The thread, and the task on it when one is running.
    """
    asyncio = sys.modules.get("asyncio")
    if asyncio is not None:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is not None:
            return (threading.get_ident(), id(task))
    return (threading.get_ident(), 0)


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
        self._generation = 0
        self._lock = threading.RLock()
        # Reentrancy is tracked by owner rather than delegated to an RLock, because the owner has to be the task
        # and not the thread: `migrate` and the reconciliation flows nest a write, and two async pushes must not.
        self._writing = threading.Lock()
        self._writer: tuple[int, int] | None = None
        self._depth = 0

    def brain(self, capability: Capability = Capability.INSPECT) -> Brain:
        """
        The opened brain, memoized per capability.

        Args:
            capability (Capability): How much to stand up.

        Returns:
            Brain: The brain.
        """
        cached = self._cache.get(capability)
        if cached is not None:
            return cached
        with self._lock:
            # Re-read under the lock: the thread that waited here may have been waiting for this very open.
            opened = self._cache.get(capability)
            if opened is None:
                with translated():
                    opened = open_brain(self.config, capability)
                self._cache[capability] = opened
            return opened

    def invalidate(self) -> None:
        """
        Forget every opened brain.

        Everything opened before a head change describes the composition that was just replaced, and answering a
        later question from it would report stale state. Callers normally use :meth:`write`, which decides whether
        invalidation is necessary from the durable pointer.
        """
        with self._lock:
            self._cache.clear()
            self._generation += 1

    @contextmanager
    def pinned(self, capability: Capability = Capability.INSPECT) -> Iterator[Brain]:
        """Execute a read and refuse its result if the composition was replaced while it ran.

        The read counterpart of :meth:`write`, and the answer to the one thing :meth:`invalidate` cannot do. It
        matters where a read spans something slow: the distribution operations hold a brain across a registry
        round trip, and until this existed a concurrent pull could make ``plan_pull`` report an impact computed
        against a composition that no longer existed.

        Args:
            capability (Capability): How much to stand up.

        Yields:
            Brain: The session-owned brain at that capability.

        Raises:
            StaleBrainError: The session was invalidated while the body ran.
        """
        with self._lock:
            brain = self.brain(capability)
            generation = self._generation
        yield brain
        if self._generation != generation:
            raise StaleBrainError(
                "the brain was replaced while this read was running",
                hint="run it again; the result would have described a composition that no longer exists",
            )

    @contextmanager
    def write(self) -> Iterator[Brain]:
        """Execute with the WRITE brain and keep every cached capability coherent.

        The durable head pointer is the authority, rather than a result type or a caller-provided hint. That makes
        duplicate registrations and store-only writes free of unnecessary reopenings, while any operation that
        really advances the composition invalidates every view automatically. The comparison runs even when code
        after the pointer move raises, because a failed renderer must not leave a successful commit hidden behind a
        stale INSPECT or RETRIEVE cache.

        Only one writer at a time, and the second is refused rather than queued -- which is also what makes the
        before/after comparison mean anything, since two writers sharing this brain would each measure a pointer
        the other had already moved.

        Yields:
            Brain: The session-owned WRITE-capability brain.

        Raises:
            SessionBusyError: Another task or thread is inside this session's write.
        """
        me = _writer()
        with self._writing:
            if self._writer not in (None, me):
                raise SessionBusyError(
                    "another write is already running on this brain",
                    hint="wait for it to finish and run this again",
                )
            self._writer = me
            self._depth += 1
        try:
            brain = self.brain(Capability.WRITE)
            before = brain.store.read_pointer(HEAD_POINTER)
            try:
                yield brain
            finally:
                if brain.store.read_pointer(HEAD_POINTER) != before:
                    self.invalidate()
        finally:
            # In a `finally` so that a cancelled task releases: `CancelledError` arrives at the `yield` above.
            with self._writing:
                self._depth -= 1
                if self._depth == 0:
                    self._writer = None
