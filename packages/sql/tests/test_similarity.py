"""``about`` and ``similarity``: a threshold over every block, scored once, and always reported as approximate."""

from __future__ import annotations

from typing import Any

import pytest

from vitruvio.kernel import UsageError
from vitruvio.sql import Similarity, SqlEngine, guard
from vitruvio.sql.similarity import SCORE_BLOCK, similarity_table

# The `brain` fixture's type lives in conftest.py, which a test module cannot import by name.
Brain = Any


class FixedScorer:
    """Scores from a table, and a count of how often it was asked -- a text must be embedded once per query."""

    def __init__(self, scores: dict[str, float], *, missing: dict[str, str] | None = None) -> None:
        self.scores = scores
        self.missing = missing or {}
        self.asked: list[str] = []

    def similarity(self, text: str) -> Similarity:
        self.asked.append(text)
        return Similarity(scores=dict(self.scores), models={"semantic": "test/model@1"}, missing=dict(self.missing))


@pytest.fixture
def scorer(brain: Brain) -> FixedScorer:
    """The concept scores high, one physics fact scores in the middle, everything else is unscored or low."""
    return FixedScorer({brain.id("concept"): 0.91, brain.id("physics"): 0.40, brain.id("old"): 0.95})


def _query(brain: Brain, scorer: Any, sql: str, **options: Any) -> Any:
    with SqlEngine(brain.modules, scorer=scorer) as engine:
        return engine.query(sql, **options)


class TestThreshold:
    def test_about_admits_every_block_at_or_above_the_threshold(self, brain: Brain, scorer: FixedScorer) -> None:
        outcome = _query(brain, scorer, "SELECT label FROM semantic WHERE about(id, 'fourier', 0.4)")
        assert outcome.rows == [["Fourier series"], ["Physics fact 0"]]

    def test_the_threshold_is_inclusive(self, brain: Brain, scorer: FixedScorer) -> None:
        assert _query(brain, scorer, "SELECT count(*) FROM semantic WHERE about(id, 't', 0.91)").rows == [[1]]

    def test_a_hidden_block_is_not_counted_however_well_it_scores(self, brain: Brain, scorer: FixedScorer) -> None:
        """The superseded fact scores highest; visibility is applied before similarity, as everywhere else."""
        assert _query(brain, scorer, "SELECT count(*) FROM semantic WHERE about(id, 't', 0.9)").rows == [[1]]

    def test_similarity_is_a_value_a_query_can_order_by(self, brain: Brain, scorer: FixedScorer) -> None:
        sql = (
            "SELECT label, similarity(id, 'fourier') AS score FROM semantic "
            "WHERE similarity(id, 'fourier') IS NOT NULL ORDER BY score DESC"
        )
        assert _query(brain, scorer, sql).rows == [["Fourier series", 0.91], ["Physics fact 0", 0.4]]

    def test_an_unqualified_id_binds_to_the_callers_row(self, brain: Brain, scorer: FixedScorer) -> None:
        """Inside the rewritten subquery `id` would bind to the score table if it had one; it must not."""
        sql = "SELECT count(*) FROM semantic WHERE similarity(id, 'fourier') > 0.5"
        assert _query(brain, scorer, sql).rows == [[1]]

    def test_a_qualified_id_works_across_a_join(self, brain: Brain, scorer: FixedScorer) -> None:
        sql = (
            "SELECT p.label FROM procedural p, UNNEST(p.steps) AS s(step), UNNEST(step.uses) AS u(used) "
            "JOIN semantic c ON c.id = used WHERE about(c.id, 'fourier', 0.5)"
        )
        assert _query(brain, scorer, sql).rows == [["Decompose a signal"]]


class TestApproximate:
    def test_a_query_that_uses_similarity_is_never_exact(self, brain: Brain, scorer: FixedScorer) -> None:
        outcome = _query(brain, scorer, "SELECT count(*) FROM semantic WHERE about(id, 'fourier', 0.4)")
        assert outcome.exact is False
        (entry,) = outcome.approximate
        assert entry == {
            "kind": "similarity",
            "text": "fourier",
            "min_score": [0.4],
            "models": {"semantic": "test/model@1"},
            "unscored": {},
        }

    def test_each_text_is_scored_once_however_often_it_appears(self, brain: Brain, scorer: FixedScorer) -> None:
        sql = (
            "SELECT count(*) FROM semantic WHERE about(id, 'a', 0.3) OR about(id, 'a', 0.9) "
            "OR similarity(id, 'b') > 0.1"
        )
        outcome = _query(brain, scorer, sql)
        assert scorer.asked == ["a", "b"]
        assert [(entry["text"], entry["min_score"]) for entry in outcome.approximate] == [("a", [0.3, 0.9]), ("b", [])]

    def test_an_unscored_module_the_query_reads_is_reported(self, brain: Brain) -> None:
        scorer = FixedScorer({brain.id("concept"): 0.9}, missing={"procedural": "no vector index"})
        outcome = _query(brain, scorer, "SELECT count(*) FROM blocks WHERE about(id, 'x', 0.5)")
        assert outcome.approximate[0]["unscored"] == {"procedural": "no vector index"}
        assert outcome.degradations[-1]["kind"] == "similarity_unscored"

    def test_an_unscored_module_the_query_does_not_read_is_not(self, brain: Brain) -> None:
        scorer = FixedScorer({brain.id("concept"): 0.9}, missing={"procedural": "no vector index"})
        outcome = _query(brain, scorer, "SELECT count(*) FROM semantic WHERE about(id, 'x', 0.5)")
        assert outcome.approximate[0]["unscored"] == {}
        assert outcome.degradations == []


class TestRefusals:
    def test_without_a_scorer_similarity_is_refused(self, brain: Brain) -> None:
        with pytest.raises(UsageError, match="vector indices"):
            _query(brain, None, "SELECT count(*) FROM semantic WHERE about(id, 'x', 0.5)")

    def test_nothing_scored_is_an_error_never_a_zero(self, brain: Brain) -> None:
        scorer = FixedScorer({}, missing={"semantic": "embedder unavailable"})
        with pytest.raises(UsageError, match="nothing could be scored") as raised:
            _query(brain, scorer, "SELECT count(*) FROM semantic WHERE about(id, 'x', 0.5)")
        assert "embedder unavailable" in raised.value.message
        assert raised.value.hint is not None
        assert "index build" in raised.value.hint

    def test_a_query_without_similarity_never_asks_the_scorer(self, brain: Brain, scorer: FixedScorer) -> None:
        outcome = _query(brain, scorer, "SELECT count(*) FROM semantic")
        assert scorer.asked == []
        assert outcome.exact is True

    @pytest.mark.parametrize(
        ("sql", "message"),
        [
            ("SELECT about(id, label, 0.3) FROM semantic", "string literal"),
            ("SELECT about(id, '   ', 0.3) FROM semantic", "string literal"),
            ("SELECT about(id, 'x', 0) FROM semantic", "min_score"),
            ("SELECT about(id, 'x', 1.5) FROM semantic", "min_score"),
            ("SELECT about(id, 'x', kind) FROM semantic", "min_score"),
            ("SELECT about(id, 'x') FROM semantic", "3 arguments"),
            ("SELECT similarity(id) FROM semantic", "2 arguments"),
        ],
    )
    def test_malformed_calls_are_usage_errors(self, sql: str, message: str) -> None:
        with pytest.raises(UsageError, match=message):
            guard(sql)

    def test_the_score_tables_cannot_be_named_directly(self) -> None:
        with pytest.raises(UsageError, match="reserved"):
            guard(f"SELECT {SCORE_BLOCK} FROM {similarity_table(0)}")

    def test_a_cte_cannot_forge_a_score_table(self, brain: Brain, scorer: FixedScorer) -> None:
        """The review's reproduction: a CTE named like the score table would be resolved before it."""
        forged = (
            f"WITH {similarity_table(0)} AS (SELECT 'sha256:fake' AS {SCORE_BLOCK}, 1.0 AS __vitruvio_score) "
            "SELECT about('sha256:fake', 'ethics', 0.5)"
        )
        with pytest.raises(UsageError, match="reserved"):
            _query(brain, scorer, forged)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT label AS __vitruvio_score FROM semantic",
            "SELECT x.label FROM semantic AS __vitruvio_visible_x",
            "WITH __VITRUVIO_similarity_0 AS (SELECT 1) SELECT 1",
        ],
    )
    def test_the_engine_prefix_is_reserved_everywhere(self, sql: str) -> None:
        with pytest.raises(UsageError, match="reserved"):
            guard(sql)


class TestGuard:
    def test_the_canonical_query_keeps_the_call_and_the_executed_one_reads_scores(self) -> None:
        admitted = guard("SELECT count(*) FROM semantic WHERE about(id, 'ética', 0.35)")
        assert "about(" in admitted.canonical.lower()
        assert similarity_table(0) in admitted.executed
        assert admitted.similarity == ("ética",)
        assert admitted.thresholds == {"ética": (0.35,)}

    def test_the_threshold_is_part_of_the_signature(self) -> None:
        assert (
            guard("SELECT count(*) FROM semantic WHERE about(id, 'x', 0.3)").signature
            != guard("SELECT count(*) FROM semantic WHERE about(id, 'x', 0.4)").signature
        )
