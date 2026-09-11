"""A block's payload as it appears on the wire, one vocabulary per memory type.

``Match.content`` in a bundle and the payload a browse row is projected from are the same thing: ``Block.payload()``,
the SDK's ``model_dump(mode="json", exclude_none=True)``. That call fixes the rules here. A field that is ``None`` is
*absent*, never ``null``; a block the store cannot resolve has an empty payload; and which fields exist depends on
the memory type and on the schema version the block was written under -- a version-3 semantic block has no
``statement``, a version-1 one has no ``scheme``. So every content type is ``total=False``: what it states is the
vocabulary and the value types, and presence stays a fact to read with ``.get``. What the discriminated union over
``memory_type`` in :mod:`vitruvio.runtime.retrieval_result` buys is that ``content["statement"]`` on a canonical
match is a type error rather than a ``KeyError`` in production.

Nested objects follow the same ``exclude_none`` rule: a field the SDK declares optional is ``NotRequired`` here,
one it requires is required.

**No ``from __future__ import annotations`` here, unlike every other module in this package.** Under PEP 563 an
annotation is a string when the class body runs, so ``TypedDict`` cannot see ``NotRequired`` and files every
optional key under ``__required_keys__`` instead -- silently, on 3.11. Every type in this module turns on that
distinction, so the import stays out.
"""

from typing import Literal, NotRequired, TypedDict

SemanticKindName = Literal["concept", "fact", "formula", "relation", "constraint", "scheme", "class"]


class ContentRefResult(TypedDict):
    """Bytes a block names: the canonical blob, a normalized view of it, or a derived block's own datum."""

    blob: str
    media_type: str
    size: int


class RelationResult(TypedDict):
    """One edge a semantic block asserts."""

    predicate: str
    target: str


class StepResult(TypedDict):
    """One step of a procedure."""

    action: str
    condition: NotRequired[str]
    alternatives: NotRequired[list[str]]
    uses: NotRequired[list[str]]


class CanonicalContent(TypedDict, total=False):
    """Registered evidence: the bytes, and the normalized view when one was produced."""

    blob: str
    media_type: str
    size: int
    normalized_view: ContentRefResult


class EpisodicContent(TypedDict, total=False):
    """What happened, when."""

    summary: str
    occurred_at: str
    ended_at: str
    context: str
    participants: list[str]
    outcome: str
    evidence: list[str]
    tags: list[str]
    content: ContentRefResult


class SemanticContent(TypedDict, total=False):
    """Versions 1 to 3 in one vocabulary.

    ``statement``, ``subject`` and ``aliases`` exist before version 3, ``scheme`` and ``exclusive`` from it, and a
    version-3 relation may carry no ``label`` at all -- which is why every key is optional and why
    :func:`vitruvio.runtime.browse.identify` names such a block by its predicates.
    """

    kind: SemanticKindName
    label: str
    statement: str
    subject: str
    evidence: list[str]
    relations: list[RelationResult]
    aliases: list[str]
    content: ContentRefResult
    scheme: str
    exclusive: bool


class ProceduralContent(TypedDict, total=False):
    """How to do something: a goal and ordered steps."""

    label: str
    goal: str
    steps: list[StepResult]
    preconditions: list[str]
    success_criteria: list[str]
    subject: str
    evidence: list[str]
    content: ContentRefResult


class RecordActorResult(TypedDict):
    """Who wrote a provenance record.

    Not :class:`vitruvio.runtime.lifecycle_result.ActorResult`: that one is a full dump, in which ``name`` is
    ``null`` when unset. Here it is absent.
    """

    id: str
    kind: str
    name: NotRequired[str]


class CollaboratorResult(RecordActorResult):
    """An assisting party, with the model it ran when it was one."""

    model: NotRequired[str]


class ProducerResult(TypedDict):
    """What derived a block."""

    kind: str
    id: str
    version: NotRequired[str]


class _Attributed(TypedDict):
    """What every record carries. ``assisted_by`` arrived with schema version 2 and a version-1 record has none."""

    actor: RecordActorResult
    at: str
    assisted_by: NotRequired[list[CollaboratorResult]]


class RegistrationRecordResult(_Attributed):
    """A block entering the ledger, and where it came from.

    ``origin`` is the one field a browse row is allowed to rename a canonical block by, which is why it is read
    here and not guessed from the payload: a block's identity must not depend on what anyone called the file.
    """

    record_type: Literal["registration"]
    block: str
    origin: NotRequired[str]
    license: NotRequired[str]
    retention_policy: NotRequired[str]


class DerivationRecordResult(_Attributed):
    """``producer`` exists at version 1 only and ``assisted_by`` is required from version 2 only, so across the
    union neither is required."""

    record_type: Literal["derivation"]
    block: str
    derived_from: list[str]
    producer: NotRequired[ProducerResult]
    task: NotRequired[str]
    locator: NotRequired[str]


class NormalizationRecordResult(_Attributed):
    """Bytes turned into a readable view. ``pipeline`` and ``pipeline_version`` are both required because a
    normalization nobody can reproduce is not evidence, and the version is what makes it reproducible."""

    record_type: Literal["normalization"]
    block: str
    pipeline: str
    pipeline_version: str


class SupersessionRecordResult(_Attributed):
    """One block replacing another. ``reason`` is optional because the protocol does not compel an explanation,
    and a required field would invite an empty one."""

    record_type: Literal["supersession"]
    block: str
    supersedes: str
    reason: NotRequired[str]


class DemotionRecordResult(_Attributed):
    """A block losing standing without being removed, so the ledger keeps both the block and the judgement.
    ``policy`` names the rule that demoted it when a rule did, rather than a person."""

    record_type: Literal["demotion"]
    block: str
    reason: NotRequired[str]
    policy: NotRequired[str]


class ValidationRecordResult(_Attributed):
    """A verdict on a block, with the ``checks`` that produced it.

    ``checks`` is required and ``task`` is not: a verdict without the checks behind it cannot be re-judged later,
    which is the only reason to keep the record at all.
    """

    record_type: Literal["validation"]
    block: str
    verdict: str
    checks: list[str]
    task: NotRequired[str]


class RemovalRecordResult(TypedDict):
    """Version 1 forever, by protocol: every verifier must decode a removal, so it never gained ``assisted_by``."""

    record_type: Literal["removal"]
    blocks: list[str]
    mechanism: str
    memory_type: str
    actor: RecordActorResult
    at: str
    reason: str
    policy: NotRequired[str]
    cascaded_from: NotRequired[str]
    resulting_roots: NotRequired[dict[str, str]]


ProvenanceRecordResult = (
    RegistrationRecordResult
    | DerivationRecordResult
    | NormalizationRecordResult
    | SupersessionRecordResult
    | DemotionRecordResult
    | ValidationRecordResult
    | RemovalRecordResult
)
"""One entry in the provenance ledger. Comparing ``record_type`` narrows it."""


class ProvenanceContent(TypedDict, total=False):
    """A provenance block is its record."""

    record: ProvenanceRecordResult


__all__ = [
    "CanonicalContent",
    "CollaboratorResult",
    "ContentRefResult",
    "DemotionRecordResult",
    "DerivationRecordResult",
    "EpisodicContent",
    "NormalizationRecordResult",
    "ProceduralContent",
    "ProducerResult",
    "ProvenanceContent",
    "ProvenanceRecordResult",
    "RecordActorResult",
    "RegistrationRecordResult",
    "RelationResult",
    "RemovalRecordResult",
    "SemanticContent",
    "SemanticKindName",
    "StepResult",
    "SupersessionRecordResult",
    "ValidationRecordResult",
]
