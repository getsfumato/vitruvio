"""The guard admits one read over the brain's tables, and refuses everything else by name."""

from __future__ import annotations

import pytest

from vitruvio.kernel import UsageError
from vitruvio.sql import guard
from vitruvio.sql.guard import every_name, visible_name


class TestRefusals:
    """Each refusal names the construct, so the caller can rephrase rather than report a bug."""

    @pytest.mark.parametrize(
        ("sql", "named"),
        [
            ("DELETE FROM semantic", "DELETE"),
            ("INSERT INTO semantic VALUES (1)", "INSERT"),
            ("UPDATE semantic SET label = 'x'", "UPDATE"),
            ("CREATE TABLE x AS SELECT 1", "CREATE"),
            ("DROP TABLE semantic", "DROP"),
            ("COPY semantic TO '/tmp/x.csv'", "COPY"),
            ("ATTACH 'other.db'", "ATTACH"),
            ("INSTALL httpfs", "INSTALL"),
            ("PRAGMA version", "PRAGMA"),
            ("SET memory_limit = '1TB'", "SET"),
        ],
    )
    def test_anything_that_is_not_a_query_is_refused(self, sql: str, named: str) -> None:
        with pytest.raises(UsageError, match=named):
            guard(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM read_csv('/etc/passwd')",
            "SELECT * FROM read_parquet('x.parquet')",
            "SELECT * FROM glob('*')",
            "SELECT (SELECT count(*) FROM read_json_auto('x.json'))",
        ],
    )
    def test_a_file_reader_is_refused_wherever_it_appears(self, sql: str) -> None:
        with pytest.raises(UsageError, match="outside the brain"):
            guard(sql)

    def test_a_table_function_is_refused(self) -> None:
        with pytest.raises(UsageError, match="table functions"):
            guard("SELECT * FROM range(10)")

    def test_a_string_used_as_a_table_is_an_unknown_table(self) -> None:
        """DuckDB reads ``FROM 'x.parquet'`` as a file. Here it is only a name, and not one of ours."""
        with pytest.raises(UsageError, match="no table called"):
            guard("SELECT * FROM 'x.parquet'")

    def test_an_unknown_table_lists_the_real_ones(self) -> None:
        with pytest.raises(UsageError) as raised:
            guard("SELECT * FROM facts")
        assert raised.value.hint is not None
        assert "semantic" in raised.value.hint

    def test_a_schema_qualified_table_is_refused_on_a_single_brain(self) -> None:
        with pytest.raises(UsageError, match="names a schema"):
            guard("SELECT * FROM physics.semantic")

    def test_two_statements_are_refused_rather_than_the_first_run(self) -> None:
        with pytest.raises(UsageError, match="one statement"):
            guard("SELECT 1; SELECT 2")

    @pytest.mark.parametrize("sql", ["", "   ", ";"])
    def test_an_empty_query_is_refused(self, sql: str) -> None:
        with pytest.raises(UsageError, match="empty"):
            guard(sql)

    def test_a_query_that_does_not_parse_says_so(self) -> None:
        with pytest.raises(UsageError, match="does not parse"):
            guard("SELECT FROM WHERE (")


class TestRewriting:
    """Admitted queries read the engine's views, keep the caller's names, and are ordered totally."""

    def test_a_table_reads_the_accessible_view_under_its_own_name(self) -> None:
        admitted = guard("SELECT label FROM semantic")
        assert f"{visible_name('semantic')} AS semantic" in admitted.executed
        assert admitted.tables == ("semantic",)

    def test_including_superseded_reads_every_member(self) -> None:
        admitted = guard("SELECT label FROM semantic", include_superseded=True)
        assert every_name("semantic") in admitted.executed

    def test_a_caller_alias_is_kept(self) -> None:
        admitted = guard("SELECT s.label FROM semantic AS s")
        assert f"{visible_name('semantic')} AS s" in admitted.executed

    def test_a_cte_is_not_mistaken_for_a_table(self) -> None:
        admitted = guard("WITH facts AS (SELECT * FROM semantic WHERE kind = 'fact') SELECT count(*) FROM facts")
        assert admitted.tables == ("semantic",)

    def test_every_table_read_is_reported_once(self) -> None:
        admitted = guard("SELECT * FROM episodic e JOIN semantic s ON s.id = e.id UNION ALL SELECT * FROM semantic")
        assert admitted.tables == ("episodic", "semantic")

    def test_an_unordered_query_is_ordered_by_all(self) -> None:
        admitted = guard("SELECT label FROM semantic LIMIT 3")
        assert not admitted.ordered
        assert "ORDER BY ALL LIMIT 3" in admitted.executed

    def test_an_ordered_query_keeps_its_order(self) -> None:
        admitted = guard("SELECT label FROM semantic ORDER BY label DESC")
        assert admitted.ordered
        assert "ALL" not in admitted.executed

    def test_a_trailing_semicolon_is_allowed(self) -> None:
        assert guard("SELECT 1;").canonical == "SELECT 1"


class TestSignature:
    """The signature names the question, so it follows the meaning rather than the spelling."""

    def test_two_spellings_of_one_query_share_a_signature(self) -> None:
        assert guard("select count(*) from semantic").signature == guard("SELECT COUNT(*)  FROM semantic").signature

    def test_visibility_is_part_of_the_question(self) -> None:
        assert (
            guard("SELECT count(*) FROM semantic").signature
            != guard("SELECT count(*) FROM semantic", include_superseded=True).signature
        )
