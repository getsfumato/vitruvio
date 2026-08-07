"""Synthetic corpora with known ground truth, and a recall/latency harness.

Not published. It exists because a cost-based planner cannot be evaluated on a fixture: below a few hundred blocks an
exhaustive scan legitimately wins every plan comparison, so a small test corpus would pass without exercising a single
index path. The crossover from exhaustive to indexed is the central claim of the cost model, and only a corpus large
enough for it to happen can show it.

Corpora here are generated *from* their relevance judgements, so recall is measurable rather than estimated.
"""

from __future__ import annotations

from vitruvio.bench.corpus import SUBJECTS, VOCABULARY, Corpus, Judgement, generate

__all__ = ["SUBJECTS", "VOCABULARY", "Corpus", "Judgement", "generate"]
