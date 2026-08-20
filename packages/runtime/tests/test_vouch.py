"""The guard that :mod:`vitruvio.runtime.vouch` says it has.

That module is the only place in vitruvio that touches an SDK private, and its docstring claims the dependency is
"covered by a test that fails loudly if ``_vouched`` disappears". The claim matters because the failure it describes
is silent by construction: :func:`supported` degrades to a *reported* warning, so when the private goes away every
caller keeps working, every existing test stays green, and `dist push` ships a brain without the one index a
consumer cannot rebuild.

These tests are that guard. They are pinned to the SDK on purpose, so an upgrade that moves the mechanism is
expected to fail here -- that failure *is* the feature. The fix is the "What to ask upstream" section of
:mod:`vitruvio.runtime.vouch`, not a relaxed assertion.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from vitruvio.kernel import resolve
from vitruvio.runtime import BrainService, Capability
from vitruvio.runtime.vouch import VOUCHED_ATTRIBUTE, supported, vouch_travelling


@pytest.fixture
def populated(tmp_path: Path, source_file: Path) -> BrainService:
    """A brain holding one committed semantic block, which is the least that projects to a vector."""
    from boltzmann.blocks.memory_type import MemoryType
    from boltzmann.identity.digest import BlockId
    from boltzmann.ingest.proposer import Candidate, CandidateSet

    service = BrainService(resolve(brain=tmp_path / "brain", actor_id="tester@example.com", require_layout=False))
    service.init()
    registered = service.register(source_file, media_type="text/markdown")

    brain = service.brain(Capability.WRITE)
    source = BlockId.parse(registered["block_id"])
    task = brain.define_task(source, allowed=[MemoryType.SEMANTIC])
    brain.commit(
        brain.validate(
            CandidateSet(
                task_id=task.task_id,
                candidates=[
                    Candidate(
                        memory_type=MemoryType.SEMANTIC,
                        evidence=[source],
                        locator="p1",
                        payload={
                            "kind": "concept",
                            "label": "Serie de Fourier",
                            "subject": "senales",
                            "statement": "Descompone una funcion periodica en senos y cosenos.",
                        },
                    )
                ],
            ),
            task,
        )
    )
    return service


class TestTheSdkPrivatesThisDependsOn:
    """One assertion per private. Failing here means the workaround is broken, not that a test is stale."""

    def test_the_vouched_set_still_exists(self, populated: BrainService) -> None:
        brain = populated.brain(Capability.RETRIEVE)
        assert supported(brain), (
            f"Brain.{VOUCHED_ATTRIBUTE} is gone. vouch_travelling() now degrades to a warning, so a vector index "
            "will be silently omitted from every publish. See the 'What to ask upstream' section of "
            "vitruvio.runtime.vouch: the SDK may have grown the public Brain.vouch() this module is waiting for."
        )

    def test_the_vouched_set_is_still_a_mutable_set_of_memory_types(self, populated: BrainService) -> None:
        """``vouch_travelling`` calls ``.discard`` on it to *un*-vouch, which a frozenset would refuse."""
        from boltzmann.blocks.memory_type import MemoryType

        brain = populated.brain(Capability.RETRIEVE)
        vouched = getattr(brain, VOUCHED_ATTRIBUTE)
        assert isinstance(vouched, set), f"expected a mutable set, got {type(vouched).__name__}"
        assert all(isinstance(item, MemoryType) for item in vouched)

    def test_the_build_path_still_takes_a_module_and_indices(self) -> None:
        """The other private: ``brain._build(module, [index])`` is the SDK's own vouching path."""
        from boltzmann.brain import Brain

        assert hasattr(Brain, "_build"), "Brain._build is gone; vouch_travelling has no way to vouch"
        parameters = list(inspect.signature(Brain._build).parameters)
        assert parameters[:3] == ["self", "module", "indices"], (
            f"Brain._build's signature changed to {parameters}; vouch_travelling calls it positionally"
        )


class TestVouchingAVectorIndex:
    def test_a_built_index_is_vouched_for(self, populated: BrainService) -> None:
        """The property the module exists for, asserted through the command that people actually run."""
        assert populated.index_build()["vouched"]["semantic"] == "vouched"

    def test_a_module_that_projects_to_nothing_is_refused_and_un_vouched(self, populated: BrainService) -> None:
        """An artifact claiming a vector index and carrying none is what the protocol calls the worse failure.

        `canonical` holds blocks and has a vector index registered, but originals project to nothing embeddable. A
        refusal is not enough on its own: the SDK's write path vouches on every commit, so the module arrives here
        *already* vouched and has to come back out of the set, or `pack()` raises while the message stays accurate.
        """
        from boltzmann.blocks.memory_type import MemoryType

        brain = populated.brain(Capability.RETRIEVE)
        outcome = vouch_travelling(brain)
        vouched = getattr(brain, VOUCHED_ATTRIBUTE)

        assert "no vector index to publish" in outcome["canonical"]
        assert MemoryType.CANONICAL not in vouched, "a refused module must be discarded, not merely reported"
        assert MemoryType.SEMANTIC in vouched, "and refusing one module must not cost another its vector index"
