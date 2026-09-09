"""Four read operations the suite reached through no path at all.

Found by the wire contract rather than by reading: they are offered to a caller elsewhere, they return JSON, and
nothing in two thousand tests had ever called one, so nothing pinned their field names. Each is cheap and each
answers a question somebody asks, so the answer is a test rather than an entry on an allowlist.
"""

from __future__ import annotations

import pytest

from vitruvio.runtime import BrainService


class TestReadsNothingElseCovered:
    def test_catalog_show_reports_an_empty_catalog_rather_than_failing(self, service: BrainService) -> None:
        """A brain with no scheme declared is the state every brain starts in."""
        result = service.catalog_show()

        assert result["schemes"] == []

    def test_index_stats_reports_freshness_per_module(self, service: BrainService) -> None:
        result = service.index_stats()

        assert {row["memory_type"] for row in result["statistics"]}
        assert all("freshness" in row for row in result["statistics"])

    def test_index_gc_is_a_dry_run_until_told_otherwise(self, service: BrainService) -> None:
        """The flag is `apply`, and defaulting it to False is the difference between a report and a deletion."""
        result = service.index_gc()

        assert result["applied"] is False
        assert result["removed"] == []

    def test_test_embedder_reports_the_tag_it_would_write(self, service: BrainService) -> None:
        """The default text embedder is feature hashing, tagged loudly enough that nobody mistakes it for
        semantics -- which is the thing worth asserting about it."""
        result = service.test_embedder(text="senos y cosenos")

        assert result["tag"].startswith("hashing/bow")
        assert result["semantic"] is False, "the default is feature hashing, and it says so"
        assert result["measured_dimensions"] == result["declared_dimensions"]


class TestIndexStatsScoped:
    @pytest.mark.parametrize("memory_type", ["canonical", "semantic"])
    def test_it_accepts_one_module(self, service: BrainService, memory_type: str) -> None:
        rows = service.index_stats(memory_type=memory_type)["statistics"]

        assert {row["memory_type"] for row in rows} == {memory_type}
