"""The scoring, tested against hand-built rankings.

The metrics are where a benchmark quietly lies: an empty answer set that scores 1.0, a mean that hides a p99, a
failure averaged in as a zero. Each of those is pinned here rather than trusted, because a benchmark nobody can
believe is worse than none -- it produces numbers people quote.
"""

from __future__ import annotations

import pytest

from vitruvio.bench.corpus import Judgement
from vitruvio.bench.harness import Measurement, compare, measure, ndcg


class FakeCorpus:
    """A corpus with hand-written judgements and the real recall calculation."""

    def __init__(self, judgements: list[Judgement]) -> None:
        self.judgements = judgements

    def recall_at(self, found: list[str], judgement: Judgement, k: int = 10) -> float:
        """The same rule the real corpus uses."""
        if not judgement.relevant:
            return 1.0
        return len(set(found[:k]) & judgement.relevant) / len(judgement.relevant)


def judgement(query: str, *relevant: str) -> Judgement:
    """One judged query."""
    return Judgement(query=query, relevant=frozenset(relevant), subject="test")


class TestNdcg:
    def test_the_ideal_ranking_scores_one(self) -> None:
        assert ndcg(["a", "b", "c"], judgement("q", "a", "b")) == pytest.approx(1.0)

    def test_rank_matters_not_only_presence(self) -> None:
        """Recall says whether the answer was found; nDCG says whether it was found near the top. A plan that
        returns everything relevant at ranks 8 and 9 has perfect recall@10 and is useless to a caller reading the
        first result."""
        top = ndcg(["a", "x", "y"], judgement("q", "a"))
        buried = ndcg(["x", "y", "a"], judgement("q", "a"))
        assert top > buried

    def test_finding_nothing_scores_zero(self) -> None:
        assert ndcg(["x", "y"], judgement("q", "a")) == 0.0

    def test_a_cutoff_beyond_the_results_does_not_inflate(self) -> None:
        assert ndcg(["a"], judgement("q", "a", "b"), k=10) < 1.0


class TestMeasure:
    def test_recall_is_averaged_over_the_query_set(self) -> None:
        corpus = FakeCorpus([judgement("uno", "a"), judgement("dos", "b")])

        def run(_configuration: str, query: str) -> list[str]:
            return ["a"] if query == "uno" else ["z"]

        result = measure(corpus, run, "planner")  # type: ignore[arg-type]
        assert result.recall[10] == pytest.approx(0.5)

    def test_a_configuration_that_cannot_run_is_recorded_not_averaged_as_zero(self) -> None:
        """ "This configuration is not installed" and "this configuration retrieved nothing" are different findings.
        Collapsing them would make a missing index look like a bad ranking."""

        def run(_configuration: str, _query: str) -> list[str]:
            raise RuntimeError("no vector index in this build")

        result = measure(FakeCorpus([judgement("uno", "a")]), run, "vector")  # type: ignore[arg-type]
        assert result.recall[10] == 0.0
        assert len(result.failures) == 1
        assert "no vector index" in result.failures[0]
        assert result.latencies_ms == [], "a query that raised took no measurable time worth reporting"

    def test_latency_is_recorded_per_query(self) -> None:
        corpus = FakeCorpus([judgement("uno", "a"), judgement("dos", "b")])
        result = measure(corpus, lambda _c, _q: ["a"], "planner")  # type: ignore[arg-type]
        assert len(result.latencies_ms) == 2


class TestPercentiles:
    def test_percentiles_come_from_the_samples(self) -> None:
        measurement = Measurement(configuration="x", latencies_ms=[float(value) for value in range(1, 101)])
        assert measurement.percentile(0.50) == pytest.approx(51.0, abs=1.0)
        assert measurement.percentile(0.95) == pytest.approx(96.0, abs=1.0)

    def test_no_samples_is_zero_rather_than_an_error(self) -> None:
        assert Measurement(configuration="x").percentile(0.95) == 0.0

    def test_the_report_carries_percentiles_not_only_a_mean(self) -> None:
        """A mean hides the case that matters: 4 ms usually and 400 ms on a cache miss has a fine mean."""
        payload = Measurement(configuration="x", latencies_ms=[4.0] * 99 + [400.0]).as_dict()
        assert payload["p99_ms"] == 400.0
        assert payload["mean_ms"] < 10.0


class TestCompare:
    def test_the_planner_must_match_the_scan_on_recall(self) -> None:
        """The scan reads everything, so anything less means the indices lost information."""
        scan = Measurement(configuration="scan", recall={10: 0.90}, latencies_ms=[100.0])
        worse = Measurement(configuration="planner", recall={10: 0.80}, latencies_ms=[10.0])
        verdict = compare([scan, worse])
        assert verdict["recall_at_10"]["passed"] is False
        assert verdict["passed"] is False

    def test_equal_recall_passes(self) -> None:
        scan = Measurement(configuration="scan", recall={10: 0.90}, latencies_ms=[100.0])
        same = Measurement(configuration="planner", recall={10: 0.90}, latencies_ms=[10.0])
        assert compare([scan, same])["passed"] is True

    def test_the_latency_budget_is_deliberately_loose(self) -> None:
        """A real planner is allowed to be slower on a small brain. Pretending otherwise is what pushes it toward
        bad plans at the sizes that matter."""
        scan = Measurement(configuration="scan", recall={10: 0.9}, latencies_ms=[10.0])
        slower = Measurement(configuration="planner", recall={10: 0.9}, latencies_ms=[25.0])
        assert compare([scan, slower])["p95_ms"]["passed"] is True

    def test_far_slower_fails(self) -> None:
        scan = Measurement(configuration="scan", recall={10: 0.9}, latencies_ms=[10.0])
        crawling = Measurement(configuration="planner", recall={10: 0.9}, latencies_ms=[500.0])
        assert compare([scan, crawling])["p95_ms"]["passed"] is False

    def test_a_missing_configuration_is_not_gated_rather_than_passed(self) -> None:
        """Silently passing a gate that could not be evaluated is how a gate stops meaning anything."""
        verdict = compare([Measurement(configuration="scan", recall={10: 0.9})])
        assert verdict["gated"] is False
        assert verdict.get("passed") is not True
