"""A small, hand-built corpus the index tests share.

Blocks are constructed directly rather than committed through a brain: an index is handed decoded blocks and a
``ContentReader``, so that is what a test should give it. Going through a brain would test the SDK's write path
again and make every assertion about an index depend on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from boltzmann.blocks.canonical import CanonicalBlock, NormalizedView
from boltzmann.blocks.episodic import EpisodicBlock
from boltzmann.blocks.procedural import ProceduralBlock, Step
from boltzmann.blocks.provenance import Actor, ActorKind, ProvenanceBlock, RegistrationRecord
from boltzmann.blocks.semantic import Relation, SemanticBlock, SemanticKind

from vitruvio.indices.testing import MemoryContent, block_id

if TYPE_CHECKING:
    from boltzmann.blocks.base import Block


@pytest.fixture
def empty_content() -> MemoryContent:
    """A reader holding nothing, for asserting that an unreadable view degrades rather than fails."""
    return MemoryContent({})


@pytest.fixture
def content() -> MemoryContent:
    """A reader holding the normalized view the canonical fixture points at."""
    return MemoryContent()


@pytest.fixture
def canonical(content: MemoryContent) -> CanonicalBlock:
    """A canonical block whose normalized view carries real text."""
    view = content.add(b"# Series de Fourier\n\nDescompone una funcion periodica en senos y cosenos.\n")
    blob = content.add(b"%PDF-1.7 pretend bytes")
    return CanonicalBlock(
        blob=blob,
        media_type="application/pdf",
        size=22,
        normalized_view=NormalizedView(blob=view, media_type="text/markdown", size=70),
    )


@pytest.fixture
def semantic_blocks() -> list[SemanticBlock]:
    """Four concepts across two subjects, one of them citing another."""
    evidence = block_id("source-pdf")
    concept = SemanticBlock(
        kind=SemanticKind.CONCEPT,
        label="Serie de Fourier",
        subject="senales",
        statement="Una serie de Fourier descompone una funcion periodica en senos y cosenos.",
        aliases=["Fourier series", "serie trigonometrica"],
        evidence=[evidence],
    )
    return [
        concept,
        SemanticBlock(
            kind=SemanticKind.FORMULA,
            label="Coeficientes de Fourier",
            subject="senales",
            statement="a_n = (2/T) integral f(t) cos(n w t) dt.",
            evidence=[evidence],
            relations=[Relation(predicate="derives_from", target=concept.block_id)],
        ),
        SemanticBlock(
            kind=SemanticKind.FACT,
            label="Ortogonalidad de armonicos",
            subject="senales",
            statement="Los armonicos son ortogonales sobre un periodo completo.",
            evidence=[evidence],
        ),
        SemanticBlock(
            kind=SemanticKind.CONCEPT,
            label="Transformada de Laplace",
            subject="control",
            statement="Lleva una funcion del tiempo al dominio de la frecuencia compleja.",
            evidence=[evidence],
        ),
    ]


@pytest.fixture
def episodic_blocks() -> list[EpisodicBlock]:
    """Three episodes spread across three months, so a range predicate has something to cut."""
    evidence = block_id("source-pdf")
    return [
        EpisodicBlock(
            summary="Clase de senales del 14 de mayo sobre series de Fourier",
            occurred_at="2026-05-14T14:00:00Z",
            context="aula 302",
            participants=["alex", "profesora"],
            outcome="se resolvio un ejercicio de coeficientes",
            tags=["clase", "senales"],
            evidence=[evidence],
        ),
        EpisodicBlock(
            summary="Consulta del 21 de mayo sobre ortogonalidad",
            occurred_at="2026-05-21T10:30:00Z",
            context="consulta",
            participants=["alex"],
            tags=["consulta", "senales"],
        ),
        EpisodicBlock(
            summary="Parcial de control del 2 de julio",
            occurred_at="2026-07-02T09:00:00Z",
            context="aula magna",
            participants=["alex"],
            tags=["parcial", "control"],
        ),
    ]


@pytest.fixture
def procedural_block(semantic_blocks: list[SemanticBlock]) -> ProceduralBlock:
    """A procedure whose steps use a semantic block, which is a graph edge."""
    return ProceduralBlock(
        label="Calcular coeficientes de Fourier",
        subject="senales",
        goal="Obtener a_n y b_n de una funcion periodica",
        steps=[
            Step(action="Identificar el periodo T"),
            Step(action="Integrar f(t) cos(n w t) sobre un periodo", uses=[semantic_blocks[1].block_id]),
            Step(action="Multiplicar por 2/T", condition="si la funcion no esta normalizada"),
        ],
        success_criteria=["la serie reconstruye la funcion original"],
    )


@pytest.fixture
def provenance_block(semantic_blocks: list[SemanticBlock]) -> ProvenanceBlock:
    """A registration record, which is addressed by what it talks about rather than by itself."""
    return ProvenanceBlock(
        record=RegistrationRecord(
            block=semantic_blocks[0].block_id,
            actor=Actor(id="tester@example.com", kind=ActorKind.HUMAN),
            at="2026-05-14T14:00:00Z",
            origin="fourier.pdf",
        )
    )


@pytest.fixture
def corpus(
    semantic_blocks: list[SemanticBlock],
    episodic_blocks: list[EpisodicBlock],
    procedural_block: ProceduralBlock,
) -> list[Block]:
    """Every non-canonical block, for tests that do not care which module a block came from."""
    return [*semantic_blocks, *episodic_blocks, procedural_block]
