"""Measuring retrieval, so a claim about it can be checked rather than asserted.

The whole planner rests on one bet: that consulting several indices under a cost model retrieves better than any
single strategy, and that the cost of doing so is worth paying. That is a measurable claim and this is what measures
it -- against a corpus whose answers are known because it was *generated* from them rather than judged afterwards.

Four configurations run over the same queries, and the comparison between them is the point:

* ``scan`` -- the SDK's linear scan. The oracle for correctness and the floor for quality.
* ``lexical`` -- the inverted index alone. Fast, and blind to a synonym.
* ``vector`` -- the vector index alone. Finds synonyms, and misses an exact identifier.
* ``planner`` -- the cost model choosing. The configuration whose recall must not be worse than ``scan``.

The last row is the one that justifies the design. If the planner does not beat the single-index rows on recall, the
single-authority rule is costing latency for nothing, and that is worth knowing rather than assuming.

**Latency is reported as percentiles, never as a mean.** A mean hides the case that matters: a planner that answers in
4 ms most of the time and 400 ms when the cache misses has a fine mean and an unusable p99.

## What this does not measure yet

The corpus builds its queries by sampling terms out of the blocks they answer, so **every query is answerable
lexically** -- the words are literally in the text. Measured at 800 blocks, all four configurations return identical
recall (0.14 / 0.63 / 0.92), and swapping the hashing embedder for a real one through Ollama changes nothing. That is
not a finding about the embedder; it is a property of the queries.

So what these numbers currently establish is the latency comparison and recall *parity with the scan* -- which is the
gate, and a real one: the planner matched the scan's recall at a third of its p95. What they cannot establish is
whether a semantic index retrieves better than a lexical one, because no query in the set requires it.

Making them able to needs **paraphrase judgements**: queries whose terms do not appear in the blocks that answer them.
That is a change to the generator rather than to this module, and until it lands, a row-by-row recall comparison
between two embedders means nothing.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from vitruvio.bench.corpus import Corpus, Judgement

CONFIGURATIONS = ("scan", "lexical", "vector", "planner")
"""Which strategies to compare, in the order they are reported."""

CUTOFFS = (1, 5, 10)
"""Where recall is measured. One, because a caller that shows a single answer only has the first; ten, because that is
a page of results; five, because the gap between one and ten is where a ranking either works or does not."""


def ndcg(found: Sequence[str], judgement: Judgement, k: int = 10) -> float:
    """
    Normalized discounted cumulative gain at ``k``.

    Recall says whether the answer was found; this says whether it was found *near the top*. Both are needed: a plan
    that returns every relevant block at ranks 8, 9 and 10 has perfect recall@10 and is useless to a caller reading
    the first result.

    Binary relevance, since the corpus knows which blocks answer a query and not how well.

    Args:
        found (Sequence[str]): Block identities in rank order.
        judgement (Judgement): The query and its answer set.
        k (int): How deep to score.

    Returns:
        float: In ``[0, 1]``, where 1.0 means every relevant block came back above every irrelevant one.
    """
    import math

    if not judgement.relevant:
        return 1.0
    gain = sum(1.0 / math.log2(rank + 2) for rank, item in enumerate(found[:k]) if item in judgement.relevant)
    ideal = sum(1.0 / math.log2(rank + 2) for rank in range(min(k, len(judgement.relevant))))
    return gain / ideal if ideal else 0.0


@dataclass
class Measurement:
    """
    What one configuration did over the whole query set.

    Attributes:
        configuration (str): Which strategy.
        recall (dict[int, float]): Mean recall at each cutoff.
        ndcg (float): Mean nDCG@10.
        latencies_ms (list[float]): Per-query wall time, kept so percentiles are computed rather than accumulated.
        failures (list[str]): Queries this configuration could not answer at all, with why. Reported rather than
            averaged into a zero, because "the vector index is not installed" and "the vector index found nothing"
            are different results.
    """

    configuration: str
    recall: dict[int, float] = field(default_factory=dict)
    ndcg: float = 0.0
    latencies_ms: list[float] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def percentile(self, fraction: float) -> float:
        """
        One latency percentile in milliseconds.

        Args:
            fraction (float): Between 0 and 1.

        Returns:
            float: The percentile, or 0.0 with no samples.
        """
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        position = min(len(ordered) - 1, int(fraction * len(ordered)))
        return ordered[position]

    def as_dict(self) -> dict[str, Any]:
        """The measurement, as JSON-able data."""
        return {
            "configuration": self.configuration,
            "recall": {f"@{cutoff}": round(value, 4) for cutoff, value in sorted(self.recall.items())},
            "ndcg@10": round(self.ndcg, 4),
            "queries": len(self.latencies_ms),
            "p50_ms": round(self.percentile(0.50), 2),
            "p95_ms": round(self.percentile(0.95), 2),
            "p99_ms": round(self.percentile(0.99), 2),
            "mean_ms": round(statistics.fmean(self.latencies_ms), 2) if self.latencies_ms else 0.0,
            "failures": self.failures[:5],
            "failure_count": len(self.failures),
        }


def measure(
    corpus: Corpus,
    run: Callable[[str, str], list[str]],
    configuration: str,
    *,
    limit: int = 10,
) -> Measurement:
    """
    Run every judged query through one configuration and score the results.

    Args:
        corpus (Corpus): The brain and its ground truth.
        run (Callable[[str, str], list[str]]): Takes a configuration name and a query, returns block identities in
            rank order. Injected rather than imported so the harness does not depend on the runtime -- ``bench`` is a
            development tool and the arrow must not point the other way.
        configuration (str): Which strategy ``run`` should use.
        limit (int): How many results to ask for.

    Returns:
        Measurement: Recall, nDCG and latencies.
    """
    measurement = Measurement(configuration=configuration)
    totals: dict[int, list[float]] = {cutoff: [] for cutoff in CUTOFFS}
    gains: list[float] = []

    for judgement in corpus.judgements:
        started = time.perf_counter()
        try:
            found = run(configuration, judgement.query)
        except Exception as error:
            # Recorded, not averaged into a zero. "This configuration cannot run here" and "this configuration
            # retrieved nothing" are different findings, and collapsing them would make a missing index look like a
            # bad ranking.
            measurement.failures.append(f"{judgement.query!r}: {error}")
            continue
        measurement.latencies_ms.append((time.perf_counter() - started) * 1000)

        for cutoff in CUTOFFS:
            totals[cutoff].append(corpus.recall_at(found, judgement, k=cutoff))
        gains.append(ndcg(found, judgement, k=limit))

    measurement.recall = {cutoff: statistics.fmean(values) if values else 0.0 for cutoff, values in totals.items()}
    measurement.ndcg = statistics.fmean(gains) if gains else 0.0
    return measurement


def compare(measurements: Sequence[Measurement]) -> dict[str, Any]:
    """
    The verdict: whether the planner earned its cost.

    Two gates, and the second is deliberately loose. Recall must be **at least** the scan's, because the scan reads
    everything and anything less means the indices lost information. Latency is allowed to be several times the
    scan's, because on a small brain a real planner *is* slower -- pretending otherwise is what pushes it toward bad
    plans at the sizes that matter.

    Args:
        measurements (Sequence[Measurement]): One per configuration.

    Returns:
        dict[str, Any]: The gates and whether each passed.
    """
    by_name = {item.configuration: item for item in measurements}
    scan = by_name.get("scan")
    planner = by_name.get("planner")
    if scan is None or planner is None:
        return {"gated": False, "reason": "both scan and planner are needed to compare"}

    recall_ok = planner.recall.get(10, 0.0) >= scan.recall.get(10, 0.0) - 1e-9
    budget = scan.percentile(0.95) * 3
    latency_ok = planner.percentile(0.95) <= budget or budget == 0.0

    return {
        "gated": True,
        "recall_at_10": {
            "scan": round(scan.recall.get(10, 0.0), 4),
            "planner": round(planner.recall.get(10, 0.0), 4),
            "passed": recall_ok,
        },
        "p95_ms": {
            "scan": round(scan.percentile(0.95), 2),
            "planner": round(planner.percentile(0.95), 2),
            "budget": round(budget, 2),
            "passed": latency_ok,
        },
        "passed": recall_ok and latency_ok,
    }
