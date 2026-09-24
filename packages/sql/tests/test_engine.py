"""The engine answers SQL over a brain exactly, and cannot be talked into touching the host."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.module.module import Module

from vitruvio.kernel import UsageError
from vitruvio.sql import SqlEngine, SqlTimeoutError
from vitruvio.sql.engine import _stored_table

# The `brain` fixture's type lives in conftest.py, which a test module cannot import by name.
Brain = Any


def _rows(brain: Brain, sql: str, **options: Any) -> list[list[Any]]:
    with SqlEngine(brain.modules) as engine:
        return engine.query(sql, **options).rows


class TestAggregates:
    """The questions search cannot answer: how many, grouped how, satisfying what."""

    def test_count_is_over_every_accessible_block_not_a_top_k(self, brain: Brain) -> None:
        assert _rows(brain, "SELECT count(*) FROM semantic WHERE kind = 'fact'") == [[4]]

    def test_group_by_is_by_the_value_as_written(self, brain: Brain) -> None:
        """``Physics`` and ``math`` stay as spelled: the table holds what the block says, not a folded key."""
        assert _rows(brain, "SELECT subject, count(*) FROM semantic WHERE kind = 'fact' GROUP BY subject") == [
            ["Physics", 3],
            ["math", 1],
        ]

    def test_boolean_logic_is_sql_boolean_logic(self, brain: Brain) -> None:
        sql = "SELECT count(*) FROM semantic WHERE (kind = 'concept' OR subject = 'math') AND NOT label LIKE 'Old%'"
        assert _rows(brain, sql) == [[2]]

    def test_a_multi_valued_field_is_grouped_through_unnest(self, brain: Brain) -> None:
        sql = "SELECT tag, count(*) FROM episodic, UNNEST(tags) AS u(tag) GROUP BY tag"
        assert _rows(brain, sql) == [["planning", 2], ["review", 2]]

    def test_list_membership_and_time_ranges_compose(self, brain: Brain) -> None:
        sql = "SELECT summary FROM episodic WHERE list_contains(participants, 'juan') AND occurred_at >= '2025-03-01'"
        assert _rows(brain, sql) == [["retro"], ["review"]]

    def test_a_date_bound_on_a_timestamp_reads_as_written(self, brain: Brain) -> None:
        assert _rows(brain, "SELECT count(*) FROM episodic WHERE occurred_at < '2025-06-30'") == [[2]]

    def test_having_filters_groups(self, brain: Brain) -> None:
        sql = "SELECT subject FROM semantic GROUP BY subject HAVING count(*) > 1"
        assert _rows(brain, sql) == [["Physics"]]

    def test_the_blocks_table_counts_the_whole_brain(self, brain: Brain) -> None:
        sql = "SELECT memory_type, count(*) FROM blocks GROUP BY memory_type"
        assert _rows(brain, sql) == [
            ["canonical", 1],
            ["episodic", 3],
            ["procedural", 1],
            ["provenance", 1],
            ["semantic", 5],
        ]


class TestAcrossMemories:
    """Joins are what make "which procedures use this concept" a query rather than a walk."""

    def test_which_procedures_use_a_concept(self, brain: Brain) -> None:
        sql = (
            "SELECT p.label FROM procedural p, UNNEST(p.steps) AS s(step), UNNEST(step.uses) AS u(used) "
            "JOIN semantic c ON c.id = used WHERE c.label = 'Fourier series'"
        )
        assert _rows(brain, sql) == [["Decompose a signal"]]

    def test_relations_are_structs_a_query_can_read(self, brain: Brain) -> None:
        sql = (
            "SELECT r.predicate, t.label FROM semantic s, UNNEST(s.relations) AS u(r) "
            "JOIN semantic t ON t.id = r.target"
        )
        assert _rows(brain, sql) == [["generalises", "Physics fact 0"]]

    def test_evidence_resolves_to_its_canonical_source(self, brain: Brain) -> None:
        sql = (
            "SELECT c.media_type FROM semantic s, UNNEST(s.evidence) AS u(e) JOIN canonical c ON c.id = e "
            "WHERE s.label = 'Fourier series'"
        )
        assert _rows(brain, sql) == [["text/markdown"]]


class TestVisibility:
    """Superseded blocks are members, and hidden by default, exactly as search hides them."""

    def test_a_superseded_block_is_hidden_and_counted_as_hidden(self, brain: Brain) -> None:
        with SqlEngine(brain.modules) as engine:
            outcome = engine.query("SELECT count(*) FROM semantic WHERE label = 'Old fact'")
        assert outcome.rows == [[0]]
        assert outcome.hidden == {"semantic": 1}

    def test_including_superseded_shows_it_and_says_so(self, brain: Brain) -> None:
        with SqlEngine(brain.modules) as engine:
            outcome = engine.query(
                "SELECT label, superseded FROM semantic WHERE label = 'Old fact'", include_superseded=True
            )
        assert outcome.rows == [["Old fact", True]]
        assert outcome.hidden == {}

    def test_a_ledger_passed_in_is_the_one_used(self, brain: Brain) -> None:
        """The runtime hands over the planner's cached ledger; reading a second one would cost seconds."""
        ledger = SimpleNamespace(superseded_by={}, demoted={brain.named["physics"].block_id})
        with SqlEngine(brain.modules, ledger=ledger) as engine:  # type: ignore[arg-type]
            outcome = engine.query("SELECT count(*) FROM semantic WHERE kind = 'fact'")
        assert outcome.rows == [[4]], "the demoted fact is hidden, and the old one -- not superseded here -- is not"

    def test_an_unreadable_member_is_still_a_member(self, brain: Brain) -> None:
        """A redacted block proves into the root, so it counts -- with nothing to say about its fields."""
        semantic = brain.modules[MemoryType.SEMANTIC]
        redacted = Module(
            MemoryType.SEMANTIC,
            semantic.store,
            semantic.composition,
            tombstones=[brain.named["concept"].block_id],
        )
        modules = {**brain.modules, MemoryType.SEMANTIC: redacted}
        with SqlEngine(modules) as engine:
            rows = engine.query("SELECT resolvable, label FROM semantic WHERE NOT resolvable").rows
        assert rows == [[False, None]]


class TestHonesty:
    """What the outcome says about itself: roots, absences, truncation, proofs."""

    def test_the_outcome_names_the_root_of_every_module_it_read(self, brain: Brain) -> None:
        with SqlEngine(brain.modules) as engine:
            outcome = engine.query("SELECT count(*) FROM semantic JOIN episodic ON true")
        assert outcome.verified_against == {
            "episodic": str(brain.modules[MemoryType.EPISODIC].root),
            "semantic": str(brain.modules[MemoryType.SEMANTIC].root),
        }

    def test_a_module_that_is_not_installed_is_empty_and_named(self, brain: Brain) -> None:
        modules = {kind: module for kind, module in brain.modules.items() if kind is not MemoryType.EPISODIC}
        with SqlEngine(modules) as engine:
            outcome = engine.query("SELECT count(*) FROM episodic")
        assert outcome.rows == [[0]]
        assert outcome.not_installed == ["episodic"]

    def test_more_rows_than_the_limit_are_truncated_not_dropped_silently(self, brain: Brain) -> None:
        with SqlEngine(brain.modules) as engine:
            outcome = engine.query("SELECT label FROM semantic", limit=2)
        assert outcome.row_count == 2
        assert outcome.truncated

    def test_unordered_rows_come_back_in_one_order(self, brain: Brain) -> None:
        first = _rows(brain, "SELECT label FROM semantic")
        assert first == sorted(first)
        assert first == _rows(brain, "SELECT label FROM semantic")

    def test_verification_proves_every_returned_block(self, brain: Brain) -> None:
        with SqlEngine(brain.modules) as engine:
            outcome = engine.query("SELECT id, label FROM semantic", verify=True)
        assert outcome.verified_rows == outcome.row_count == 5
        assert outcome.degradations == []

    def test_verification_drops_a_row_that_names_no_member(self, brain: Brain) -> None:
        with SqlEngine(brain.modules) as engine:
            outcome = engine.query("SELECT 'sha256:' || repeat('0', 64) AS id", verify=True)
        assert outcome.rows == []
        assert outcome.degradations[0]["kind"] == "verification_failed"

    def test_verification_without_an_id_column_says_it_was_skipped(self, brain: Brain) -> None:
        with SqlEngine(brain.modules) as engine:
            outcome = engine.query("SELECT count(*) FROM semantic", verify=True)
        assert outcome.verified_rows is None
        assert outcome.degradations[0]["kind"] == "verification_skipped"

    def test_values_leave_json_native(self, brain: Brain) -> None:
        (row,) = _rows(brain, "SELECT occurred_at, tags, sum(1) FROM episodic WHERE summary = 'review' GROUP BY ALL")
        assert row == ["2025-03-02T16:30:00Z", ["planning", "review"], 1]

    def test_explain_returns_a_plan_and_no_rows(self, brain: Brain) -> None:
        with SqlEngine(brain.modules) as engine:
            outcome = engine.explain("SELECT count(*) FROM semantic")
        assert outcome.rows == []
        assert outcome.plan
        assert "AGGREGATE" in outcome.plan


class TestErrors:
    def test_an_unknown_column_is_a_usage_error_pointing_at_the_schema(self, brain: Brain) -> None:
        with pytest.raises(UsageError) as raised:
            _rows(brain, "SELECT nope FROM semantic")
        assert raised.value.hint is not None
        assert "--schema" in raised.value.hint

    def test_a_limit_below_one_is_refused(self, brain: Brain) -> None:
        with pytest.raises(UsageError, match="limit"):
            _rows(brain, "SELECT 1", limit=0)

    def test_a_query_past_its_timeout_is_interrupted(self, brain: Brain) -> None:
        """Ten-way cross join of six rows: sixty million string concatenations, and far longer than 50ms."""
        tables = ", ".join(f"semantic t{n}" for n in range(10))
        labels = " || ".join(f"t{n}.label" for n in range(10))
        with SqlEngine(brain.modules, timeout=0.05) as engine, pytest.raises(SqlTimeoutError):
            engine.query(f"SELECT count(*) FROM {tables} WHERE {labels} LIKE '%never%'")


class TestSandbox:
    """The second wall: what the guard admitted still meets a database that cannot reach the host."""

    def test_the_database_cannot_read_a_file_even_if_asked_directly(self, brain: Brain, tmp_path: Path) -> None:
        import duckdb

        secret = tmp_path / "secret.csv"
        secret.write_text("a\n1\n")
        with SqlEngine(brain.modules) as engine:
            connection = engine._open(("semantic",))
            with pytest.raises(duckdb.PermissionException):
                connection.execute(f"SELECT * FROM read_csv('{secret}')")
            with pytest.raises(duckdb.InvalidInputException):
                connection.execute("SET enable_external_access = true")

    def test_the_stored_table_is_loaded_before_the_seal(self, brain: Brain) -> None:
        with SqlEngine(brain.modules) as engine:
            connection = engine._open(("semantic",))
            assert connection.execute(f"SELECT count(*) FROM {_stored_table('semantic')}").fetchone() == (6,)


class TestCache:
    """A cached table is either exactly the composition's or not found."""

    def test_a_table_is_cached_under_its_root_and_reused(self, brain: Brain, tmp_path: Path) -> None:
        with SqlEngine(brain.modules, cache_dir=tmp_path) as engine:
            first = engine.query("SELECT count(*) FROM semantic").rows
        (cached,) = tmp_path.glob("semantic-*.parquet")
        stamp = cached.stat().st_mtime_ns
        with SqlEngine(brain.modules, cache_dir=tmp_path) as engine:
            assert engine.query("SELECT count(*) FROM semantic").rows == first
        assert cached.stat().st_mtime_ns == stamp, "the second run read the cache rather than rebuilding it"

    def test_a_new_composition_builds_a_new_table_and_drops_the_old(self, brain: Brain, tmp_path: Path) -> None:
        with SqlEngine(brain.modules, cache_dir=tmp_path) as engine:
            engine.query("SELECT count(*) FROM semantic")
        (before,) = tmp_path.glob("semantic-*.parquet")

        semantic = brain.modules[MemoryType.SEMANTIC]
        smaller = semantic.without_blocks([brain.named["physics"].block_id])
        with SqlEngine({**brain.modules, MemoryType.SEMANTIC: smaller}, cache_dir=tmp_path) as engine:
            assert engine.query("SELECT count(*) FROM semantic WHERE kind = 'fact'").rows == [[3]]
        (after,) = tmp_path.glob("semantic-*.parquet")
        assert after != before

    def test_supersession_is_not_frozen_into_the_cache(self, brain: Brain, tmp_path: Path) -> None:
        """The ledger lives in another module, so it can change under a cached table whose root did not."""
        with SqlEngine(brain.modules, cache_dir=tmp_path) as engine:
            assert engine.query("SELECT count(*) FROM semantic").rows == [[5]]
        nothing_hidden = SimpleNamespace(superseded_by={}, demoted=set())
        with SqlEngine(brain.modules, ledger=nothing_hidden, cache_dir=tmp_path) as engine:  # type: ignore[arg-type]
            assert engine.query("SELECT count(*) FROM semantic").rows == [[6]]
