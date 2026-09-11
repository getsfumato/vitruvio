"""The retrieval slice mirrors models it does not import, so a test has to say they agree.

Two regimes, two rules (see ``block_result`` and ``retrieval_result``): a payload dump drops ``None`` fields, so its
types state vocabulary and mark presence with ``NotRequired``; a full dump keeps every field, so its types are total.
The SDK's block classes, the provenance records and the planner's explanation are imported here, where the import
cost does not matter, and compared field by field. The recorded wire shape is walked through the types too: a path
the suite has observed that the type cannot reach is a field the contract forgot.
"""

from __future__ import annotations

import json
import types
from functools import reduce
from operator import or_
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints, is_typeddict

import pytest
from boltzmann.authenticity.authenticator import Authorship
from boltzmann.blocks import provenance
from boltzmann.blocks.base import Block
from boltzmann.blocks.canonical import NormalizedView
from boltzmann.blocks.content import ContentRef
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.blocks.procedural import Step
from boltzmann.blocks.semantic import Relation, SemanticBlock, SemanticKind
from boltzmann.query.evidence import EvidenceBundle, Match, SourceRef
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from vitruvio.planner import explain
from vitruvio.runtime import block_result, retrieval_result, wire
from vitruvio.runtime.ops.retrieval import RetrievalOps
from vitruvio.runtime.query_diagnostics import query_diagnostics
from vitruvio.runtime.retrieval_result import (
    DiagnosticsResult,
    ExplanationResult,
    SearchResult,
    VectorPointResult,
    VectorScopeResult,
)

GOLDEN = json.loads((Path(__file__).resolve().parents[3] / "tests" / "operation_shapes.json").read_text("utf-8"))

CONTENT: dict[MemoryType, type] = {
    MemoryType.CANONICAL: block_result.CanonicalContent,
    MemoryType.EPISODIC: block_result.EpisodicContent,
    MemoryType.SEMANTIC: block_result.SemanticContent,
    MemoryType.PROCEDURAL: block_result.ProceduralContent,
    MemoryType.PROVENANCE: block_result.ProvenanceContent,
}

RECORDS: dict[str, type] = {
    "registration": block_result.RegistrationRecordResult,
    "derivation": block_result.DerivationRecordResult,
    "normalization": block_result.NormalizationRecordResult,
    "supersession": block_result.SupersessionRecordResult,
    "demotion": block_result.DemotionRecordResult,
    "validation": block_result.ValidationRecordResult,
    "removal": block_result.RemovalRecordResult,
}

MIRRORS: list[tuple[type, type[BaseModel]]] = [
    (retrieval_result.SourceRefResult, SourceRef),
    (retrieval_result.AuthorshipResult, Authorship),
    (retrieval_result.OperatorResult, explain.OperatorExplain),
    (retrieval_result.DegradationResult, explain.Degradation),
    (retrieval_result.IntentResult, explain.IntentExplain),
    (retrieval_result.PredicateResult, explain.PredicateExplain),
    (retrieval_result.PlanExplainResult, explain.PlanExplain),
    (retrieval_result.StatsResult, explain.StatsExplain),
    (retrieval_result.ExplanationResult, explain.Explanation),
]


def _keys(typed: type) -> set[str]:
    return set(get_type_hints(typed))


def _required(typed: Any) -> set[str]:
    return set(typed.__required_keys__)


def _always_present(field: FieldInfo) -> bool:
    """In a payload dump a key is present exactly when its value is not ``None``: required, or defaulting to one."""
    has_default = field.default is not PydanticUndefined and field.default is not None
    return field.is_required() or field.default_factory is not None or has_default


def _present(annotation: Any) -> Any:
    """``X | None`` as ``X``; anything else as it is."""
    if get_origin(annotation) in (Union, types.UnionType):
        present = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(present) == 1:
            return present[0]
    return annotation


def _inner(annotation: Any) -> Any:
    """The type behind ``list[...]`` and ``X | None``. Shares :func:`_present` so the two cannot prune differently."""
    if get_origin(annotation) is list:
        return _inner(get_args(annotation)[0])
    pruned = _present(annotation)
    return _inner(pruned) if pruned is not annotation else annotation


def _members(entry: Any) -> dict[str, type[BaseModel]]:
    """The record classes behind one of the SDK's ``Annotated`` discriminated unions, by ``record_type``."""
    return {
        get_args(member.model_fields["record_type"].annotation)[0]: member for member in get_args(get_args(entry)[0])
    }


class TestPayloadRegime:
    @pytest.mark.parametrize("memory_type", list(MemoryType), ids=lambda kind: kind.value)
    def test_a_content_type_is_the_vocabulary_of_every_schema_version(self, memory_type: MemoryType) -> None:
        """Nothing is required: the payload is empty when the block is not resolvable, and a field one version
        requires another version does not have."""
        fields = set().union(*(set(schema.model_fields) for schema in Block.schemas(memory_type)))
        assert _keys(CONTENT[memory_type]) == fields
        assert _required(CONTENT[memory_type]) == set()

    @pytest.mark.parametrize(
        ("typed", "model"),
        [
            (block_result.ContentRefResult, ContentRef),
            (block_result.ContentRefResult, NormalizedView),
            (block_result.RelationResult, Relation),
            (block_result.StepResult, Step),
            (block_result.RecordActorResult, provenance.Actor),
            (block_result.CollaboratorResult, provenance.Collaborator),
            (block_result.ProducerResult, provenance.Producer),
        ],
        ids=lambda item: item.__name__,
    )
    def test_a_nested_object_requires_what_the_model_never_leaves_none(
        self, typed: type, model: type[BaseModel]
    ) -> None:
        assert _keys(typed) == set(model.model_fields)
        assert _required(typed) == {name for name, field in model.model_fields.items() if _always_present(field)}

    def test_the_semantic_kinds_are_the_protocols(self) -> None:
        assert set(get_args(block_result.SemanticKindName)) == {kind.value for kind in SemanticKind}


class TestProvenanceRecords:
    def test_the_union_names_every_record_type_once(self) -> None:
        assert set(RECORDS) == set(_members(provenance.ProvenanceEntry))
        assert set(get_args(block_result.ProvenanceRecordResult)) == set(RECORDS.values())

    @pytest.mark.parametrize("record_type", sorted(RECORDS))
    def test_a_record_is_the_union_of_its_versions(self, record_type: str) -> None:
        """Version 2 added ``assisted_by`` and dropped a derivation's ``producer``; a removal stayed at version 1.
        Across the union a key is required only when every version has it and never leaves it ``None``."""
        versions = [
            members[record_type]
            for members in (_members(provenance.ProvenanceEntry), _members(provenance.ProvenanceEntryV2))
            if record_type in members
        ]
        typed = RECORDS[record_type]
        assert _keys(typed) == set().union(*(set(version.model_fields) for version in versions))
        present = [
            {name for name, field in version.model_fields.items() if _always_present(field)} for version in versions
        ]
        assert _required(typed) == set.intersection(*present)
        assert get_type_hints(typed)["record_type"] == Literal[record_type]


class TestFullDumpRegime:
    @pytest.mark.parametrize(("typed", "model"), MIRRORS, ids=lambda item: item.__name__)
    def test_a_mirror_carries_every_field_and_requires_all_of_them(self, typed: type, model: type[BaseModel]) -> None:
        assert _keys(typed) == set(model.model_fields)
        assert _required(typed) == _keys(typed)

    @pytest.mark.parametrize(("typed", "model"), MIRRORS, ids=lambda item: item.__name__)
    def test_a_nested_model_is_mirrored_by_the_type_the_field_names(self, typed: type, model: type[BaseModel]) -> None:
        mirrored = {model: typed for typed, model in MIRRORS}
        hints = get_type_hints(typed)
        for name, field in model.model_fields.items():
            nested = _inner(field.annotation)
            if isinstance(nested, type) and issubclass(nested, BaseModel):
                assert _inner(hints[name]) is mirrored[nested], name

    def test_match_variants_share_the_sdk_match_fields_and_split_on_memory_type(self) -> None:
        variants = get_args(retrieval_result.MatchResult)
        for variant in variants:
            assert _keys(variant) == set(Match.model_fields) == _required(variant)
        discriminators = {get_args(get_type_hints(variant)["memory_type"])[0] for variant in variants}
        assert discriminators == {kind.value for kind in MemoryType}
        assert {get_type_hints(variant)["content"] for variant in variants} == set(CONTENT.values())

    def test_the_bundle_is_the_sdk_bundle_plus_what_search_adds(self) -> None:
        assert _keys(SearchResult) == set(EvidenceBundle.model_fields) | {"all_verified", "plan", "diagnostics"}
        assert set(SearchResult.__optional_keys__) == {"plan", "diagnostics"}

    def test_json_mode_writes_estimation_error_keys_as_strings(self) -> None:
        """The model keys it by node id; ``model_dump(mode="json")`` renders the key, so the wire type says ``str``."""
        assert get_type_hints(ExplanationResult)["estimation_error"] == dict[str, float]
        explanation = explain.Explanation(
            query_digest="q",
            intent=explain.IntentExplain(kind="lookup"),
            chosen=explain.PlanExplain(signature="s"),
            estimation_error={3: 0.5},
        )
        assert explanation.model_dump(mode="json")["estimation_error"] == {"3": 0.5}


class TestAnnotations:
    def test_runtime_annotations_expose_the_retrieval_slice(self) -> None:
        assert get_type_hints(RetrievalOps.search)["return"] is SearchResult
        assert get_type_hints(RetrievalOps.explain)["return"] is ExplanationResult
        assert get_type_hints(wire.evidence)["return"] is SearchResult
        assert get_type_hints(query_diagnostics)["return"] is DiagnosticsResult


_MISSING = object()


def _hints(typed: Any) -> dict[str, Any]:
    """The keys at a type: a TypedDict's, or every member's when it is a union of them, merged key by key."""
    members = get_args(typed) if get_origin(typed) in (Union, types.UnionType) else (typed,)
    found: dict[str, list[Any]] = {}
    for member in members:
        if is_typeddict(member):
            for name, hint in get_type_hints(member).items():
                found.setdefault(name, []).append(hint)
    return {name: reduce(or_, hints) for name, hints in found.items()}


def _step(current: Any, name: str) -> Any:
    """The type under ``name``. A mapping keyed by data admits any name; ``Any`` admits anything below it."""
    current = _present(current)
    if current is Any:
        return Any
    if get_origin(current) is dict:
        return get_args(current)[1]
    return _hints(current).get(name, _MISSING)


def _reachable(path: str, root: Any) -> bool:
    current: Any = root
    for segment in path.split("."):
        name, _, listed = segment.partition("[")
        current = _step(current, name)
        if current is _MISSING:
            return False
        if listed and current is not Any:
            current = _present(current)
            if get_origin(current) is not list:
                return False
            current = get_args(current)[0]
    return True


class TestTheRecordedShapeIsReachable:
    """Every path the suite has observed resolves through the type; a field the type forgot fails here."""

    @pytest.mark.parametrize(("operation", "root"), [("search", SearchResult), ("explain", ExplanationResult)])
    def test_every_recorded_path_resolves(self, operation: str, root: type) -> None:
        paths = [path for path in GOLDEN[operation]["paths"] if path != "."]
        assert [path for path in paths if not _reachable(path, root)] == []

    def test_the_walk_would_notice_a_path_the_type_does_not_have(self) -> None:
        assert not _reachable("matches[].content.answer", SearchResult)
        assert not _reachable("plan.operators[].scope[]", SearchResult)
        assert _reachable("plan.operators[].params.anything.at.all", SearchResult)

    def test_every_recorded_content_path_belongs_to_at_least_one_memory_type(self) -> None:
        """Walked through ``MatchResult`` the five vocabularies are one bag, so this walks them apart.

        A path recorded under ``matches[]`` came from a match of one memory type, so it has to resolve through one
        variant end to end. A content type that grew a key belonging to another would still pass the union walk --
        every key would be in the bag -- and fails here.
        """
        prefix = "matches[]."
        recorded = [path[len(prefix) :] for path in GOLDEN["search"]["paths"] if path.startswith(prefix)]
        assert recorded, "the corpus observed no match at all"
        variants = get_args(retrieval_result.MatchResult)
        assert [path for path in recorded if not any(_reachable(path, variant) for variant in variants)] == []

    def test_a_variant_does_not_answer_for_another_variants_content(self) -> None:
        """What the discriminator buys, stated as a test rather than trusted: narrowing on ``memory_type`` picks
        one content vocabulary, so a procedural match's ``steps`` is not reachable on a canonical one."""
        assert _reachable("content.steps[].action", retrieval_result.ProceduralMatchResult)
        assert not _reachable("content.steps[].action", retrieval_result.CanonicalMatchResult)
        assert _reachable("content.blob", retrieval_result.CanonicalMatchResult)
        assert not _reachable("content.blob", retrieval_result.ProceduralMatchResult)


class TestDiagnosticsProducers:
    """A vector projection is built in ``vitruvio.indices`` as a plain dictionary, and no search in the fast suite
    selects the vector index, so the golden has never seen one. Its keys are pinned against the type here."""

    def test_a_projection_carries_the_declared_fields(self) -> None:
        from vitruvio.embeddings import FakeEmbedder
        from vitruvio.indices import VectorIndex
        from vitruvio.indices.testing import MemoryContent

        blocks = [
            SemanticBlock(kind=SemanticKind.CONCEPT, label=f"Concept {n}", statement=f"Statement {n} about fourier")
            for n in range(3)
        ]
        index = VectorIndex(MemoryType.SEMANTIC, None, embedder=FakeEmbedder(dimensions=32))
        index.build(blocks, MemoryContent())

        projected = index.project_2d("fourier", [str(block.block_id) for block in blocks])

        assert set(projected) | {"scope"} == _keys(VectorScopeResult) - {"error"}
        assert {key for point in projected["points"] for key in point} | {"label"} == _keys(VectorPointResult)
