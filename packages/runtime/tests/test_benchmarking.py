"""The benchmark harness, run end to end at a tier the fast suite can afford."""

from __future__ import annotations

from vitruvio.runtime import BrainService


def test_bench_measures_every_configuration_over_a_judged_corpus(service: BrainService) -> None:
    """Tier 100 is below where the indices earn their cost, so the verdict here says nothing about the planner; the
    tier-800 gate in CI does. What this pins is that the harness runs, and what it reports.

    The `service` fixture's `init` writes a `vitruvio.toml` that declares the actor, which is what every project has
    after `init` and what the CI gate, run from a bare checkout, never had. Until now `bench` re-resolved that file
    under an actor of its own and was refused by it."""
    report = service.bench(tier=100, queries=4, limit=5)

    assert report["blocks"] == 100
    assert report["queries"] >= 1
    assert [row["configuration"] for row in report["measurements"]] == ["scan", "lexical", "vector", "planner"]
    assert {"recall_at_10", "p95_ms", "passed"} <= set(report["verdict"])
