"""The shared brain cache, and the property that makes invalidating it mean something.

`BrainSession` exists so that the operations can be split across modules without each of them deciding for itself
what "the brain" is. Its whole value is that there is exactly one cache, so these tests are about identity: the same
capability hands back the same object, a different capability does not, and invalidating drops all of them.

The concurrency tests are here rather than beside the TUI because that is where the shared session lives, but the
TUI is what reaches them: it holds one session for the whole run and drives it from worker threads whose
exclusivity groups do not span reads and writes.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest

from vitruvio.kernel import ResolvedConfig, SessionBusyError, StaleBrainError
from vitruvio.runtime import BrainService
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.session import BrainSession


@pytest.fixture
def opened(config: ResolvedConfig) -> BrainSession:
    """A session over an initialised brain."""
    BrainService(config).init()
    return BrainSession(config)


class TestMemoization:
    def test_the_same_capability_returns_the_same_brain(self, opened: BrainSession) -> None:
        """Identity, not equality: the point is that the second call opened nothing."""
        assert opened.brain(Capability.INSPECT) is opened.brain(Capability.INSPECT)

    def test_a_higher_capability_is_a_different_brain(self, opened: BrainSession) -> None:
        """An INSPECT brain registers no index, so it cannot stand in for a RETRIEVE one."""
        assert opened.brain(Capability.INSPECT) is not opened.brain(Capability.RETRIEVE)

    def test_constructing_a_session_opens_nothing(self, config: ResolvedConfig) -> None:
        """A read must not pay for a write's machinery, which starts with not opening a brain to find out."""
        assert BrainSession(config)._cache == {}


def test_operation_modules_cannot_open_a_write_brain_directly() -> None:
    """A direct WRITE open bypasses the session seam that owns coherence."""
    operations = Path(__file__).parents[1] / "src" / "vitruvio" / "runtime" / "ops"
    bypasses = []
    for path in operations.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.Attribute) and node.attr == "WRITE" for node in ast.walk(tree)):
            bypasses.append(path.name)
    assert bypasses == [], f"WRITE brains must be opened through BrainSession.write(): {bypasses}"


class TestInvalidation:
    def test_invalidating_forces_a_reopen(self, opened: BrainSession) -> None:
        """What `install.pull` depends on: everything opened before the pointer moved describes the old head."""
        before = opened.brain(Capability.INSPECT)
        opened.invalidate()
        assert opened.brain(Capability.INSPECT) is not before

    def test_invalidating_drops_every_capability_not_just_one(self, opened: BrainSession) -> None:
        """A pull advances the pointer through the WRITE instance, so the INSPECT one is stale too."""
        inspect_before = opened.brain(Capability.INSPECT)
        write_before = opened.brain(Capability.WRITE)
        opened.invalidate()
        assert opened.brain(Capability.INSPECT) is not inspect_before
        assert opened.brain(Capability.WRITE) is not write_before

    def test_the_service_reads_through_the_session(self, config: ResolvedConfig) -> None:
        """The corollary that keeps invalidation honest: an operation may hold the session, never a `Brain`.

        A brain cached anywhere else is a copy the session cannot reach, which makes `invalidate()` a lie in exactly
        the case it exists for -- a `state()` after a `pull()` answering from the composition the pull replaced.
        """
        service = BrainService(config)
        service.init()
        assert service.brain(Capability.INSPECT) is service.session.brain(Capability.INSPECT)

        service.session.invalidate()
        assert service.brain(Capability.INSPECT) is service.session.brain(Capability.INSPECT)

    def test_inspect_write_inspect_reports_the_new_composition(self, service: BrainService, source_file: Path) -> None:
        """The reported defect: an INSPECT view opened before registration must not survive it."""
        before = service.state()["snapshot"]["digest"]

        registered = service.register(source_file, media_type="text/markdown")
        after = service.state()

        assert after["snapshot"]["digest"] == registered["snapshot"]
        assert after["snapshot"]["digest"] != before
        assert set(after["installed"]) == {"canonical", "provenance"}

    def test_retrieve_write_retrieve_reopens_the_query_view(self, service: BrainService, source_file: Path) -> None:
        before = service.brain(Capability.RETRIEVE)

        service.register(source_file, media_type="text/markdown")

        after = service.brain(Capability.RETRIEVE)
        assert after is not before
        assert after.snapshot().block_count > 0

    def test_a_store_only_write_does_not_reopen_capability_views(
        self, service: BrainService, source_file: Path
    ) -> None:
        before = service.brain(Capability.INSPECT)

        service.put_content(source_file, media_type="text/markdown")

        assert service.brain(Capability.INSPECT) is before

    def test_a_head_change_invalidates_even_when_later_code_raises(
        self, opened: BrainSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        brain = opened.brain(Capability.WRITE)
        pointers = iter((b"before", b"after"))
        monkeypatch.setattr(brain.store, "read_pointer", lambda name: next(pointers))

        with pytest.raises(RuntimeError, match="after commit"):
            with opened.write():
                raise RuntimeError("after commit")

        assert opened._cache == {}


class TestConcurrency:
    """What happens when two threads reach one session, which is what the TUI does on every classification."""

    def test_two_threads_opening_at_once_share_one_brain(self, opened: BrainSession) -> None:
        """The loser of the race must not walk away with a brain the session never held.

        A second instance is not merely wasteful: it is unreachable from `invalidate`, so the thread holding it
        would keep answering from a composition that had been replaced.
        """
        start = threading.Barrier(2)
        opened_brains = []

        def open_one() -> None:
            start.wait()
            opened_brains.append(opened.brain(Capability.RETRIEVE))

        threads = [threading.Thread(target=open_one) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert opened_brains[0] is opened_brains[1]

    def test_a_read_whose_composition_was_replaced_is_refused(self, opened: BrainSession) -> None:
        with pytest.raises(StaleBrainError, match="replaced while this read was running"):
            with opened.pinned(Capability.INSPECT):
                opened.invalidate()

    def test_a_read_nothing_disturbed_returns_normally(self, opened: BrainSession) -> None:
        with opened.pinned(Capability.INSPECT) as brain:
            assert brain is opened.brain(Capability.INSPECT)

    def test_a_failing_read_reports_its_own_failure_rather_than_staleness(self, opened: BrainSession) -> None:
        """The body's exception is the one worth seeing; a staleness check that masked it would hide the cause."""

        def read_and_fail() -> None:
            with opened.pinned(Capability.INSPECT):
                opened.invalidate()
                raise RuntimeError("during the read")

        with pytest.raises(RuntimeError, match="during the read"):
            read_and_fail()

    def test_a_second_thread_cannot_start_a_write(self, opened: BrainSession) -> None:
        inside = threading.Event()
        release = threading.Event()

        def hold_the_write() -> None:
            with opened.write():
                inside.set()
                release.wait(timeout=5)

        holder = threading.Thread(target=hold_the_write)
        holder.start()
        try:
            assert inside.wait(timeout=5)
            with pytest.raises(SessionBusyError, match="another write is already running"):
                with opened.write():
                    pass
        finally:
            release.set()
            holder.join(timeout=5)

        with opened.write() as brain:
            assert brain is opened.brain(Capability.WRITE)

    def test_the_same_thread_may_nest_a_write(self, opened: BrainSession) -> None:
        """`migrate` and the reconciliation flows nest one write inside another; only a second thread is refused."""
        with opened.write():
            with opened.write() as inner:
                assert inner is opened.brain(Capability.WRITE)
