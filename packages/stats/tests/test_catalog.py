"""The statistics vocabulary independent of any one index implementation."""

from vitruvio.stats import CATALOG_SCHEMA, ColumnStats, StatsVersion


class TestColumnStats:
    def test_a_scalar_column_uses_populated_blocks_not_distinct_values(self) -> None:
        stats = ColumnStats(distinct=10, null_count=20, populated_count=100, total_values=100)
        assert stats.average_values == 1.0

    def test_a_multi_valued_column_excludes_null_blocks_from_the_mean(self) -> None:
        stats = ColumnStats(distinct=5, null_count=7, populated_count=3, total_values=8)
        assert stats.average_values == 8 / 3

    def test_an_all_null_column_has_a_zero_average(self) -> None:
        stats = ColumnStats(null_count=12)
        assert stats.average_values == 0.0

    def test_an_empty_column_has_a_zero_average(self) -> None:
        assert ColumnStats().average_values == 0.0


def test_the_previous_catalog_schema_is_stale() -> None:
    version = StatsVersion(catalog_schema=CATALOG_SCHEMA - 1, root="root", leaf_fingerprint="leaves")
    freshness = version.freshness_against("root", "leaves")
    assert freshness.state == "stale"
    assert "catalog schema" in (freshness.reason or "")
