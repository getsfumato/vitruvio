"""The typed reconciliation result contract at the runtime boundary."""

from __future__ import annotations

import json
from typing import Any, cast, get_type_hints

from boltzmann.reconcile import ReconcilePlan

from vitruvio.runtime.ops.reconcile import ReconcileOps
from vitruvio.runtime.reconcile_result import (
    PlanView,
    ReconcilePlanResult,
    ReconcileStatusEnvelope,
    serialize_plan,
)


class _Plan:
    """The SDK surface the serializer consumes, kept small so the contract itself is visible."""

    is_noop = False
    is_clean = False
    is_blocked = True
    excluded: dict[Any, list[Any]] = {}

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return {
            "ancestor": "sha256:ancestor",
            "theirs": "sha256:theirs",
            "modules": {},
            "incoming": {
                "verdicts": [
                    {
                        "block": "sha256:block",
                        "memory_type": "semantic",
                        "status": "rejected",
                        "issues": [],
                        "conflicts_with": [],
                        "missing_evidence": {},
                    }
                ]
            },
            "cascaded": {},
            "withdrawn": {"canonical": ["sha256:ours"]},
            "attribution": {},
            "collapsed": 1,
            "replayable": 1,
            "untransferred": [],
            "carried": {},
        }


def test_plan_serialization_preserves_json_and_surfaces_branching_invariants() -> None:
    result = serialize_plan(cast(ReconcilePlan, _Plan()))

    assert result["is_noop"] is False
    assert result["is_clean"] is False
    assert result["is_blocked"] is True
    assert result["excluded"] == {}
    assert json.loads(json.dumps(result))["incoming"]["verdicts"][0]["block"] == "sha256:block"


def test_plan_view_centrally_interprets_questions_and_withdrawals() -> None:
    result = serialize_plan(cast(ReconcilePlan, _Plan()))
    viewed = PlanView(result)

    assert [entry["block"] for entry in viewed.questions] == ["sha256:block"]
    assert viewed.withdrawn_count == 1


def test_runtime_annotations_expose_the_typed_vertical_slice() -> None:
    assert get_type_hints(ReconcileOps.plan)["return"] is ReconcilePlanResult
    assert get_type_hints(ReconcileOps.status)["return"] is ReconcileStatusEnvelope
