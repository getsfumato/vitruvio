"""Typed compound results: several brains' evidence for one query, in one shape for both modes.

ADR-0015 fixed the shape and this module writes it down. A compound match is a single-brain match plus ``brains``,
the brains that returned it with each one's rank and installation state -- so every variant here subclasses its
single-brain variant from :mod:`vitruvio.runtime.retrieval_result`. Not one match type with ``brains`` optional: a
bundle's match never carries it and a compound's always does, and a key optional in the union would be true of
neither use. ``members`` keeps each brain's own summary, roots and plan, because ``verified_against`` is never merged
upward: a citation names the root of the brain it was verified in.
"""

from typing import TypedDict

from vitruvio.runtime.retrieval_result import (
    CanonicalMatchResult,
    EpisodicMatchResult,
    ExplanationResult,
    ProceduralMatchResult,
    ProvenanceMatchResult,
    SearchPlanResult,
    SemanticMatchResult,
    SourceRefResult,
)


class BrainOriginResult(TypedDict):
    """Where a match came from: the brain, the rank it held there, and that brain's installation of the block."""

    brain: str
    rank: int
    score: str
    resolvable: bool
    superseded_by: str | None
    sources: list[SourceRefResult]


class CanonicalCompoundMatchResult(CanonicalMatchResult):
    brains: list[BrainOriginResult]


class EpisodicCompoundMatchResult(EpisodicMatchResult):
    brains: list[BrainOriginResult]


class SemanticCompoundMatchResult(SemanticMatchResult):
    brains: list[BrainOriginResult]


class ProceduralCompoundMatchResult(ProceduralMatchResult):
    brains: list[BrainOriginResult]


class ProvenanceCompoundMatchResult(ProvenanceMatchResult):
    brains: list[BrainOriginResult]


CompoundMatchResult = (
    CanonicalCompoundMatchResult
    | EpisodicCompoundMatchResult
    | SemanticCompoundMatchResult
    | ProceduralCompoundMatchResult
    | ProvenanceCompoundMatchResult
)
"""One match across brains. Grouped, ``brains`` has one entry; fused, one per brain that returned the block."""


class SkippedBrainResult(TypedDict):
    """A declared brain that was not consulted, and why."""

    brain: str
    reason: str


class CompoundMemberResult(TypedDict):
    """One brain's contribution, without its matches. ``plan`` is ``None`` when no cost-based planner ran there."""

    brain: str
    count: int
    truncated: bool
    all_verified: bool
    verified_against: dict[str, str]
    plan: SearchPlanResult | None


class CompoundSearchResult(TypedDict):
    """Several brains' evidence for one query. A consumer branches on ``fused`` and on nothing else."""

    project: str | None
    brains: list[str]
    skipped: list[SkippedBrainResult]
    fused: bool
    members: list[CompoundMemberResult]
    matches: list[CompoundMatchResult]
    truncated: bool
    all_verified: bool


class CompoundExplainMemberResult(TypedDict):
    """One brain's explanation, from its own planner over its own statistics."""

    brain: str
    explanation: ExplanationResult


class CompoundExplainResult(TypedDict):
    """One explanation per brain. There is no compound plan: composition happens after every brain has answered."""

    project: str | None
    brains: list[str]
    skipped: list[SkippedBrainResult]
    members: list[CompoundExplainMemberResult]


__all__ = [
    "BrainOriginResult",
    "CanonicalCompoundMatchResult",
    "CompoundExplainMemberResult",
    "CompoundExplainResult",
    "CompoundMatchResult",
    "CompoundMemberResult",
    "CompoundSearchResult",
    "EpisodicCompoundMatchResult",
    "ProceduralCompoundMatchResult",
    "ProvenanceCompoundMatchResult",
    "SemanticCompoundMatchResult",
    "SkippedBrainResult",
]
