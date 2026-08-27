"""The shared brain cache, and the property that makes invalidating it mean something.

`BrainSession` exists so that the operations can be split across modules without each of them deciding for itself
what "the brain" is. Its whole value is that there is exactly one cache, so these tests are about identity: the same
capability hands back the same object, a different capability does not, and invalidating drops all of them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from vitruvio.kernel import ResolvedConfig
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
