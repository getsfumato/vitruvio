"""A small brain, built in memory, whose every count is known in advance.

Real SDK modules over a real in-memory store rather than stand-ins: the engine reads ``resolvable()``, ``get()``,
``root`` and ``inclusion_proof()``, and a fake that agreed with the engine about those would prove nothing about the
brains it will actually meet.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from boltzmann.blocks.base import Block
from boltzmann.blocks.canonical import CanonicalBlock
from boltzmann.blocks.episodic import EpisodicBlock
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.blocks.procedural import ProceduralBlock, Step
from boltzmann.blocks.provenance import Actor, ActorKind, ProvenanceBlock, SupersessionRecord
from boltzmann.blocks.semantic import Relation, SemanticBlock, SemanticKind
from boltzmann.module.composition import Composition
from boltzmann.module.module import Module
from boltzmann.store.memory import MemoryBlockStore


@dataclass
class Brain:
    """The modules, the store they share, and the blocks by name so a test can refer to one."""

    store: MemoryBlockStore
    modules: dict[MemoryType, Module] = field(default_factory=dict)
    named: dict[str, Block] = field(default_factory=dict)

    def id(self, name: str) -> str:
        return str(self.named[name].block_id)


def _module(store: MemoryBlockStore, kind: MemoryType, blocks: Sequence[Block]) -> Module:
    return Module(kind, store, Composition(kind, [store.put_block(block) for block in blocks]))


@pytest.fixture
def brain() -> Brain:
    """
    Semantic: four facts (three physics, one math), one concept, and an old fact the concept superseded.
    Episodic: three episodes in 2025, tagged and attended so a GROUP BY over UNNEST has a known answer.
    Procedural: one procedure whose single step uses the concept.
    Canonical: one source.
    Provenance: the one supersession.
    """
    store = MemoryBlockStore()
    built = Brain(store)

    source = CanonicalBlock(blob=store.put_bytes(b"lecture notes"), media_type="text/markdown", size=13)
    facts = [
        SemanticBlock(kind=SemanticKind.FACT, label=f"Physics fact {n}", statement="s", subject="Physics")
        for n in range(3)
    ]
    facts.append(SemanticBlock(kind=SemanticKind.FACT, label="Math fact", statement="s", subject="math"))
    old = SemanticBlock(kind=SemanticKind.FACT, label="Old fact", statement="replaced", subject="Physics")
    concept = SemanticBlock(
        kind=SemanticKind.CONCEPT,
        label="Fourier series",
        statement="a periodic function as sines and cosines",
        relations=[Relation(predicate="generalises", target=facts[0].block_id)],
        evidence=[source.block_id],
    )
    episodes = [
        EpisodicBlock(summary="kickoff", occurred_at="2025-01-15T09:00:00Z", tags=["planning"], participants=["ana"]),
        EpisodicBlock(
            summary="review",
            occurred_at="2025-03-02T16:30:00Z",
            tags=["planning", "review"],
            participants=["ana", "juan"],
        ),
        EpisodicBlock(summary="retro", occurred_at="2025-06-30T23:59:59Z", tags=["review"], participants=["juan"]),
    ]
    procedure = ProceduralBlock(
        label="Decompose a signal",
        goal="find its frequencies",
        steps=[Step(action="apply the transform", uses=[concept.block_id])],
    )
    supersession = ProvenanceBlock(
        record=SupersessionRecord(
            block=concept.block_id,
            supersedes=old.block_id,
            actor=Actor(id="tester@example.com", kind=ActorKind.HUMAN),
            at="2025-02-01T00:00:00Z",
        )
    )

    built.modules = {
        MemoryType.CANONICAL: _module(store, MemoryType.CANONICAL, [source]),
        MemoryType.SEMANTIC: _module(store, MemoryType.SEMANTIC, [*facts, old, concept]),
        MemoryType.EPISODIC: _module(store, MemoryType.EPISODIC, episodes),
        MemoryType.PROCEDURAL: _module(store, MemoryType.PROCEDURAL, [procedure]),
        MemoryType.PROVENANCE: _module(store, MemoryType.PROVENANCE, [supersession]),
    }
    built.named = {"source": source, "old": old, "concept": concept, "procedure": procedure, "physics": facts[0]}
    return built
