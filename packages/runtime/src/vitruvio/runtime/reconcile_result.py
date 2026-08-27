"""Typed reconciliation results, their JSON serialization, and shared interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

from boltzmann.reconcile import ReconcilePlan, ReconcileResult, ReconcileStatus


class ValidationIssueResult(TypedDict):
    """One validation issue attached to an incoming block."""

    code: str
    message: str


class ReconcileVerdictResult(TypedDict):
    """The gate's judgment of one incoming block."""

    block: str
    memory_type: str
    status: str
    issues: list[ValidationIssueResult]
    conflicts_with: list[str]
    missing_evidence: dict[str, str]


class IncomingResult(TypedDict):
    """All incoming reconciliation verdicts."""

    verdicts: list[ReconcileVerdictResult]


class AttributionResult(TypedDict):
    """The lineage consequences of one strategy."""

    strategy: str
    parents: int
    snapshots_written: int
    keeps_their_snapshots: bool
    mints_new_identities: bool
    their_signatures_survive: bool


class ReconcilePlanResult(TypedDict):
    """The stable JSON contract for a reconciliation plan."""

    ancestor: str
    theirs: str
    modules: dict[str, dict[str, Any]]
    incoming: IncomingResult
    cascaded: dict[str, list[str]]
    withdrawn: dict[str, list[str]]
    attribution: dict[str, AttributionResult]
    collapsed: int
    replayable: int
    untransferred: list[str]
    carried: dict[str, dict[str, Any]]
    is_noop: bool
    is_clean: bool
    is_blocked: bool
    excluded: dict[str, list[str]]


class ResolutionResult(TypedDict):
    """One persisted answer to a reconciliation question."""

    kind: str
    prefer: str | None
    actor: dict[str, Any]
    reason: str | None
    at: str


class ReconcileStateResult(TypedDict):
    """The persisted state of an open reconciliation."""

    boltzmann: int
    theirs: str
    ancestor: str
    strategy: str
    actor: dict[str, Any]
    reason: str
    head: str
    resolutions: dict[str, ResolutionResult]
    accepted_removals: dict[str, Any] | None


class ReconcileStatusResult(TypedDict):
    """The stable JSON contract for an open reconciliation."""

    state: ReconcileStateResult
    plan: ReconcilePlanResult
    unresolved: list[str]
    resolved: list[str]
    withdrawn: dict[str, list[str]]
    removals_accepted: bool
    is_resolved: bool


class ReconcileCommittedResult(TypedDict):
    """The stable JSON contract for a committed reconciliation."""

    halted: Literal[False]
    snapshot: str
    strategy: str
    attribution: AttributionResult
    parents: list[str]
    snapshots: list[str]
    roots: dict[str, str]
    admitted: dict[str, list[str]]
    excluded: dict[str, list[str]]
    plan: ReconcilePlanResult


class ReconcileHaltedResult(ReconcileStatusResult):
    """An operation that opened questions rather than committing."""

    halted: Literal[True]
    strategy: str
    open: Literal[True]


class ReconcileClosedStatusResult(TypedDict):
    """The ordinary status response when no reconciliation is open."""

    open: Literal[False]


class ReconcileOpenStatusResult(ReconcileStatusResult):
    """A status response with an open reconciliation."""

    open: Literal[True]


ReconcileOperationResult = ReconcileCommittedResult | ReconcileHaltedResult
ReconcileStatusEnvelope = ReconcileClosedStatusResult | ReconcileOpenStatusResult


def serialize_plan(plan: ReconcilePlan) -> ReconcilePlanResult:
    """Serialize a plan and surface every computed invariant callers branch on."""
    payload = cast(ReconcilePlanResult, plan.model_dump(mode="json"))
    payload["is_noop"] = plan.is_noop
    payload["is_clean"] = plan.is_clean
    payload["is_blocked"] = plan.is_blocked
    payload["excluded"] = {kind.value: [str(block) for block in blocks] for kind, blocks in plan.excluded.items()}
    return payload


def serialize_committed(result: ReconcileResult) -> ReconcileCommittedResult:
    """Serialize a committed reconciliation without embedding its snapshot document."""
    payload = result.model_dump(mode="json")
    payload["snapshot"] = str(result.snapshot.digest)
    payload["plan"] = serialize_plan(result.plan)
    return cast(ReconcileCommittedResult, {"halted": False, **payload})


def serialize_status(status: ReconcileStatus) -> ReconcileStatusResult:
    """Serialize an open status with its recomputed plan and readiness invariant."""
    payload = status.model_dump(mode="json")
    payload["plan"] = serialize_plan(status.plan)
    payload["is_resolved"] = status.is_resolved
    return cast(ReconcileStatusResult, payload)


def halted_result(strategy: str, status: ReconcileStatusResult) -> ReconcileHaltedResult:
    """Attach operation state to a status without changing its established JSON fields."""
    return cast(ReconcileHaltedResult, {"halted": True, "strategy": strategy, "open": True, **status})


def open_status(status: ReconcileStatusResult) -> ReconcileOpenStatusResult:
    """Attach the status discriminator used by the public runtime operation."""
    return cast(ReconcileOpenStatusResult, {"open": True, **status})


@dataclass(frozen=True, slots=True)
class PlanView:
    """Shared interpretation of the nested plan contract for CLI and TUI callers."""

    result: ReconcilePlanResult

    @property
    def verdicts(self) -> list[ReconcileVerdictResult]:
        """Every incoming verdict."""
        return self.result["incoming"]["verdicts"]

    @property
    def questions(self) -> list[ReconcileVerdictResult]:
        """Verdicts that require an operator decision."""
        return [entry for entry in self.verdicts if entry["status"] != "validated"]

    @property
    def withdrawn(self) -> dict[str, list[str]]:
        """Blocks the reconciliation would remove, by module."""
        return self.result["withdrawn"]

    @property
    def withdrawn_count(self) -> int:
        """How many existing blocks would leave."""
        return sum(len(blocks) for blocks in self.withdrawn.values())


@dataclass(frozen=True, slots=True)
class StatusView:
    """Shared interpretation of an open reconciliation status."""

    result: ReconcileStatusResult

    @property
    def state(self) -> ReconcileStateResult:
        """The persisted reconciliation state."""
        return self.result["state"]

    @property
    def plan(self) -> PlanView:
        """The recomputed plan."""
        return PlanView(self.result["plan"])

    @property
    def questions(self) -> list[ReconcileVerdictResult]:
        """The questions displayed by both reconciliation interfaces."""
        return self.plan.questions

    @property
    def decisions(self) -> dict[str, ResolutionResult]:
        """Answers already persisted, keyed by incoming block."""
        return self.state["resolutions"]

    @property
    def withdrawn(self) -> dict[str, list[str]]:
        """Blocks that will leave if the reconciliation concludes."""
        return self.result["withdrawn"]

    @property
    def withdrawn_count(self) -> int:
        """How many existing blocks would leave."""
        return sum(len(blocks) for blocks in self.withdrawn.values())


__all__ = [
    "AttributionResult",
    "PlanView",
    "ReconcileClosedStatusResult",
    "ReconcileCommittedResult",
    "ReconcileHaltedResult",
    "ReconcileOpenStatusResult",
    "ReconcileOperationResult",
    "ReconcilePlanResult",
    "ReconcileStatusEnvelope",
    "ReconcileStatusResult",
    "ReconcileVerdictResult",
    "StatusView",
    "halted_result",
    "open_status",
    "serialize_committed",
    "serialize_plan",
    "serialize_status",
]
