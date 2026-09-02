"""Catalog declarations, navigation and query filtering through the runtime interface."""

from __future__ import annotations

from pathlib import Path

import pytest

from vitruvio.kernel import UsageError, VitruvioError
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

    tree = service.catalog_tree()
    assert tree["schemes"][0]["exclusive"] is True
    science = tree["schemes"][0]["roots"][0]
    assert science["label"] == "Science"
    assert science["children"][0]["label"] == "Mathematics"
    assert science["children"][0]["direct_sources"][0]["block_id"] == source
    assert tree["unclassified"] == []

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


def test_unclassified_sources_are_explicit(service: BrainService, source_file: Path) -> None:
    source = service.register(source_file, media_type="text/markdown")["block_id"]
    tree = service.catalog_tree()
    assert tree["schemes"] == []
    assert [row["block_id"] for row in tree["unclassified"]] == [source]


def test_query_class_references_filter_through_descendants(service: BrainService, source_file: Path) -> None:
    source = service.register(source_file, media_type="text/markdown")["block_id"]
    service.catalog_apply(manifest(source))

    found = service.search("", classes=["topic/Science"], memory_types=["canonical"], limit=10)
    assert [match["block_id"] for match in found["matches"]] == [source]


def test_unknown_manifest_class_references_are_translated(service: BrainService) -> None:
    with pytest.raises(VitruvioError) as caught:
        service.catalog_apply(
            {
                "schema": "vitruvio.catalog/v1",
                "classes": [{"scheme": "topic", "label": "Child", "broader": ["missing/Parent"]}],
            }
        )
    assert caught.value.code == "CATALOG_INVALID"


def test_unknown_query_class_references_are_translated(service: BrainService) -> None:
    with pytest.raises(VitruvioError) as caught:
        service.search("", classes=["missing/Parent"])
    assert caught.value.code == "CATALOG_INVALID"


def test_malformed_query_class_references_are_usage_errors(service: BrainService) -> None:
    with pytest.raises(UsageError, match="scheme/label"):
        service.search("", classes=["missing-slash"])


def test_malformed_manifest_schema_is_a_stable_usage_error(service: BrainService) -> None:
    with pytest.raises(VitruvioError) as caught:
        service.catalog_apply({"schema": "vitruvio.catalog/v1", "schemes": [{"exclusive": True}]})
    assert caught.value.code == "USAGE"
