"""The SDK's own behavioural conformance suites, run against vitruvio's wiring.

This is the gate that says vitruvio *conforms to* the protocol rather than merely imports it. The suites are
pytest classes shipped in ``pyboltzmann[conformance]``; inheriting one runs its assertions against whatever
``make_reader`` hands back, which here is a fully wired brain -- the planner, the index set and the validators
this runtime actually assembles.

``BrainReaderConformance`` is the one that matters most, because its assertions are exactly the invariants the
paper fixes and leaves the implementation free underneath: every returned block verified by hash *and*
membership, data rather than prose, an unregistered index refused rather than faked, and no-match reported as an
answer rather than an error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from boltzmann.conformance import (
    BrainReaderConformance,
    CompositionConformance,
    IdentityConformance,
    MerkleConformance,
)

from vitruvio.kernel import resolve
from vitruvio.runtime import Capability, open_brain

if TYPE_CHECKING:
    from pathlib import Path

    from boltzmann.brain import Brain


class TestBrainReaderConformance(BrainReaderConformance):
    """vitruvio's assembled brain against the protocol's read contract."""

    @pytest.fixture
    def reader(self, tmp_path: Path) -> Brain:
        """A brain wired the way the runtime wires one, at retrieval capability."""
        config = resolve(brain=tmp_path / "conformance", actor_id="conformance@example.com", require_layout=False)
        return open_brain(config, Capability.WRITE, create=True)


class TestIdentityConformance(IdentityConformance):
    """Block identity, against the SDK's golden vectors.

    This suite tests the *SDK*, not vitruvio, and that is the point: it is the canary that catches a
    ``pyboltzmann`` bump changing how a block hashes underneath us. Identity changing silently would invalidate
    every index and every published artifact vitruvio has produced.
    """


class TestMerkleConformance(MerkleConformance):
    """Merkle roots and inclusion proofs, against the golden vectors. Same canary rationale."""


class TestCompositionConformance(CompositionConformance):
    """Composition arithmetic: add, drop, diff, and the append-only rule for episodic memory."""
