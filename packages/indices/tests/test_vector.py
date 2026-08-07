"""The vector index and the embedding layer: tags, travel, chunking, and the refusals that matter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.exceptions import DistributionError
from boltzmann.indices.base import Index, TravellingIndex

from vitruvio.embeddings import (
    EmbedderUnavailableError,
    EmbeddingCache,
    FakeEmbedder,
    HashingEmbedder,
    ImageInput,
    MemoryCache,
    Modality,
    ModelTag,
    cache_key,
    explain_mismatch,
    resolve,
)
from vitruvio.indices import (
    CHUNKER_ID,
    IndexModelMismatchError,
    MemoryContent,
    VectorIndex,
    VectorQuery,
    chunk,
)

if TYPE_CHECKING:
    from pathlib import Path

    from boltzmann.blocks.semantic import SemanticBlock


def an_index(memory_type: MemoryType = MemoryType.SEMANTIC, home: Path | None = None, **kwargs: object) -> VectorIndex:
    """A vector index over deterministic vectors."""
    return VectorIndex(memory_type, home, embedder=FakeEmbedder(dimensions=32), **kwargs)  # type: ignore[arg-type]


class TestModelTag:
    def test_a_tag_round_trips(self) -> None:
        tag = ModelTag(
            provider="local-st",
            model="intfloat/multilingual-e5-base",
            revision="a1b2c3",
            dimensions=768,
            dtype="f16",
            projection="proj1",
            chunker="chunk1",
        )
        assert ModelTag.parse(tag.render()) == tag

    def test_a_slash_in_the_model_survives(self) -> None:
        """Model names carry slashes and the format uses them as a separator."""
        tag = ModelTag(provider="p", model="org/model", dimensions=8)
        parsed = ModelTag.parse(tag.render())
        assert parsed is not None
        assert parsed.model == "org/model"

    def test_the_chunker_is_part_of_the_tag(self) -> None:
        """A different chunker embeds different strings, so it changes where a vector lands."""
        first = ModelTag(provider="p", model="m", dimensions=8, chunker="a")
        second = ModelTag(provider="p", model="m", dimensions=8, chunker="b")
        assert first.render() != second.render()

    def test_the_projection_is_part_of_the_tag(self) -> None:
        """Same reason: the same model over different text lands somewhere else."""
        first = ModelTag(provider="p", model="m", dimensions=8, projection="a")
        second = ModelTag(provider="p", model="m", dimensions=8, projection="b")
        assert first.render() != second.render()

    def test_a_mismatch_names_the_field(self) -> None:
        """The difference between a usable error and "the tags do not match"."""
        held = ModelTag(provider="p", model="m", revision="old", dimensions=8).render()
        wanted = ModelTag(provider="p", model="m", revision="new", dimensions=8).render()
        assert "revision" in explain_mismatch(held, wanted)

    def test_an_unparseable_tag_is_reported_verbatim(self) -> None:
        """Another implementation's tag, or an older vitruvio's. Still has to produce a message."""
        assert "whatever" in explain_mismatch("whatever", ModelTag(provider="p", model="m").render())

    def test_hashed_features_are_not_semantic_and_say_so(self) -> None:
        """A result ranked by hashed features must not be taken for a semantic one."""
        assert HashingEmbedder().tag.is_semantic is False
        assert FakeEmbedder().tag.is_semantic is False
        assert ModelTag(provider="local-st", model="m").is_semantic is True


class TestEmbedders:
    def test_hashing_needs_no_extras_and_produces_unit_vectors(self) -> None:
        """The default, so a bare install can build, publish and query a vector index."""
        embedder = HashingEmbedder(dimensions=64)
        (vector,) = embedder.embed_text(["una funcion periodica"])
        assert len(vector) == 64
        assert sum(value * value for value in vector) == pytest.approx(1.0)

    def test_hashing_is_deterministic(self) -> None:
        first = HashingEmbedder(dimensions=32).embed_text(["fourier"])
        second = HashingEmbedder(dimensions=32).embed_text(["fourier"])
        assert first == second

    def test_hashing_refuses_images_rather_than_inventing_a_vector(self) -> None:
        """A meaningless vector that ranks is worse than an error."""
        with pytest.raises(EmbedderUnavailableError, match="vision"):
            HashingEmbedder().embed_images([ImageInput(data=b"x", media_type="image/png")])

    def test_folding_is_the_hashing_revision(self) -> None:
        """It decides what a token is, so a change to it moves every vector.

        Folding rather than the whole analyzer id: stemming and the term spaces are above this embedder and change
        nothing about its vectors, so putting them in its revision would invalidate every cached vector for a change
        that did not move any of them.
        """
        from vitruvio.embeddings.folding import FOLDING_VERSION

        assert HashingEmbedder().tag.revision == f"fold{FOLDING_VERSION}"

    def test_the_embedder_claims_no_projection_and_the_index_supplies_one(self) -> None:
        """An embedder does not know what text it will be handed, so the projection is not part of its identity -- but
        it is very much part of the index's, because a different projection embeds different strings."""
        from vitruvio.indices.projection import PROJECTION_ID

        assert HashingEmbedder().tag.projection == "none"
        model_tag = an_index().model_tag
        assert model_tag is not None
        assert PROJECTION_ID.replace("/", "-") in model_tag

    def test_fake_vectors_are_bit_identical_across_instances(self) -> None:
        """Which is what lets a test assert on results rather than on tolerances."""
        assert FakeEmbedder().embed_text(["x"]) == FakeEmbedder().embed_text(["x"])

    def test_fake_can_script_a_neighbourhood(self) -> None:
        """So "these are synonyms" is a fact in a test rather than a hope about a real model."""
        embedder = FakeEmbedder(neighbourhoods={"serie de fourier": "g", "fourier series": "g"})
        first, second = embedder.embed_text(["serie de fourier", "fourier series"])
        assert first == second

    def test_fake_handles_both_modalities(self) -> None:
        assert FakeEmbedder().modalities == {Modality.TEXT, Modality.IMAGE}

    def test_an_unknown_provider_is_refused_rather_than_substituted(self) -> None:
        """A substitute would produce vectors whose tag lies about where they came from."""
        from vitruvio.kernel import EmbedderSpec

        with pytest.raises(EmbedderUnavailableError, match="no embedder provider"):
            resolve(EmbedderSpec(provider="telepathy", model="m"))

    def test_a_missing_extra_names_what_installs_it(self) -> None:
        from vitruvio.kernel import EmbedderSpec

        with pytest.raises(EmbedderUnavailableError, match=r"vitruvio\[local\]"):
            resolve(EmbedderSpec(provider="local-st", model="m"))


class TestCache:
    def test_the_key_is_over_the_embedded_string(self) -> None:
        """Which is what makes an edit to an unprojected field free."""
        first = cache_key("tag", "text", "passage", "hello")
        same = cache_key("tag", "text", "passage", "hello")
        different = cache_key("tag", "text", "passage", "goodbye")
        assert first == same != different

    def test_a_different_model_invalidates(self) -> None:
        assert cache_key("a", "text", "passage", "x") != cache_key("b", "text", "passage", "x")

    def test_a_different_role_invalidates(self) -> None:
        """Some models prefix a query differently from a passage."""
        assert cache_key("a", "text", "query", "x") != cache_key("a", "text", "passage", "x")

    def test_vectors_round_trip_through_sqlite(self, tmp_path: Path) -> None:
        cache = EmbeddingCache(tmp_path / "c.sqlite", "tag")
        key = cache_key("tag", "text", "passage", "x")
        cache.put_many({key: (0.5, -0.5)}, "text")
        assert cache.get_many([key])[key] == pytest.approx((0.5, -0.5))
        cache.close()

    def test_a_reopened_cache_still_holds_its_vectors(self, tmp_path: Path) -> None:
        """The whole reason it is on disk: `build` runs on every open, and re-embedding would be minutes."""
        key = cache_key("tag", "text", "passage", "x")
        first = EmbeddingCache(tmp_path / "c.sqlite", "tag")
        first.put_many({key: (1.0, 0.0)}, "text")
        first.close()

        second = EmbeddingCache(tmp_path / "c.sqlite", "tag")
        assert second.count() == 1
        second.close()

    def test_one_file_per_model_tag(self, tmp_path: Path) -> None:
        """So switching models does not invalidate the old vectors, and switching back is free."""
        first = EmbeddingCache.for_model(tmp_path, "model-a")
        second = EmbeddingCache.for_model(tmp_path, "model-b")
        assert first.path != second.path
        first.close()
        second.close()

    def test_vacuum_drops_what_is_no_longer_used(self) -> None:
        cache = MemoryCache()
        keep, drop = cache_key("t", "text", "passage", "a"), cache_key("t", "text", "passage", "b")
        cache.put_many({keep: (1.0,), drop: (0.0,)}, "text")
        assert cache.vacuum([keep]) == 1
        assert cache.count() == 1


class TestChunking:
    def test_short_text_is_one_chunk(self) -> None:
        assert len(chunk("a short statement")) == 1

    def test_long_text_is_split(self) -> None:
        pieces = chunk("palabra " * 800)
        assert len(pieces) > 1

    def test_chunks_carry_their_span(self) -> None:
        """Which is the payoff: a citation points at a passage rather than at a whole document."""
        pieces = chunk("frase. " * 500)
        for _, text, span in pieces:
            assert span[1] > span[0]
            assert len(text) <= 1600 + 200

    def test_chunking_is_a_pure_function_of_the_text(self) -> None:
        """Character-based, so boundaries cannot depend on which tokenizer happens to be installed."""
        text = "oracion larga. " * 400
        assert chunk(text) == chunk(text)

    def test_empty_text_is_no_chunks(self) -> None:
        assert chunk("   ") == []


class TestVectorIndex:
    def test_it_satisfies_the_travelling_protocol(self) -> None:
        """Mandatory: a non-rebuildable index that is not travelling makes `pack()` raise."""
        index = an_index()
        assert isinstance(index, Index)
        assert isinstance(index, TravellingIndex)

    def test_it_is_not_rebuildable(self) -> None:
        """Which is why it travels: a consumer cannot regenerate it without a model."""
        assert an_index().REBUILDABLE is False

    def test_it_indexes_and_ranks(self, semantic_blocks: list[SemanticBlock], content: MemoryContent) -> None:
        """That the *right* block comes back is asserted by the scripted-neighbourhood test below.

        It cannot be asserted here: a deterministic embedder is deliberately not semantic, so a bare statement has no
        relationship to the vector of the full projected string it appears in. Testing it here would be testing that
        sha256 happens to be kind.
        """
        index = an_index()
        index.build(semantic_blocks, content)
        assert index.population == 4

        found = index.lookup(VectorQuery(text="cualquier consulta"), limit=2)
        assert len(found) == 2
        assert all(0.0 <= score <= 1.0 for _, score, _, _ in found)
        assert found[0][1] >= found[1][1], "results must be ranked"

    def test_a_scripted_neighbourhood_is_found(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        """A hard assertion about semantics, made possible by a deterministic embedder."""
        target = semantic_blocks[0]
        embedded = f"[{target.subject}] {target.label}. {target.statement} (aka: {', '.join(target.aliases or [])})"
        embedder = FakeEmbedder(dimensions=32, neighbourhoods={embedded: "g", "una consulta sin palabras comunes": "g"})
        index = VectorIndex(MemoryType.SEMANTIC, embedder=embedder)
        index.build(semantic_blocks, content)

        found = index.lookup(VectorQuery(text="una consulta sin palabras comunes"), limit=1)
        assert found[0][0] == str(target.block_id)

    def test_population_counts_blocks_not_vectors(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        """A chunked document is several vectors and one block, and the planner reasons about blocks."""
        index = an_index()
        index.build(semantic_blocks, content)
        assert index.population == len(semantic_blocks)

    def test_the_tag_folds_in_the_chunker(self) -> None:
        assert CHUNKER_ID.replace("/", "-") in (an_index().model_tag or "")

    def test_a_mask_is_honoured(self, semantic_blocks: list[SemanticBlock], content: MemoryContent) -> None:
        index = an_index()
        index.build(semantic_blocks, content)
        everything = index.lookup(VectorQuery(text="fourier"), limit=10)
        masked = index.lookup(VectorQuery(text="fourier", allow=frozenset({0})), limit=10)
        assert len(masked) < len(everything)

    def test_exact_and_approximate_agree_on_a_small_space(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        """Below a few dozen vectors HNSW visits everything, so a disagreement would mean a bug."""
        index = an_index()
        index.build(semantic_blocks, content)
        exact = index.lookup(VectorQuery(text="fourier", exact=True), limit=4)
        approximate = index.lookup(VectorQuery(text="fourier"), limit=4)
        assert {block for block, _, _, _ in exact} == {block for block, _, _, _ in approximate}

    def test_a_locator_points_at_a_chunk(self) -> None:
        assert an_index().locator_for("sha256:aa", 3, (1600, 3200)) == "chunk:3#1600-3200"

    def test_the_statistics_carry_a_measured_recall_curve(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        """Without it, recall in the cost objective would be a made-up number."""
        index = an_index()
        index.build(semantic_blocks, content)
        stats = index.fragment().vectors["text"]
        assert stats.recall_curve
        assert 0.0 < stats.recall_at(64) <= 1.0


class TestTravel:
    def test_dump_and_load_round_trip(self, semantic_blocks: list[SemanticBlock], content: MemoryContent) -> None:
        first = an_index()
        first.build(semantic_blocks, content)
        first.bind("sha256:" + "ab" * 32)

        second = an_index()
        second.load(first.dump())
        assert second.population == first.population
        assert second.bound_root == first.bound_root

        query = VectorQuery(text=semantic_blocks[0].statement)
        assert [entry[0] for entry in second.lookup(query, 3)] == [entry[0] for entry in first.lookup(query, 3)]

    def test_dump_is_exactly_the_bytes_on_disk(
        self, tmp_path: Path, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        """So it is impossible to publish something this process does not hold."""
        index = an_index(home=tmp_path)
        index.build(semantic_blocks, content)
        path = tmp_path / "semantic.vector.vidx"
        assert path.read_bytes() == index.dump()

    def test_an_empty_index_refuses_to_be_published(self, content: MemoryContent) -> None:
        """A layer claiming a vector index and carrying none is worse than an absent layer: absence is detectable."""
        index = an_index()
        index.build([], content)
        with pytest.raises(DistributionError, match="worse than an absent one"):
            index.dump()

    def test_a_mismatched_model_tag_is_refused(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        """Not degraded. The two spaces are unrelated, so the cosines between them are noise."""
        built = VectorIndex(MemoryType.SEMANTIC, embedder=FakeEmbedder(dimensions=32))
        built.build(semantic_blocks, content)
        travelled = built.dump()

        other = VectorIndex(MemoryType.SEMANTIC, embedder=FakeEmbedder(dimensions=64))
        with pytest.raises(IndexModelMismatchError):
            other.load(travelled)

    def test_the_refusal_is_a_distribution_error(self) -> None:
        """Which is what the SDK catches, so a brain with a mismatched index still *opens*, degraded."""
        assert issubclass(IndexModelMismatchError, DistributionError)

    def test_a_reloaded_sidecar_answers_identically(
        self, tmp_path: Path, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        first = an_index(home=tmp_path)
        first.build(semantic_blocks, content)

        second = an_index(home=tmp_path)
        query = VectorQuery(text="fourier")
        assert [entry[0] for entry in second.lookup(query, 4)] == [entry[0] for entry in first.lookup(query, 4)]

    def test_a_cached_vector_is_not_re_embedded(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        """The reason the cache is not optional: `build` runs on every commit and every open."""

        class Counting(FakeEmbedder):
            calls = 0

            def embed_text(self, texts, *, role=None):
                Counting.calls += len(texts)
                return super().embed_text(texts)

        cache = MemoryCache()
        embedder = Counting(dimensions=32)
        first = VectorIndex(MemoryType.SEMANTIC, embedder=embedder, cache=cache)
        first.build(semantic_blocks, content)
        after_first = Counting.calls

        second = VectorIndex(MemoryType.SEMANTIC, embedder=embedder, cache=cache)
        second.build(semantic_blocks, content)
        assert Counting.calls == after_first, "the second build re-embedded what the cache already held"
