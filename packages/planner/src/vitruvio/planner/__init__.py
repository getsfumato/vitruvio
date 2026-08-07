"""A cost-based query planner for the Boltzmann protocol.

A ``Query`` names no index: the protocol makes queries declarative and leaves index selection to the implementation.
This package is that implementation -- it extracts predicates, classifies intent, enumerates physical plans over the
installed indices, costs them against measured statistics, and explains what it chose.

Recall is part of the objective rather than a hope. A cheap plan that misses the answer is wrong, so expected recall
is estimated per plan, floored per intent as a hard constraint, and traded against latency explicitly. And "no single
index may be treated as authoritative" enters as a **validity rule** that pruning cannot reach around, not as a
preference the optimiser could sell for enough latency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vitruvio.planner.cost import (
    BRUTE_THRESHOLD,
    DEFAULT_CALIBRATION,
    Calibration,
    Objective,
    damped_conjunction,
    damped_union,
    estimate,
    plan_recall,
    vector_recall,
)
from vitruvio.planner.explain import (
    Degradation,
    Explanation,
    OperatorExplain,
    PlanExplain,
    PredicateExplain,
    render_tree,
)
from vitruvio.planner.fusion import RRF_K, Candidate, accumulate, fuse, normalize, render
from vitruvio.planner.intent import Intent, IntentKind, classify
from vitruvio.planner.ir import Estimates, Metrics, Node, Op, Output, Plan, PlanBuilder
from vitruvio.planner.planner import Capabilities, CostBasedPlanner, build_planner

if TYPE_CHECKING:
    from vitruvio.kernel import PlannerConfig

__all__ = [
    "BRUTE_THRESHOLD",
    "DEFAULT_CALIBRATION",
    "RRF_K",
    "Calibration",
    "Candidate",
    "Capabilities",
    "CostBasedPlanner",
    "Degradation",
    "Estimates",
    "Explanation",
    "Intent",
    "IntentKind",
    "Metrics",
    "Node",
    "Objective",
    "Op",
    "OperatorExplain",
    "Output",
    "Plan",
    "PlanBuilder",
    "PlanExplain",
    "PredicateExplain",
    "accumulate",
    "build_planner",
    "classify",
    "damped_conjunction",
    "damped_union",
    "estimate",
    "fuse",
    "normalize",
    "plan_recall",
    "render",
    "render_tree",
    "vector_recall",
]
