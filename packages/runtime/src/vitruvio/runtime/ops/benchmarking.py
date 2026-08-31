"""The recall-and-latency harness, over a generated corpus.

Builds its own brain rather than measuring the caller's, and that is the point: the numbers have to come from a
corpus with known judgements, so recall is a measurement rather than an assertion. The brain is a temporary
directory, and the *configuration* is the caller's -- so the embedder under test is the one actually configured,
which is what makes "would switching to Ollama help" answerable by re-running.

It holds its own :class:`~vitruvio.runtime.session.BrainSession` over that corpus config, and drives the index
build through :class:`~vitruvio.runtime.ops.indices.IndexOps` directly. Going through ``BrainService`` -- which is
what this did while it lived on the facade -- would make this module import the class that imports it.
"""

from __future__ import annotations

from typing import Any

from boltzmann.blocks.memory_type import MemoryType

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.ops.indices import IndexOps
from vitruvio.runtime.session import BrainSession


class BenchmarkOps:
    """The benchmark harness, as an operation."""

    def __init__(self, session: BrainSession) -> None:
        """
        Args:
            session (BrainSession): The shared session. Only its configuration is used -- the measurement runs
                against a corpus brain of its own.
        """
        self.session = session

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration, read through the session that owns it."""
        return self.session.config

    def bench(self, *, tier: int = 1000, seed: int = 1234, queries: int = 24, limit: int = 10) -> dict[str, Any]:
        """
        Generate a corpus with known answers, and measure four retrieval strategies over it.

        Runs against a **generated** brain rather than the configured one, and that is the point: recall can only be
        measured where the answers are known, and they are known here because the corpus was built from them. Pointing
        this at a real brain would produce latency numbers and no way to say whether the results were right.

        Args:
            tier (int): Corpus size, in blocks. Below a few hundred an exhaustive scan legitimately wins, so a small tier
                measures the scan rather than the indices -- which is why the default is above that.
            seed (int): Makes the corpus reproducible, so two runs are comparable.
            queries (int): How many judged queries.
            limit (int): Results per query.

        Returns:
            dict[str, Any]: One measurement per configuration, and the verdict on whether the planner earned its cost.
        """
        import tempfile
        from pathlib import Path

        from boltzmann.query.request import Query, QueryFilters, QueryHints, RetrievalMode

        from vitruvio.bench.corpus import generate
        from vitruvio.bench.harness import CONFIGURATIONS, compare, measure

        with tempfile.TemporaryDirectory(prefix="vitruvio-bench-") as workspace:
            root = Path(workspace) / "corpus"
            with translated():
                corpus = generate(root, blocks=tier, seed=seed, queries=queries)

            # A service over the generated brain, sharing this project's embedder and index configuration -- so the
            # numbers describe *your* setup rather than a default one. Which is what makes the comparison actionable:
            # switching to Ollama and re-running is how you find out whether it helped.
            from vitruvio.kernel import resolve as resolve_config

            config = resolve_config(brain=root, config=self.config.config_file, actor_id="vitruvio/bench")
            corpus_session = BrainSession(config)

            index_report = IndexOps(corpus_session).index_build()

            # A hint per configuration. `lexical` excludes the vector generator and `semantic` requires it, which is
            # what isolates each index -- and `auto` lets the cost model choose, which is the row under test.
            modes = {
                "scan": RetrievalMode.AUTO,
                "lexical": RetrievalMode.LEXICAL,
                "vector": RetrievalMode.SEMANTIC,
                "planner": RetrievalMode.AUTO,
            }

            def run(configuration: str, text: str) -> list[str]:
                """One query under one strategy, returning block identities in rank order."""
                brain = corpus_session.brain(Capability.RETRIEVE)
                query = Query(
                    text=text,
                    filters=QueryFilters(memory_types=[MemoryType.SEMANTIC]),
                    hints=QueryHints(limit=limit, mode=modes[configuration]),
                )
                if configuration == "scan":
                    from boltzmann.query.scan import scan

                    bundle = scan(query, brain.modules())
                else:
                    bundle = brain.search(query)
                return [str(match.block_id) for match in bundle.matches]

            measurements = [measure(corpus, run, name, limit=limit) for name in CONFIGURATIONS]

        return {
            "blocks": corpus.blocks,
            "queries": len(corpus.judgements),
            "seed": seed,
            "embedder": self.config.project.text_embedder.uri,
            "indices_built": index_report.get("written"),
            "measurements": [item.as_dict() for item in measurements],
            "verdict": compare(measurements),
        }
