"""The statistics catalogue: what vitruvio's indices measure and the query planner costs against.

Its own distribution to break a cycle. Indices *produce* fragments during the pass they are already making over
the blocks; the planner *consumes* them to estimate selectivity, cardinality and recall. Neither should import
the other, so the vocabulary they share lives here.

Statistics are derived state: they live beside the indices under ``<brain>/.vitruvio/`` and are versioned against
the module's Merkle root **plus** a fingerprint of the leaves actually indexed -- because a redaction destroys
bytes without changing the composition, and only the fingerprint sees that.

Every estimate carries whether it was measured or interpolated. A planner that could not tell those apart would
trade a real number against a guess as though they were the same.
"""

from __future__ import annotations

from vitruvio.stats.catalog import (
    CATALOG_SCHEMA,
    HISTOGRAM_BUCKETS,
    TOP_VALUES,
    ColumnStats,
    Estimate,
    Freshness,
    GraphStats,
    ModuleStats,
    StatsVersion,
    TermStats,
    TimeStats,
    VectorStats,
)
from vitruvio.stats.fragment import StatsFragment, leaf_fingerprint, load, merge, save

__all__ = [
    "CATALOG_SCHEMA",
    "HISTOGRAM_BUCKETS",
    "TOP_VALUES",
    "ColumnStats",
    "Estimate",
    "Freshness",
    "GraphStats",
    "ModuleStats",
    "StatsFragment",
    "StatsVersion",
    "TermStats",
    "TimeStats",
    "VectorStats",
    "leaf_fingerprint",
    "load",
    "merge",
    "save",
]
