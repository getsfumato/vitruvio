"""One function that says what each block type contributes to every index.

Field policy is defined **exactly once**. Six indices need overlapping views of the same block -- the bitmap wants
facets, the B-tree wants ordered keys, the hash map wants identity keys, the inverted index wants weighted text,
the graph wants edges, the vector index wants a string to embed -- and deriving those separately in six places is
six places to disagree about what a block's subject is.

It also means a change to the policy bumps **one** identifier, :data:`PROJECTION_ID`, which appears in every index
header and inside the vector index's model tag. A consumer can then detect that vectors were produced from
different strings, which is as important as detecting a different model: the same model over different text lands
in a different place.

Note what is deliberately extended here. ``boltzmann.query.searchable_text`` returns only ``[media_type]`` for a
canonical block, which is right for the SDK's linear scan -- it holds no ``ContentReader`` and reading blobs would
turn a cheap pass expensive -- and wrong for an index, which *is* handed a reader for exactly this purpose. So
vitruvio's results are a strict **superset** of the SDK's, and the conformance tests assert that direction.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from boltzmann.blocks.canonical import CanonicalBlock
from boltzmann.blocks.episodic import EpisodicBlock
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.blocks.procedural import ProceduralBlock
from boltzmann.blocks.provenance import ProvenanceBlock
from boltzmann.blocks.semantic import SemanticBlock

if TYPE_CHECKING:
    from boltzmann.blocks.base import Block
    from boltzmann.indices.base import ContentReader

PROJECTION_ID = "vitruvio-projection/1"
"""Bumped whenever what gets extracted, or how it is weighted, changes."""


class Facet(StrEnum):
    """A categorical dimension the bitmap index intersects on."""

    MEMORY_TYPE = "memory_type"
    SUBJECT = "subject"
    TAG = "tag"
    SEMANTIC_KIND = "semantic_kind"
    MEDIA_TYPE = "media_type"
    RECORD_TYPE = "record_type"
    HAS_NORMALIZED_VIEW = "has_normalized_view"
    HAS_EVIDENCE = "has_evidence"
    PARTICIPANT = "participant"
    PREDICATE = "predicate"


class OrderedKey(StrEnum):
    """A key the B-tree index sorts on, for range and prefix predicates."""

    OCCURRED_AT = "occurred_at"
    ENDED_AT = "ended_at"
    SUBJECT = "subject"
    LABEL = "label"
    MEDIA_TYPE = "media_type"
    SIZE = "size"
    RECORDED_AT = "recorded_at"


class IdentityKey(StrEnum):
    """A key the hash-map index resolves exactly.

    Only *identity-shaped* keys belong here. ``subject`` and ``tags`` are facets -- many blocks share one -- and
    they go to the bitmap index. ``label`` earns a table because "get the concept called Fourier series" is an
    operation people actually perform.
    """

    BLOB = "blob"
    NORMALIZED_VIEW = "normalized_view"
    LABEL = "label"
    ALIAS = "alias"
    RECORD_SUBJECT = "record_subject"


class EdgeKind(StrEnum):
    """A typed, directed edge the graph index traverses."""

    RELATION = "relation"
    EVIDENCE = "evidence"
    USES = "uses"
    DERIVED_FROM = "derived_from"
    SUPERSEDES = "supersedes"


@dataclass(frozen=True, slots=True)
class Edge:
    """
    One directed edge out of a block.

    Attributes:
        kind (EdgeKind): What sort of relation.
        target (str): The block identity it points at. May be outside the module -- a citation pointing outside
            an install is information, not an error.
        predicate (str | None): The relation's own predicate, when it has one.
        weight (float): Confidence in the edge, used to decay a traversal score.
    """

    kind: EdgeKind
    target: str
    predicate: str | None = None
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class TextField:
    """
    One weighted piece of text.

    Attributes:
        name (str): Which field it came from, so a scoring explanation can name it.
        text (str): The content.
        weight (float): How much a match here counts. A label match means more than a body match.
    """

    name: str
    text: str
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class Projection:
    """
    Everything the six indices need from one block.

    Attributes:
        block_id (str): The block's identity.
        memory_type (MemoryType): Which module holds it.
        fields (tuple[TextField, ...]): Weighted text, for the inverted index.
        embed_text (str | None): The single string to embed, for the vector index.
        facets (dict[Facet, tuple[str, ...]]): Categorical values, for the bitmap index.
        keys (dict[OrderedKey, str | int]): Ordered values, for the B-tree index.
        identities (dict[IdentityKey, tuple[str, ...]]): Exact-lookup keys, for the hash map.
        edges (tuple[Edge, ...]): Outgoing edges, for the graph index.
        content_digests (tuple[str, ...]): Blobs this block names, which the vision path reads.
        size (int): Serialized size, for the module-level statistics.
    """

    block_id: str
    memory_type: MemoryType
    fields: tuple[TextField, ...] = ()
    embed_text: str | None = None
    facets: dict[Facet, tuple[str, ...]] = field(default_factory=dict)
    keys: dict[OrderedKey, str | int] = field(default_factory=dict)
    identities: dict[IdentityKey, tuple[str, ...]] = field(default_factory=dict)
    edges: tuple[Edge, ...] = ()
    content_digests: tuple[str, ...] = ()
    size: int = 0

    @property
    def text(self) -> str:
        """Every field joined, for the callers that want one string rather than weights."""
        return "\n".join(item.text for item in self.fields if item.text)


def fold(value: str) -> str:
    """
    Normalize a key for exact matching: NFKC, then case-folded.

    Deterministic rather than locale-aware, deliberately. Two clients must derive the same key from the same
    bytes, and locale collation would make the same brain answer differently depending on the machine. The cost
    is that visually distinct characters can merge (``µ`` and ``μ`` fold together) -- accepted, because the raw
    form stays in the block and only the *key* is folded.

    Args:
        value (str): The raw value.

    Returns:
        str: The folded key.
    """
    return unicodedata.normalize("NFKC", value).casefold().strip()


def _clean(values: object) -> tuple[str, ...]:
    """Coerce a scalar or a list into a tuple of non-empty folded strings."""
    if values is None:
        return ()
    if isinstance(values, str):
        stripped = values.strip()
        return (stripped,) if stripped else ()
    if isinstance(values, (list, tuple)):
        return tuple(item.strip() for item in values if isinstance(item, str) and item.strip())
    return ()


def _canonical(block: CanonicalBlock, content: ContentReader | None) -> Projection:
    """Project a canonical block, reading its normalized view when one exists."""
    facets: dict[Facet, tuple[str, ...]] = {
        Facet.MEMORY_TYPE: (MemoryType.CANONICAL.value,),
        Facet.MEDIA_TYPE: (fold(block.media_type),),
        Facet.HAS_NORMALIZED_VIEW: ("yes" if block.normalized_view else "no",),
    }
    identities: dict[IdentityKey, tuple[str, ...]] = {IdentityKey.BLOB: (str(block.blob),)}
    if block.normalized_view:
        identities[IdentityKey.NORMALIZED_VIEW] = (str(block.normalized_view.blob),)

    fields = [TextField("media_type", block.media_type, 0.5)]
    embed_text: str | None = None
    digests = [str(block.blob)]

    # The block itself holds no text -- the bytes are in the store. The normalized view is what makes a canonical
    # source searchable at all, and reading it is why an index receives a ContentReader.
    if block.normalized_view is not None and content is not None:
        digests.append(str(block.normalized_view.blob))
        try:
            raw = content.get_bytes(block.normalized_view.blob)
            text = raw.decode("utf-8", errors="replace").strip()
        except Exception:
            # A view that cannot be read must not fail a commit: the block is still valid evidence, and the
            # index degrades to structural matching only.
            text = ""
        if text:
            fields.append(TextField("normalized_view", text, 1.0))
            embed_text = text

    return Projection(
        block_id=str(block.block_id),
        memory_type=MemoryType.CANONICAL,
        fields=tuple(fields),
        embed_text=embed_text,
        facets=facets,
        keys={OrderedKey.MEDIA_TYPE: fold(block.media_type), OrderedKey.SIZE: block.size},
        identities=identities,
        content_digests=tuple(digests),
        size=block.size,
    )


def _semantic(block: SemanticBlock) -> Projection:
    """Project a semantic block: label and statement carry the meaning, relations carry the graph."""
    aliases = _clean(block.aliases)
    fields = [
        TextField("label", block.label, 3.0),
        TextField("statement", block.statement, 1.0),
        TextField("kind", block.kind.value, 0.5),
    ]
    fields += [TextField("alias", alias, 2.0) for alias in aliases]
    if block.subject:
        fields.append(TextField("subject", block.subject, 1.5))

    facets: dict[Facet, tuple[str, ...]] = {
        Facet.MEMORY_TYPE: (MemoryType.SEMANTIC.value,),
        Facet.SEMANTIC_KIND: (block.kind.value,),
        Facet.HAS_EVIDENCE: ("yes" if block.evidence else "no",),
    }
    if block.subject:
        facets[Facet.SUBJECT] = (fold(block.subject),)

    edges = [Edge(EdgeKind.EVIDENCE, str(cited)) for cited in (block.evidence or [])]
    predicates: list[str] = []
    for relation in block.relations or []:
        edges.append(Edge(EdgeKind.RELATION, str(relation.target), predicate=relation.predicate))
        predicates.append(fold(relation.predicate))
    if predicates:
        facets[Facet.PREDICATE] = tuple(sorted(set(predicates)))

    keys: dict[OrderedKey, str | int] = {OrderedKey.LABEL: fold(block.label)}
    if block.subject:
        keys[OrderedKey.SUBJECT] = fold(block.subject)

    embedded = (
        f"[{block.subject}] {block.label}. {block.statement}" if block.subject else f"{block.label}. {block.statement}"
    )
    if aliases:
        embedded += f" (aka: {', '.join(aliases)})"

    return Projection(
        block_id=str(block.block_id),
        memory_type=MemoryType.SEMANTIC,
        fields=tuple(fields),
        embed_text=embedded,
        facets=facets,
        keys=keys,
        identities={
            IdentityKey.LABEL: (fold(block.label),),
            IdentityKey.ALIAS: tuple(fold(alias) for alias in aliases),
        },
        edges=tuple(edges),
    )


def _episodic(block: EpisodicBlock) -> Projection:
    """Project an episode: the summary is the meaning, the timestamp is the key people filter on."""
    tags = _clean(block.tags)
    participants = _clean(block.participants)
    fields = [TextField("summary", block.summary, 3.0)]
    for name, value, weight in (
        ("outcome", block.outcome, 1.5),
        ("context", block.context, 1.0),
    ):
        if value:
            fields.append(TextField(name, value, weight))
    fields += [TextField("tag", tag, 1.5) for tag in tags]
    fields += [TextField("participant", who, 1.0) for who in participants]

    facets: dict[Facet, tuple[str, ...]] = {
        Facet.MEMORY_TYPE: (MemoryType.EPISODIC.value,),
        Facet.HAS_EVIDENCE: ("yes" if block.evidence else "no",),
    }
    if tags:
        facets[Facet.TAG] = tuple(sorted(fold(tag) for tag in tags))
    if participants:
        facets[Facet.PARTICIPANT] = tuple(sorted(fold(who) for who in participants))

    keys: dict[OrderedKey, str | int] = {OrderedKey.OCCURRED_AT: block.occurred_at}
    if block.ended_at:
        keys[OrderedKey.ENDED_AT] = block.ended_at

    # The date goes into the embedded string as well as into the ordered key, so that date-ish natural language
    # ("the May lecture") can hit without the caller having to construct a range predicate.
    parts = [f"{block.occurred_at[:10]} -- {block.summary}"]
    if block.context:
        parts.append(block.context)
    if block.outcome:
        parts.append(block.outcome)

    return Projection(
        block_id=str(block.block_id),
        memory_type=MemoryType.EPISODIC,
        fields=tuple(fields),
        embed_text=" ".join(parts),
        facets=facets,
        keys=keys,
        edges=tuple(Edge(EdgeKind.EVIDENCE, str(cited)) for cited in (block.evidence or [])),
    )


def _procedural(block: ProceduralBlock) -> Projection:
    """Project a procedure: the goal says what it is for, the steps say how."""
    fields = [TextField("label", block.label, 3.0), TextField("goal", block.goal, 2.5)]
    actions: list[str] = []
    edges: list[Edge] = [Edge(EdgeKind.EVIDENCE, str(cited)) for cited in (block.evidence or [])]

    for step in block.steps:
        fields.append(TextField("action", step.action, 1.0))
        actions.append(step.action)
        if step.condition:
            fields.append(TextField("condition", step.condition, 0.7))
        for alternative in step.alternatives or []:
            fields.append(TextField("alternative", alternative, 0.5))
        # `Step.uses` is typed as block identities, not free text, so every entry is an edge. Weighted below
        # 1.0 because "this step applies that formula" is a weaker signal for expansion than a citation is.
        edges.extend(Edge(EdgeKind.USES, str(used), weight=0.9) for used in step.uses or [])

    for name, values, weight in (
        ("precondition", block.preconditions, 0.7),
        ("success_criterion", block.success_criteria, 0.7),
    ):
        for value in _clean(values):
            fields.append(TextField(name, value, weight))

    facets: dict[Facet, tuple[str, ...]] = {
        Facet.MEMORY_TYPE: (MemoryType.PROCEDURAL.value,),
        Facet.HAS_EVIDENCE: ("yes" if block.evidence else "no",),
    }
    keys: dict[OrderedKey, str | int] = {OrderedKey.LABEL: fold(block.label)}
    if block.subject:
        facets[Facet.SUBJECT] = (fold(block.subject),)
        keys[OrderedKey.SUBJECT] = fold(block.subject)
        fields.append(TextField("subject", block.subject, 1.5))

    numbered = " ".join(f"{position}. {action}" for position, action in enumerate(actions, start=1))
    return Projection(
        block_id=str(block.block_id),
        memory_type=MemoryType.PROCEDURAL,
        fields=tuple(fields),
        embed_text=f"{block.label}. {block.goal} {numbered}".strip(),
        facets=facets,
        keys=keys,
        identities={IdentityKey.LABEL: (fold(block.label),)},
        edges=tuple(edges),
    )


def _provenance(block: ProvenanceBlock) -> Projection:
    """
    Project a provenance record.

    Structural only: no text fields and no embedded string. A registration record carries no prose worth
    embedding, and letting its wording compete in a similarity ranking is pure noise. What it *does* carry is the
    edges -- ``derived_from`` and ``supersedes`` exist nowhere else -- and the identity of what it talks about,
    because a provenance block is addressed by its subject rather than by itself.
    """
    record: Any = block.record
    record_type = getattr(record, "record_type", "unknown")
    facets: dict[Facet, tuple[str, ...]] = {
        Facet.MEMORY_TYPE: (MemoryType.PROVENANCE.value,),
        Facet.RECORD_TYPE: (str(record_type),),
    }

    subjects: list[str] = []
    edges: list[Edge] = []
    for attribute in ("block", "blocks"):
        value = getattr(record, attribute, None)
        if value is None:
            continue
        for identity in value if isinstance(value, list) else [value]:
            subjects.append(str(identity))

    for attribute, kind in (("derived_from", EdgeKind.DERIVED_FROM), ("supersedes", EdgeKind.SUPERSEDES)):
        value = getattr(record, attribute, None)
        if value is None:
            continue
        for identity in value if isinstance(value, list) else [value]:
            edges.append(Edge(kind, str(identity)))

    keys: dict[OrderedKey, str | int] = {}
    if recorded := getattr(record, "at", None):
        keys[OrderedKey.RECORDED_AT] = str(recorded)

    return Projection(
        block_id=str(block.block_id),
        memory_type=MemoryType.PROVENANCE,
        facets=facets,
        keys=keys,
        identities={IdentityKey.RECORD_SUBJECT: tuple(subjects)},
        edges=tuple(edges),
    )


def project(block: Block, content: ContentReader | None = None) -> Projection:
    """
    Extract everything the indices need from one block.

    Args:
        block (Block): The block, already decoded and verified by the store.
        content (ContentReader | None): For reading a canonical block's normalized view. Absent for the block
            types that carry their own text.

    Returns:
        Projection: What each index should take from it.
    """
    if isinstance(block, CanonicalBlock):
        return _canonical(block, content)
    if isinstance(block, SemanticBlock):
        return _semantic(block)
    if isinstance(block, EpisodicBlock):
        return _episodic(block)
    if isinstance(block, ProceduralBlock):
        return _procedural(block)
    if isinstance(block, ProvenanceBlock):
        return _provenance(block)

    # A block type this build does not know. Indexing its identity and memory type is still correct and still
    # useful; guessing at its fields would not be.
    return Projection(
        block_id=str(block.block_id),
        memory_type=block.MEMORY_TYPE,
        facets={Facet.MEMORY_TYPE: (block.MEMORY_TYPE.value,)},
    )
