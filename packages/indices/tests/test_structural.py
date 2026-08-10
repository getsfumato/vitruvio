"""The three structural indices, the envelope they persist in, and the projection they all read."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.indices.base import Index, IndexKind

from vitruvio.indices import (
    BitmapIndex,
    BTreeIndex,
    BuildDelta,
    Combine,
    FacetClause,
    FacetQuery,
    HashMapIndex,
    IdentityKey,
    IdQuery,
    IndexFormatError,
    IndexSet,
    MemoryContent,
    Order,
    OrderedKey,
    OrdinalTable,
    RangeQuery,
    project,
)
from vitruvio.indices import format as envelope
from vitruvio.indices.projection import Facet

if TYPE_CHECKING:
    from pathlib import Path

    from boltzmann.blocks.canonical import CanonicalBlock
    from boltzmann.blocks.episodic import EpisodicBlock
    from boltzmann.blocks.procedural import ProceduralBlock
    from boltzmann.blocks.provenance import ProvenanceBlock
    from boltzmann.blocks.semantic import SemanticBlock


class TestProtocolShape:
    @pytest.mark.parametrize("engine", [HashMapIndex, BitmapIndex, BTreeIndex])
    def test_each_index_satisfies_the_sdk_protocol(self, engine: type) -> None:
        assert isinstance(engine(MemoryType.SEMANTIC), Index)

    @pytest.mark.parametrize("engine", [HashMapIndex, BitmapIndex, BTreeIndex])
    def test_a_structural_index_is_rebuildable(self, engine: type) -> None:
        """Rebuildable means a consumer can regenerate it, so it never needs to travel."""
        assert engine(MemoryType.SEMANTIC).REBUILDABLE is True

    @pytest.mark.parametrize("engine", [HashMapIndex, BitmapIndex, BTreeIndex])
    def test_an_index_with_nothing_in_it_says_so(self, engine: type) -> None:
        """The whole reason `population` exists: an empty index does not otherwise announce itself."""
        index = engine(MemoryType.SEMANTIC)
        assert index.population == 0
        assert index.capability().state == "empty"
        assert index.capability().usable is False


class TestOrdinalTable:
    def test_ordinals_follow_sorted_identity_order(self) -> None:
        """Canonical ordering is what makes a serialized index depend on the block set and nothing else."""
        table = OrdinalTable(["sha256:cc", "sha256:aa", "sha256:bb"])
        assert table.identities == ("sha256:aa", "sha256:bb", "sha256:cc")
        assert table.ordinal("sha256:aa") == 0
        assert table.identity(2) == "sha256:cc"

    def test_the_same_set_in_any_order_yields_the_same_table(self) -> None:
        first = OrdinalTable(["sha256:aa", "sha256:bb"])
        second = OrdinalTable(["sha256:bb", "sha256:aa"])
        assert first.identities == second.identities

    def test_duplicates_collapse(self) -> None:
        assert len(OrdinalTable(["sha256:aa", "sha256:aa"])) == 1

    def test_an_unknown_identity_has_no_ordinal(self) -> None:
        assert OrdinalTable(["sha256:aa"]).ordinal("sha256:zz") is None
        assert OrdinalTable(["sha256:aa"]).identity(9) is None


class TestBuildDelta:
    def test_it_names_what_changed(self) -> None:
        delta = BuildDelta.between(["a", "b"], ["b", "c"])
        assert delta.added == ("c",)
        assert delta.removed == ("a",)
        assert delta.unchanged == 1

    def test_an_unchanged_set_is_a_noop(self) -> None:
        """The common case: `Brain.__init__` rebuilds on every open, and nothing has changed."""
        assert BuildDelta.between(["a"], ["a"]).is_noop is True

    def test_a_large_removal_prefers_a_clean_rebuild(self) -> None:
        delta = BuildDelta.between(["a", "b", "c"], ["a"])
        assert delta.rebuilds_everything is True


class TestProjection:
    def test_a_semantic_block_weights_its_label_above_its_statement(self, semantic_blocks: list[SemanticBlock]) -> None:
        projection = project(semantic_blocks[0])
        weights = {field.name: field.weight for field in projection.fields}
        assert weights["label"] > weights["statement"]

    def test_a_semantic_block_contributes_its_subject_as_a_facet_and_a_key(
        self, semantic_blocks: list[SemanticBlock]
    ) -> None:
        projection = project(semantic_blocks[0])
        assert projection.facets[Facet.SUBJECT] == ("senales",)
        assert projection.keys[OrderedKey.SUBJECT] == "senales"

    def test_relations_and_evidence_both_become_edges(self, semantic_blocks: list[SemanticBlock]) -> None:
        kinds = {edge.kind.value for edge in project(semantic_blocks[1]).edges}
        assert kinds == {"relation", "evidence"}

    def test_a_canonical_block_is_unsearchable_until_its_view_is_read(
        self, canonical: CanonicalBlock, content: MemoryContent
    ) -> None:
        """The SDK's own `searchable_text` returns only the media type here, which is why an index gets a reader."""
        without = project(canonical, None)
        assert without.embed_text is None

        with_reader = project(canonical, content)
        assert with_reader.embed_text is not None
        assert "Fourier" in with_reader.embed_text

    def test_an_unreadable_view_does_not_fail_the_projection(
        self, canonical: CanonicalBlock, empty_content: MemoryContent
    ) -> None:
        """A view that cannot be read must not fail a commit: the block is still valid evidence."""
        projection = project(canonical, empty_content)
        assert projection.embed_text is None
        assert projection.facets[Facet.MEDIA_TYPE] == ("application/pdf",)

    def test_a_procedure_step_that_uses_a_block_becomes_an_edge(self, procedural_block: ProceduralBlock) -> None:
        uses = [edge for edge in project(procedural_block).edges if edge.kind.value == "uses"]
        assert len(uses) == 1
        assert uses[0].weight < 1.0

    def test_a_provenance_block_is_indexed_by_what_it_talks_about(
        self, provenance_block: ProvenanceBlock, semantic_blocks: list[SemanticBlock]
    ) -> None:
        """A provenance block is never looked up by itself, so its subject is the only useful key."""
        projection = project(provenance_block)
        assert projection.identities[IdentityKey.RECORD_SUBJECT] == (str(semantic_blocks[0].block_id),)
        assert projection.fields == ()
        assert projection.embed_text is None

    def test_a_registration_record_projects_its_origin(self, provenance_block: ProvenanceBlock) -> None:
        """For most of this project's life `origin` was written by the runtime and read by nothing. Projecting it
        is what turns "have I already acquired this?" into a hash-map lookup instead of a scan of every record."""
        projection = project(provenance_block)
        assert projection.identities[IdentityKey.ORIGIN] == ("fourier.pdf",)

    def test_an_origin_is_folded_like_every_other_identity_key(self, semantic_blocks: list[SemanticBlock]) -> None:
        """Documented rather than incidental: two origins differing only in case collide, worst case a spurious
        skip. A source that cares about the distinction has to canonicalise before it hands one over."""
        from boltzmann.blocks.provenance import Actor, ActorKind, ProvenanceBlock, RegistrationRecord

        from vitruvio.indices import fold

        block = ProvenanceBlock(
            record=RegistrationRecord(
                block=semantic_blocks[0].block_id,
                actor=Actor(id="tester@example.com", kind=ActorKind.HUMAN),
                at="2026-05-14T14:00:00Z",
                origin="HTTPS://Example.COM/Paper.PDF",
            )
        )
        assert project(block).identities[IdentityKey.ORIGIN] == (fold("HTTPS://Example.COM/Paper.PDF"),)

    def test_a_record_without_an_origin_projects_no_origin_key(self, semantic_blocks: list[SemanticBlock]) -> None:
        """An empty tuple under the key would make the index answer a lookup for "" with every such block."""
        from boltzmann.blocks.provenance import Actor, ActorKind, ProvenanceBlock, RegistrationRecord

        block = ProvenanceBlock(
            record=RegistrationRecord(
                block=semantic_blocks[0].block_id,
                actor=Actor(id="tester@example.com", kind=ActorKind.HUMAN),
                at="2026-05-14T14:00:00Z",
            )
        )
        assert IdentityKey.ORIGIN not in project(block).identities

    def test_keys_are_folded_but_the_block_is_not(self, semantic_blocks: list[SemanticBlock]) -> None:
        from vitruvio.indices import fold

        assert fold("SEÑALES  ") == "señales"
        assert project(semantic_blocks[0]).keys[OrderedKey.LABEL] == "serie de fourier"


class TestHashMapIndex:
    @pytest.fixture
    def index(self, semantic_blocks: list[SemanticBlock], content: MemoryContent) -> HashMapIndex:
        built = HashMapIndex(MemoryType.SEMANTIC)
        built.build(semantic_blocks, content)
        return built

    def test_a_label_resolves_to_its_block(self, index: HashMapIndex, semantic_blocks: list[SemanticBlock]) -> None:
        results = index.lookup(IdQuery(keys=((IdentityKey.LABEL, "Serie de Fourier"),)))
        assert results.identities() == (str(semantic_blocks[0].block_id),)

    def test_an_alias_resolves_too(self, index: HashMapIndex, semantic_blocks: list[SemanticBlock]) -> None:
        results = index.lookup(IdQuery(keys=((IdentityKey.ALIAS, "Fourier series"),)))
        assert results.identities() == (str(semantic_blocks[0].block_id),)

    def test_an_origin_resolves_to_the_record_that_acquired_it(
        self, provenance_block: ProvenanceBlock, content: MemoryContent
    ) -> None:
        """The lookup `source pull` performs instead of persisting a cursor: one hash-map probe against an index
        that is already built, on derived state that a rebuild regenerates."""
        built = HashMapIndex(MemoryType.PROVENANCE)
        built.build([provenance_block], content)
        results = built.lookup(IdQuery(keys=((IdentityKey.ORIGIN, "fourier.pdf"),)))
        assert results.identities() == (str(provenance_block.block_id),)
        assert not built.lookup(IdQuery(keys=((IdentityKey.ORIGIN, "never-fetched.pdf"),))).hits

    def test_lookup_is_case_and_form_insensitive(self, index: HashMapIndex) -> None:
        assert index.lookup(IdQuery(keys=((IdentityKey.LABEL, "SERIE DE FOURIER"),))).hits

    def test_an_exact_match_scores_one_without_gradation(self, index: HashMapIndex) -> None:
        """An identity match is not a relevance judgement; a graded score would invite comparing it to one."""
        results = index.lookup(IdQuery(keys=((IdentityKey.LABEL, "Serie de Fourier"),)))
        assert results.hits[0].score == 1.0

    def test_a_bare_string_is_read_as_an_identity_or_a_label(
        self, index: HashMapIndex, semantic_blocks: list[SemanticBlock]
    ) -> None:
        by_label = index.search("Serie de Fourier")
        by_identity = index.search(str(semantic_blocks[0].block_id))
        assert by_label
        assert by_identity
        assert str(by_label[0][0]) == str(by_identity[0][0])

    def test_an_exact_lookup_is_always_exhausted(self, index: HashMapIndex) -> None:
        """It either finds the key or it does not: there is never more to find."""
        assert index.lookup(IdQuery(identities=("sha256:" + "0" * 64,))).exhausted is True

    def test_it_owns_the_module_level_statistics(self, index: HashMapIndex) -> None:
        fragment = index.fragment()
        assert fragment.module_level is True
        assert fragment.cardinality == 4

    def test_a_shared_label_is_reported_as_a_duplicate(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        """Not an error -- both are returned -- but the planner must know a label probe is not unique."""
        from boltzmann.blocks.semantic import SemanticBlock, SemanticKind

        clash = SemanticBlock(
            kind=SemanticKind.FACT,
            label="Serie de Fourier",
            statement="Un enunciado distinto con el mismo nombre.",
            evidence=semantic_blocks[0].evidence,
        )
        index = HashMapIndex(MemoryType.SEMANTIC)
        index.build([semantic_blocks[0], clash], content)
        assert index.duplicates(IdentityKey.LABEL) == {"serie de fourier": 2}


class TestBitmapIndex:
    @pytest.fixture
    def index(self, semantic_blocks: list[SemanticBlock], content: MemoryContent) -> BitmapIndex:
        built = BitmapIndex(MemoryType.SEMANTIC)
        built.build(semantic_blocks, content)
        return built

    def test_a_facet_filter_selects_by_value(self, index: BitmapIndex) -> None:
        query = FacetQuery(clauses=(FacetClause(Facet.SUBJECT, ("senales",)),))
        assert len(index.filter(query) or ()) == 3

    def test_clauses_intersect(self, index: BitmapIndex) -> None:
        query = FacetQuery(
            clauses=(
                FacetClause(Facet.SUBJECT, ("senales",)),
                FacetClause(Facet.SEMANTIC_KIND, ("formula",)),
            )
        )
        assert len(index.filter(query) or ()) == 1

    def test_values_within_a_clause_union(self, index: BitmapIndex) -> None:
        query = FacetQuery(clauses=(FacetClause(Facet.SEMANTIC_KIND, ("formula", "fact")),))
        assert len(index.filter(query) or ()) == 2

    def test_all_requires_every_value(self, index: BitmapIndex) -> None:
        query = FacetQuery(clauses=(FacetClause(Facet.SEMANTIC_KIND, ("formula", "fact"), combine=Combine.ALL),))
        assert len(index.filter(query) or ()) == 0

    def test_negation_excludes(self, index: BitmapIndex) -> None:
        query = FacetQuery(clauses=(FacetClause(Facet.SUBJECT, ("control",), negate=True),))
        assert len(index.filter(query) or ()) == 3

    def test_the_estimate_is_exact_rather_than_interpolated(self, index: BitmapIndex) -> None:
        """This is the property that makes the bitmap the planner's most trustworthy input."""
        estimate = index.estimate(FacetQuery(clauses=(FacetClause(Facet.SUBJECT, ("senales",)),)))
        assert estimate.exact is True
        assert estimate.rows == 3
        assert estimate.confidence == 1.0

    def test_a_facet_no_block_in_this_module_carries_matches_nothing(self, index: BitmapIndex) -> None:
        """Empty is the honest answer here: no semantic block has tags, so `tag=clase` genuinely matches none."""
        query = FacetQuery(clauses=(FacetClause(Facet.TAG, ("clase",)),))
        assert index.filter(query) == frozenset()

    def test_a_facet_that_blew_the_distinct_cap_returns_none_rather_than_an_empty_set(
        self, content: MemoryContent
    ) -> None:
        """`None` means "post-filter this"; empty means "nothing matches". Conflating them silently excludes
        everything, which is the one failure mode a filter must never have."""
        from boltzmann.blocks.semantic import SemanticBlock, SemanticKind

        from vitruvio.indices.bitmap import MAX_DISTINCT_PER_FACET

        blocks = [
            SemanticBlock(
                kind=SemanticKind.FACT,
                label=f"hecho {position}",
                subject=f"tema-{position}",
                statement=f"enunciado {position}",
            )
            for position in range(MAX_DISTINCT_PER_FACET + 2)
        ]
        index = BitmapIndex(MemoryType.SEMANTIC)
        index.build(blocks, content)

        query = FacetQuery(clauses=(FacetClause(Facet.SUBJECT, ("tema-1",)),))
        assert index.filter(query) is None
        assert index.estimate(query).exact is False
        assert "subject" not in index.capability().facets

    def test_a_value_that_does_not_occur_is_exactly_zero(self, index: BitmapIndex) -> None:
        """And knowing that lets the planner prune a whole subplan before running a generator."""
        stats = index.fragment().columns["subject"]
        assert stats.selectivity("optica", 4).rows == 0
        assert stats.selectivity("optica", 4).exact is True

    def test_facet_counts_are_reported_for_every_value(self, index: BitmapIndex) -> None:
        assert index.values(Facet.SUBJECT) == {"control": 1, "senales": 3}

    def test_blocks_without_a_facet_are_counted_as_null(
        self, episodic_blocks: list[EpisodicBlock], content: MemoryContent
    ) -> None:
        """Which is what keeps a subject filter's selectivity honest rather than optimistic."""
        index = BitmapIndex(MemoryType.EPISODIC)
        index.build(episodic_blocks, content)
        assert index.fragment().columns["tag"].null_count == 0
        assert "subject" not in index.fragment().columns


class TestBTreeIndex:
    @pytest.fixture
    def index(self, episodic_blocks: list[EpisodicBlock], content: MemoryContent) -> BTreeIndex:
        built = BTreeIndex(MemoryType.EPISODIC)
        built.build(episodic_blocks, content)
        return built

    def test_a_time_window_selects_by_range(self, index: BTreeIndex) -> None:
        query = RangeQuery(key=OrderedKey.OCCURRED_AT, low="2026-05-01T00:00:00Z", high="2026-05-31T23:59:59Z")
        assert len(index.scan(query)) == 2

    def test_an_open_ended_window_works(self, index: BTreeIndex) -> None:
        assert len(index.scan(RangeQuery(key=OrderedKey.OCCURRED_AT, low="2026-06-01T00:00:00Z"))) == 1

    def test_descending_order_reverses(self, index: BTreeIndex) -> None:
        ascending = index.scan(RangeQuery(key=OrderedKey.OCCURRED_AT))
        descending = index.scan(RangeQuery(key=OrderedKey.OCCURRED_AT, order=Order.DESCENDING))
        assert descending == list(reversed(ascending))

    def test_lexicographic_order_is_chronological_order(self, index: BTreeIndex) -> None:
        """Fixed-width RFC3339 in UTC, so a range scan needs no parsing on the hot path."""
        extremes = index.extremes(OrderedKey.OCCURRED_AT)
        assert extremes == ("2026-05-14T14:00:00Z", "2026-07-02T09:00:00Z")

    def test_a_key_no_block_carries_matches_nothing(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        """A time range over semantic memory is empty, because a concept has no timestamp -- the SDK's own rule."""
        index = BTreeIndex(MemoryType.SEMANTIC)
        index.build(semantic_blocks, content)
        assert index.scan(RangeQuery(key=OrderedKey.OCCURRED_AT, low="2020-01-01T00:00:00Z")) == []

    def test_a_prefix_scan_narrows_a_label(self, semantic_blocks: list[SemanticBlock], content: MemoryContent) -> None:
        index = BTreeIndex(MemoryType.SEMANTIC)
        index.build(semantic_blocks, content)
        assert len(index.scan(RangeQuery(key=OrderedKey.LABEL, prefix="serie"))) == 1

    def test_an_integer_key_sorts_numerically(self, canonical: CanonicalBlock, content: MemoryContent) -> None:
        """Zero-padded, because "10" sorts before "9" as a string and a size range would return nonsense."""
        index = BTreeIndex(MemoryType.CANONICAL)
        index.build([canonical], content)
        assert index.scan(RangeQuery(key=OrderedKey.SIZE, low=0, high=100)) == [0]
        assert index.scan(RangeQuery(key=OrderedKey.SIZE, low=100)) == []

    def test_the_histogram_records_how_many_blocks_are_timed(self, index: BTreeIndex) -> None:
        histogram = index.fragment().time["occurred_at"]
        assert histogram.timed_count == 3
        assert histogram.minimum < histogram.maximum

    def test_a_range_estimate_scales_by_the_timed_fraction(self, index: BTreeIndex) -> None:
        histogram = index.fragment().time["occurred_at"]
        estimate = histogram.range_selectivity("2026-05-01T00:00:00Z", "2026-05-31T23:59:59Z", 3)
        assert 1 <= estimate.rows <= 3

    def test_a_window_outside_the_data_is_exactly_zero(self, index: BTreeIndex) -> None:
        histogram = index.fragment().time["occurred_at"]
        assert histogram.range_selectivity("2030-01-01T00:00:00Z", None, 3).rows == 0

    def test_an_allow_mask_is_applied(self, index: BTreeIndex) -> None:
        query = RangeQuery(key=OrderedKey.OCCURRED_AT, allow=frozenset({0}))
        assert index.scan(query) == [0]


class TestPersistence:
    def test_an_index_round_trips_through_a_file(
        self, tmp_path: Path, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        first = BitmapIndex(MemoryType.SEMANTIC, tmp_path)
        first.build(semantic_blocks, content)
        first.bind("sha256:" + "ab" * 32)

        second = BitmapIndex(MemoryType.SEMANTIC, tmp_path)
        assert second.population == first.population
        assert second.bound_root == first.bound_root
        query = FacetQuery(clauses=(FacetClause(Facet.SUBJECT, ("senales",)),))
        assert second.filter(query) == first.filter(query)

    def test_the_bytes_depend_only_on_the_block_set(
        self, tmp_path: Path, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        """Byte reproducibility is what makes a golden fixture stable and a published layer digest meaningful."""
        first = HashMapIndex(MemoryType.SEMANTIC, tmp_path / "a")
        first.build(semantic_blocks, content)
        second = HashMapIndex(MemoryType.SEMANTIC, tmp_path / "b")
        second.build(list(reversed(semantic_blocks)), content)

        assert (tmp_path / "a" / "semantic.hash_map.vidx").read_bytes() == (
            tmp_path / "b" / "semantic.hash_map.vidx"
        ).read_bytes()

    def test_an_empty_index_is_not_written(self, tmp_path: Path, content: MemoryContent) -> None:
        """A file claiming to hold an index and holding nothing is the failure this design guards against."""
        index = HashMapIndex(MemoryType.SEMANTIC, tmp_path)
        index.build([], content)
        assert index.flush() is None
        assert not list(tmp_path.glob("*.vidx"))

    def test_an_index_that_becomes_empty_has_its_file_removed(
        self, tmp_path: Path, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        index = HashMapIndex(MemoryType.SEMANTIC, tmp_path)
        index.build(semantic_blocks, content)
        assert (tmp_path / "semantic.hash_map.vidx").is_file()

        index.build([], content)
        assert not (tmp_path / "semantic.hash_map.vidx").exists()

    def test_a_corrupt_file_is_refused_rather_than_read_as_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "semantic.hash_map.vidx"
        path.write_bytes(b"this is not an index")
        with pytest.raises(IndexFormatError, match="magic"):
            envelope.read(path)

    def test_a_truncated_body_is_detected_by_its_digest(self, tmp_path: Path) -> None:
        header = envelope.Header(kind="hash_map", memory_type="semantic", population=1)
        data = envelope.encode(header, {"identities": ["sha256:aa"]})
        path = tmp_path / "damaged.vidx"
        path.write_bytes(data[:-4])
        with pytest.raises(IndexFormatError):
            envelope.read(path)

    def test_a_corrupt_sidecar_leaves_the_index_empty_rather_than_wrong(self, tmp_path: Path) -> None:
        """Empty is detectable through `population`; a half-read index is not."""
        (tmp_path / "semantic.hash_map.vidx").write_bytes(b"garbage")
        index = HashMapIndex(MemoryType.SEMANTIC, tmp_path)
        assert index.population == 0

    def test_a_stale_binding_is_reported(
        self, tmp_path: Path, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        index = BitmapIndex(MemoryType.SEMANTIC, tmp_path)
        index.build(semantic_blocks, content)
        index.bind("sha256:" + "ab" * 32)

        capability = index.capability(root="sha256:" + "cd" * 32)
        assert capability.state == "stale"
        assert capability.usable is False

    def test_the_fingerprint_changes_when_a_block_is_dropped(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        """The case a Merkle root cannot catch: a redaction leaves the composition and changes what is readable."""
        index = HashMapIndex(MemoryType.SEMANTIC)
        index.build(semantic_blocks, content)
        before = index.fingerprint
        index.build(semantic_blocks[:-1], content)
        assert index.fingerprint != before


class TestIndexSet:
    def test_the_hash_map_is_registered_first(self) -> None:
        """It owns the module-level statistics because it necessarily visits every block."""
        from vitruvio.kernel import IndexSpec

        specs = [
            IndexSpec(memory_type=MemoryType.SEMANTIC, kind=IndexKind.BTREE),
            IndexSpec(memory_type=MemoryType.SEMANTIC, kind=IndexKind.HASH_MAP),
        ]
        indices = IndexSet.from_specs(specs)
        assert indices.for_module(MemoryType.SEMANTIC)[0].KIND is IndexKind.HASH_MAP

    def test_an_engine_this_build_lacks_is_reported_rather_than_dropped(self) -> None:
        """An index the user asked for and did not get is exactly what must not pass unnoticed."""
        from vitruvio.kernel import IndexSpec

        indices = IndexSet.from_specs([IndexSpec(memory_type=MemoryType.SEMANTIC, kind=IndexKind.VECTOR)])
        assert "semantic.vector" in indices.unavailable

    def test_statistics_merge_across_indices(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        indices = IndexSet()
        for engine in (HashMapIndex, BitmapIndex, BTreeIndex):
            index = engine(MemoryType.SEMANTIC)
            index.build(semantic_blocks, content)
            indices.add(index)

        stats = indices.statistics()[MemoryType.SEMANTIC]
        assert stats.cardinality == 4
        assert stats.columns["subject"].distinct == 2
        assert set(stats.version.index_kinds) == {"hash_map", "bitmap", "btree"}

    def test_merging_is_order_independent(self, semantic_blocks: list[SemanticBlock], content: MemoryContent) -> None:
        """Ordering is an optimisation; a statistics layer that broke on a different order would be a bug."""
        from vitruvio.stats import merge

        built = []
        for engine in (BitmapIndex, BTreeIndex, HashMapIndex):
            index = engine(MemoryType.SEMANTIC)
            index.build(semantic_blocks, content)
            built.append(index.fragment())

        forwards = merge("semantic", built)
        backwards = merge("semantic", list(reversed(built)))
        assert forwards.cardinality == backwards.cardinality == 4
        assert forwards.columns.keys() == backwards.columns.keys()

    def test_statistics_without_a_module_are_not_stamped(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        indices = IndexSet()
        index = HashMapIndex(MemoryType.SEMANTIC)
        index.build(semantic_blocks, content)
        indices.add(index)
        assert indices.statistics()[MemoryType.SEMANTIC].version.root is None
