"""One rule names a block, wherever it is shown.

Four readers used to answer "what is this block called" on their own -- the CLI's evidence table, the TUI's query
workspace, the diagnostics graph and the browse row -- and disagreed on provenance, on label-free relations and on
the sentinel. This drives every one of them over the same payload and expects the same answer.
"""

from __future__ import annotations

import pytest
from boltzmann.blocks.base import Block
from boltzmann.blocks.canonical import CanonicalBlock
from boltzmann.blocks.episodic import EpisodicBlock
from boltzmann.blocks.procedural import ProceduralBlock, Step
from boltzmann.blocks.provenance import Actor, ActorKind, ProvenanceBlock, RegistrationRecord
from boltzmann.blocks.semantic import SemanticBlock, SemanticKind
from boltzmann.catalog_models import PlacementDeclaration
from boltzmann.identity.digest import BlockId, OciDigest

from tests.match_payloads import match as _match
from vitruvio.runtime.browse import UNNAMED, identify, row
from vitruvio.runtime.query_diagnostics import _node, _short
from vitruvio.runtime.retrieval_result import MatchView

SOURCE = BlockId.parse("sha256:" + "1" * 64)
AT = "2026-03-01T10:00:00Z"

CASES: list[tuple[Block, tuple[str, str]]] = [
    (CanonicalBlock(blob=OciDigest.of(b"pretend"), media_type="application/pdf", size=7), ("application/pdf", "")),
    (
        EpisodicBlock(summary="Clase sobre series de Fourier", occurred_at=AT, context="aula 3"),
        ("Clase sobre series de Fourier", "aula 3"),
    ),
    (
        SemanticBlock(kind=SemanticKind.CONCEPT, label="Serie de Fourier", statement="Descompone una funcion."),
        ("Serie de Fourier", "Descompone una funcion."),
    ),
    (
        PlacementDeclaration(source=SOURCE, class_id=BlockId.parse("sha256:" + "2" * 64)).to_block(),
        ("Relation · classified_as", ""),
    ),
    (
        ProceduralBlock(label="Calcular coeficientes", goal="Obtener a_n y b_n", steps=[Step(action="Integrar")]),
        ("Calcular coeficientes", "Obtener a_n y b_n"),
    ),
    (
        ProvenanceBlock(
            record=RegistrationRecord(block=SOURCE, actor=Actor(id="tester@example.com", kind=ActorKind.HUMAN), at=AT)
        ),
        ("registration", f"{SOURCE}  by tester@example.com  {AT}"),
    ),
]


@pytest.mark.parametrize(
    ("block", "expected"), CASES, ids=lambda item: item.MEMORY_TYPE.value if isinstance(item, Block) else ""
)
def test_every_reader_names_a_block_the_same_way(block: Block, expected: tuple[str, str]) -> None:
    kind = block.MEMORY_TYPE.value
    payload = block.payload()
    title, detail = expected

    assert identify(kind, payload) == expected
    projected = row(block, block.MEMORY_TYPE)
    assert (projected["title"], projected["detail"]) == (title or UNNAMED, detail)
    assert MatchView(_match(kind, payload)).title == (title or UNNAMED)
    assert MatchView(_match(kind, payload)).detail == detail
    assert _node("sha256:" + "a" * 64, _match(kind, payload))["label"] == title[:64]


def test_a_payload_nothing_names_is_unnamed_except_in_a_diagram() -> None:
    """Two diagram nodes both called ``(unnamed)`` would be indistinguishable, so a node keeps its short digest."""
    identity = "sha256:" + "a" * 64
    assert MatchView(_match("canonical", {})).title == UNNAMED
    assert _node(identity, _match("canonical", {}))["label"] == _short(identity)
    assert _node(identity)["label"] == _short(identity)


def test_a_canonical_block_is_named_by_its_origin_when_a_list_knows_it() -> None:
    """A bundle carries no registration origin, so a match is named by its media type; a browse row, which reads
    the origin from provenance, is named by the file."""
    payload = CASES[0][0].payload()
    assert identify("canonical", payload, origin="/notes/fourier.pdf") == ("fourier.pdf", "/notes/fourier.pdf")
    assert identify("canonical", payload, origin="fourier.pdf") == ("fourier.pdf", "")
    assert identify("canonical", payload) == ("application/pdf", "")
