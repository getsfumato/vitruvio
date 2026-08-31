"""Catalog declarations, navigation and query filtering through the runtime interface."""

from __future__ import annotations

from pathlib import Path

from vitruvio.runtime import BrainService


def manifest(source: str) -> dict[str, object]:
    return {
        "schema": "vitruvio.catalog/v1",
        "schemes": [{"name": "topic", "exclusive": True}],
        "classes": [
            {"scheme": "topic", "label": "Science"},
            {"scheme": "topic", "label": "Mathematics", "broader": ["topic/Science"]},
        ],
        "placements": [{"source": source, "classes": ["topic/Mathematics"]}],
    }


def test_a_manifest_is_atomic_navigable_and_idempotent(service: BrainService, source_file: Path) -> None:
    source = service.register(source_file, media_type="text/markdown")["block_id"]
    before = service.state()["snapshot"]["digest"]

    dry_run = service.catalog_apply(manifest(source), dry_run=True)
    assert dry_run["clean"] is True
    assert dry_run["applied"] is False
    assert service.state()["snapshot"]["digest"] == before

    applied = service.catalog_apply(manifest(source))
    assert applied["clean"] is True
    assert applied["applied"] is True
    assert service.catalog_browse(["topic/Science"])["sources"] == [source]
    assert service.catalog_path(["topic"], "")["directories"] == ["Mathematics", "Science"]

    repeated = service.catalog_apply(manifest(source))
    assert repeated["clean"] is True
    assert repeated["applied"] is False


def test_an_invalid_manifest_commits_nothing(service: BrainService) -> None:
    before = service.state()["snapshot"]["digest"]
    result = service.catalog_apply(
        {
            "schema": "vitruvio.catalog/v1",
            "classes": [{"scheme": "missing", "label": "Class"}],
        }
    )
    assert result["clean"] is False
    assert result["applied"] is False
    assert service.state()["snapshot"]["digest"] == before


def test_query_class_references_filter_through_descendants(service: BrainService, source_file: Path) -> None:
    source = service.register(source_file, media_type="text/markdown")["block_id"]
    service.catalog_apply(manifest(source))

    found = service.search("", classes=["topic/Science"], memory_types=["canonical"], limit=10)
    assert [match["block_id"] for match in found["matches"]] == [source]
