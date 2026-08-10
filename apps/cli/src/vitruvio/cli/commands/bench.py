"""``vitruvio bench`` -- measuring retrieval instead of asserting it.

The planner rests on a claim: that consulting several indices under a cost model retrieves better than any single
strategy, and by enough to justify the latency. This is what checks it, against a corpus whose answers are known
because it was generated from them.

It runs on a **generated** brain, not yours. Recall can only be measured where the answers are known, and pointing
this at a real brain would produce latency numbers with no way to say whether the results were right. What it does
take from your project is the *configuration* -- your embedder, your indices -- so the numbers describe your setup.
Which is what makes it actionable: switch to a different embedding model, re-run, and read the recall column.
"""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from vitruvio.cli.context import current
from vitruvio.kernel import ExitCode, VitruvioError

app = App(
    name="bench",
    help="Measure recall and latency against a corpus with known answers.",
    result_action="return_value",
    exit_on_error=False,
)


@app.default
def bench(
    *,
    tier: int = 1000,
    seed: int = 1234,
    queries: int = 24,
    limit: int = 10,
    gate: bool = False,
) -> ExitCode:
    """Generate a corpus and compare four retrieval strategies over it.

    The four rows are `scan` (the SDK's linear scan — the oracle), `lexical` (the inverted index alone), `vector`
    (the vector index alone) and `planner` (the cost model choosing). The last one is the design under test: if it
    does not beat the single-index rows on recall, the single-authority rule is buying latency and nothing else.

    Latency is reported as percentiles, never as a mean — a mean hides the p99 that makes something unusable.

    Parameters
    ----------
    tier
        Corpus size in blocks. Below a few hundred an exhaustive scan legitimately wins, so a small tier measures
        the scan rather than the indices.
    seed
        Makes the corpus reproducible, so two runs are comparable.
    queries
        How many judged queries to run.
    limit
        Results per query.
    gate
        Exit non-zero when the planner fails either gate: recall@10 at least the scan's, and p95 within 3x of it.
        For CI.
    """
    console = current().console
    result = current().service(require_brain=False).bench(tier=tier, seed=seed, queries=queries, limit=limit)

    lines = [
        f"blocks      {result['blocks']}",
        f"queries     {result['queries']}",
        f"embedder    {result['embedder']}",
        "",
        f"{'configuration':<14}{'r@1':>7}{'r@5':>7}{'r@10':>7}{'nDCG':>7}{'p50':>9}{'p95':>9}{'p99':>9}",
    ]
    for row in result["measurements"]:
        recall = row["recall"]
        lines.append(
            f"{row['configuration']:<14}"
            f"{recall.get('@1', 0):>7.2f}{recall.get('@5', 0):>7.2f}{recall.get('@10', 0):>7.2f}"
            f"{row['ndcg@10']:>7.2f}"
            f"{row['p50_ms']:>8.1f}m{row['p95_ms']:>8.1f}m{row['p99_ms']:>8.1f}m"
        )
        if row["failure_count"]:
            console.warn(
                f"{row['configuration']}: {row['failure_count']} queries could not be answered at all -- "
                f"{row['failures'][0] if row['failures'] else ''}"
            )

    verdict = result["verdict"]
    if verdict.get("gated"):
        recall = verdict["recall_at_10"]
        latency = verdict["p95_ms"]
        recall_verdict = "ok" if recall["passed"] else "FAIL"
        latency_verdict = "ok" if latency["passed"] else "FAIL"
        lines += [
            "",
            f"recall@10   planner {recall['planner']:.3f} vs scan {recall['scan']:.3f}   {recall_verdict}",
            f"p95         planner {latency['planner']:.1f}ms vs budget {latency['budget']:.1f}ms   {latency_verdict}",
        ]

    console.emit("bench", result, lines=lines)
    if gate and not verdict.get("passed", False):
        raise VitruvioError(
            "the planner did not clear both gates",
            hint="recall@10 must be at least the scan's, and p95 within 3x of it; the rows above show which failed",
        )
    return ExitCode.OK


@app.command(name="corpus")
def corpus(
    path: Annotated[str, Parameter(name=["--into"])],
    *,
    tier: int = 1000,
    seed: int = 1234,
    queries: int = 24,
) -> ExitCode:
    """Write a generated corpus to disk and keep it.

    What `bench` builds in a temporary directory and throws away. Useful for looking at what is being measured, or
    for running `query explain` against a brain whose answers you know.

    Parameters
    ----------
    path
        Where to create the brain.
    tier
        How many blocks.
    seed
        Makes it reproducible.
    queries
        How many judged queries to build.
    """
    from pathlib import Path

    try:
        from vitruvio.bench.corpus import generate
    except ImportError as error:
        raise VitruvioError("the benchmark harness is not installed", hint="install vitruvio[bench]") from error

    console = current().console
    target = Path(path).expanduser().resolve()
    built = generate(target, blocks=tier, seed=seed, queries=queries)

    lines = [
        f"brain       {target}",
        f"blocks      {built.blocks}",
        f"queries     {len(built.judgements)}",
        "",
        *(f"  {item.query}" for item in built.judgements[:5]),
    ]
    return console.emit(
        "bench.corpus",
        {
            "brain": str(target),
            "blocks": built.blocks,
            "judgements": [
                {"query": item.query, "subject": item.subject, "relevant": sorted(item.relevant)}
                for item in built.judgements
            ],
        },
        lines=lines,
    )
