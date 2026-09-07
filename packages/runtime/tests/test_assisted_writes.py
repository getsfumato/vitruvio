"""A write that names an assisting collaborator is a provenance v2 record, and every reader must still see it.

The SDK picks the oldest schema a record fits, so ``--assisted-by`` is what moves a record from provenance v1 to v2.
``ProvenanceBlockV2`` is a sibling of ``ProvenanceBlock``, not a subtype, and the projection once filed it as an
unknown block: the subject index held no entry for it, the reader trusted the index, and a block whose record sat in
the module read "no creation provenance names this block". What is pinned here is that the indexed path finds the
record, and that an index which projected fewer records than it holds never passes for complete.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vitruvio.kernel import resolve
from vitruvio.runtime import BrainService


@pytest.fixture
def assisted(tmp_path: Path) -> BrainService:
    """A service whose every write names an agent as assisting the configured actor."""
    config = resolve(
        brain=tmp_path / "brain",
        actor_id="tester@example.com",
        assisted_by=["openai/codex"],
        require_layout=False,
    )
    service = BrainService(config)
    service.init()
    return service


def _row(service: BrainService, memory_type: str, block_id: str) -> dict[str, Any]:
    return next(item for item in service.blocks(memory_type)["rows"] if item["block_id"] == block_id)


def test_a_registration_naming_an_assistant_shows_its_creator_through_the_index(
    assisted: BrainService, source_file: Path
) -> None:
    registered = assisted.register(source_file, media_type="text/markdown")["block_id"]
    assisted.index_build()

    authorship = _row(assisted, "canonical", registered)["authorship"]

    assert authorship["provenance"]["state"] == "indexed"
    assert authorship["complete"] is True
    (claim,) = authorship["claims"]
    assert claim["record_type"] == "registration"
    assert claim["actor"]["id"] == "tester@example.com"
    assert [party["id"] for party in claim["assisted_by"]] == ["openai/codex"]


def test_related_records_reach_an_assisted_registration(assisted: BrainService, source_file: Path) -> None:
    registered = assisted.register(source_file, media_type="text/markdown")["block_id"]
    assisted.index_build()

    related = assisted.related(registered)

    assert related["provenance"]["state"] == "indexed"
    assert related["count"] >= 1
    assert related["provenance"]["complete"] is True


def test_an_index_that_projected_fewer_records_than_it_holds_does_not_pass_for_complete(
    assisted: BrainService, source_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard behind the fix: a projection gap must surface as incomplete, never as "nobody created this"."""
    from vitruvio.indices import base
    from vitruvio.indices.projection import Facet, Projection

    first = assisted.register(source_file, media_type="text/markdown")["block_id"]
    other = source_file.parent / "notas.txt"
    other.write_text("una nota corta", encoding="utf-8")
    second = assisted.register(other, media_type="text/plain")["block_id"]

    faithful = base.project

    def blind_to_first(block: Any, content: Any = None) -> Projection:
        if block.MEMORY_TYPE.value == "provenance" and first in str(block.payload()):
            return Projection(
                block_id=str(block.block_id),
                memory_type=block.MEMORY_TYPE,
                facets={Facet.MEMORY_TYPE: ("provenance",)},
            )
        return faithful(block, content)

    monkeypatch.setattr(base, "project", blind_to_first)
    assisted.index_build(force=True)

    unseen = _row(assisted, "canonical", first)["authorship"]
    seen = _row(assisted, "canonical", second)["authorship"]

    assert unseen["provenance"]["state"] == "indexed"
    assert unseen["claims"] == []
    assert unseen["complete"] is False, "an index gap read as a definitive absence"
    assert seen["claims"], "the record the index did project is still found"
    assert seen["complete"] is False, "the same index cannot vouch for what it does return"
