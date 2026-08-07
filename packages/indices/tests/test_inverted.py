"""The analyzer and the inverted index: determinism, bilingual behaviour, and BM25's properties."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from boltzmann.blocks.memory_type import MemoryType

from vitruvio.indices import (
    Combine,
    InvertedIndex,
    MemoryContent,
    TermQuery,
    analyze,
    analyzer_id,
    query_groups,
    query_terms,
    tokenize,
)
from vitruvio.indices.text import (
    ENGLISH_STOPWORDS,
    SPANISH_STOPWORDS,
    guess_language,
    out_of_vocabulary,
    phrase_terms,
    stem,
)

if TYPE_CHECKING:
    from pathlib import Path

    from boltzmann.blocks.semantic import SemanticBlock


class TestAnalyzerDeterminism:
    def test_the_identity_names_what_it_depends_on(self) -> None:
        """Unicode tables and Snowball are both versioned, and both change what a term is."""
        identity = analyzer_id()
        assert identity.startswith("vitruvio-analyzer/1")
        assert "+unicode" in identity
        assert "+snowball" in identity
        assert "unknown" not in identity, "the stemmer version must resolve, or drift detection is defeated"

    def test_tokenisation_is_unicode_aware(self) -> None:
        assert tokenize("señales periódicas") == ["señales", "periódicas"]

    def test_a_compound_identifier_survives_as_one_token(self) -> None:
        """Splitting on underscore turns `a_n` into two single characters, both of which then vanish -- so a brain
        about Fourier series could not find its own coefficient notation."""
        assert tokenize("a_n y b_m") == ["a_n", "b_m"]

    def test_a_lone_symbol_is_still_dropped(self) -> None:
        """It matches too much to filter, which is the whole reason for a minimum length."""
        assert tokenize("2/T") == []

    def test_single_characters_are_dropped(self) -> None:
        assert tokenize("a b cd") == ["cd"]

    def test_folding_is_case_and_form_insensitive(self) -> None:
        assert tokenize("FOURIER") == tokenize("fourier")

    def test_the_same_text_analyses_identically_every_time(self) -> None:
        first, second = analyze("Una funcion periodica"), analyze("Una funcion periodica")
        assert first == second

    def test_the_analyzer_and_the_hashing_embedder_split_text_the_same_way(self) -> None:
        """One tokenizer, asserted from the side that can see both.

        `embeddings` sits below `indices`, so the folding primitive lives down there and the analyzer re-exports it.
        This test is the guard on that arrangement: the hashing embedder's vectors *are* a bag of these tokens, and
        two tokenizers that agree today and drift later would move every vector while the model tag went on claiming
        they had not moved. The assertion belongs here because this is the layer allowed to import both.
        """
        from vitruvio.embeddings import folding

        assert folding.tokenize is tokenize, "the analyzer must not have grown a second tokenizer"
        for text in ("señales periódicas", "a_n y b_m", "FOURIER", "2/T", "impuesto a las ganancias"):
            assert folding.tokenize(text) == tokenize(text)

    def test_the_identity_carries_the_folding_version_separately(self) -> None:
        """Folding is shared with the embedder and versioned on its own, so both halves have to be visible."""
        from vitruvio.embeddings.folding import FOLDING_VERSION

        assert f"/1.{FOLDING_VERSION}+" in analyzer_id()


class TestLanguageGuess:
    def test_spanish_function_words_pick_spanish(self) -> None:
        assert guess_language(tokenize("una funcion periodica de las senales")) == "es"

    def test_english_function_words_pick_english(self) -> None:
        assert guess_language(tokenize("a periodic function of the signal")) == "en"

    def test_no_function_words_is_stable_rather_than_arbitrary(self) -> None:
        """A formula or a bare label must get the same answer on every machine."""
        assert guess_language(tokenize("fourier laplace")) == guess_language(tokenize("fourier laplace"))

    def test_no_tokens_is_not_an_error(self) -> None:
        assert guess_language([]) in {"en", "es"}

    def test_the_two_stopword_lists_do_not_contradict_each_other(self) -> None:
        """A word in both lists carries no signal for the guess. Some overlap is expected; a lot would be a bug."""
        overlap = ENGLISH_STOPWORDS & SPANISH_STOPWORDS
        assert len(overlap) < 12, f"too much overlap for the guess to discriminate: {sorted(overlap)}"

    def test_spanish_stemming_actually_handles_spanish(self) -> None:
        """The reason snowball is a dependency: a hand-rolled English stripper mangles -ando and -acion."""
        assert stem("ganancias", "es") == stem("ganancia", "es")
        assert stem("calculando", "es").startswith("calcul")
        assert stem("normalizacion", "es").startswith("normaliz")


class TestQueryAnalysis:
    def test_function_words_are_dropped_from_a_query(self) -> None:
        """Including them makes the filter stop filtering -- the SDK measured `an` matching 14 of 15 blocks."""
        terms = query_terms("the periodic function of time")
        assert not any(term.endswith((":the", ":of")) for term in terms)

    def test_a_query_of_only_function_words_keeps_them(self) -> None:
        """ "No terms" and "no matches" are different answers, so an all-stopword query must still analyse."""
        assert query_terms("the of and") != ()

    def test_both_term_spaces_are_produced(self) -> None:
        terms = query_terms("ganancias")
        assert any(term.startswith("t:") for term in terms)
        assert any(term.startswith("x:") for term in terms)

    def test_a_short_query_with_no_language_signal_is_stemmed_both_ways(self) -> None:
        """Committing to one stemmer here loses recall in the other outright, and two words is the common case."""
        terms = query_terms("armonico ortogonal")
        stems = {term for term in terms if term.startswith("t:")}
        assert len(stems) > 2, f"expected both languages' stems, got {sorted(stems)}"

    def test_a_query_with_clear_language_signal_uses_one_stemmer(self) -> None:
        terms = query_terms("las ganancias del ejercicio")
        stems = {term for term in terms if term.startswith("t:")}
        assert stems == {"t:gananci", "t:ejercici"}

    def test_an_explicit_language_overrides_the_guess(self) -> None:
        assert query_terms("ganancias", language="en") != query_terms("ganancias", language="es")

    def test_groups_preserve_the_token_boundary(self) -> None:
        groups = query_groups("fourier coeficientes")
        assert len(groups) == 2
        assert all(len(group) >= 2 for group in groups)

    def test_a_phrase_keeps_its_function_words(self) -> None:
        """`impuesto a las ganancias` is not a phrase without its prepositions."""
        assert phrase_terms("impuesto a las ganancias") == ("x:impuesto", "x:las", "x:ganancias")


class TestInvertedIndex:
    @pytest.fixture
    def index(self, semantic_blocks: list[SemanticBlock], content: MemoryContent) -> InvertedIndex:
        built = InvertedIndex(MemoryType.SEMANTIC)
        built.build(semantic_blocks, content)
        return built

    def labels(self, index: InvertedIndex, blocks: list[SemanticBlock], query: TermQuery) -> list[str]:
        """Resolve hits back to labels, so an assertion reads as a retrieval claim."""
        by_id = {str(block.block_id): block.label for block in blocks}
        return [by_id[hit.block_id] for hit in index.lookup(query, limit=5).hits]

    def test_a_term_finds_its_block(self, index: InvertedIndex, semantic_blocks: list[SemanticBlock]) -> None:
        found = self.labels(index, semantic_blocks, TermQuery(terms=query_terms("Laplace")))
        assert found == ["Transformada de Laplace"]

    def test_stemming_makes_a_plural_match_a_singular(
        self, index: InvertedIndex, semantic_blocks: list[SemanticBlock]
    ) -> None:
        singular = self.labels(index, semantic_blocks, TermQuery(terms=query_terms("armonico")))
        plural = self.labels(index, semantic_blocks, TermQuery(terms=query_terms("armonicos")))
        assert singular == plural != []

    def test_a_label_match_outranks_a_statement_match(
        self, index: InvertedIndex, semantic_blocks: list[SemanticBlock]
    ) -> None:
        """Field weights are the reason: a match in the name means more than a match in the body."""
        found = self.labels(index, semantic_blocks, TermQuery(terms=query_terms("Fourier")))
        assert found[0] == "Serie de Fourier"

    def test_every_score_is_bounded(self, index: InvertedIndex) -> None:
        """A weighted field can beat the per-term ceiling, and an unbounded score would break fusion."""
        for probe in ("armonico ortogonal", "Fourier", "serie de fourier descompone funcion periodica"):
            for hit in index.lookup(TermQuery(terms=query_terms(probe)), limit=5).hits:
                assert 0.0 <= hit.score <= 1.0, f"{probe} produced {hit.score}"

    def test_a_term_the_index_has_never_seen_matches_nothing(self, index: InvertedIndex) -> None:
        assert index.lookup(TermQuery(terms=query_terms("criptomoneda"))).hits == ()
        assert index.document_frequency("t:criptomoned") == 0

    def test_all_means_every_token_not_every_expansion(
        self, index: InvertedIndex, semantic_blocks: list[SemanticBlock]
    ) -> None:
        """One token expands into several terms, so intersecting the flat list asks for the impossible."""
        probe = "fourier coeficientes"
        query = TermQuery(terms=query_terms(probe), groups=query_groups(probe), combine=Combine.ALL)
        assert self.labels(index, semantic_blocks, query) == ["Coeficientes de Fourier"]

    def test_all_still_excludes_when_a_token_is_absent(
        self, index: InvertedIndex, semantic_blocks: list[SemanticBlock]
    ) -> None:
        probe = "fourier laplace"
        query = TermQuery(terms=query_terms(probe), groups=query_groups(probe), combine=Combine.ALL)
        assert self.labels(index, semantic_blocks, query) == []

    def test_a_phrase_is_order_sensitive(self, index: InvertedIndex, semantic_blocks: list[SemanticBlock]) -> None:
        forwards = TermQuery(terms=query_terms("funcion periodica"), phrase="funcion periodica")
        backwards = TermQuery(terms=query_terms("periodica funcion"), phrase="periodica funcion")
        assert self.labels(index, semantic_blocks, forwards) != []
        assert self.labels(index, semantic_blocks, backwards) == []

    def test_a_phrase_filters_rather_than_boosting(
        self, index: InvertedIndex, semantic_blocks: list[SemanticBlock]
    ) -> None:
        """Keeping it out of the score means the ranking stays pure BM25, which is a number that can be explained."""
        terms = query_terms("funcion periodica")
        without = index.lookup(TermQuery(terms=terms), limit=5)
        with_phrase = index.lookup(TermQuery(terms=terms, phrase="funcion periodica"), limit=5)

        scores = {hit.block_id: hit.score for hit in without.hits}
        for hit in with_phrase.hits:
            assert hit.score == pytest.approx(scores[hit.block_id])
        assert len(with_phrase.hits) <= len(without.hits)

    def test_a_phrase_cannot_span_a_field_boundary(
        self, index: InvertedIndex, semantic_blocks: list[SemanticBlock]
    ) -> None:
        """The last token of the label followed by the first of the statement is not a phrase."""
        block = semantic_blocks[0]
        crossing = f"{block.label.split()[-1]} {block.statement.split()[0]}"
        query = TermQuery(terms=query_terms(crossing), phrase=crossing)
        assert self.labels(index, semantic_blocks, query) == []

    def test_a_mask_is_applied_before_scoring(self, index: InvertedIndex) -> None:
        """Filter-then-score is the whole reason a bitmap prefilter is cheap."""
        everything = index.lookup(TermQuery(terms=query_terms("Fourier")), limit=5)
        masked = index.lookup(TermQuery(terms=query_terms("Fourier"), allow=frozenset({0})), limit=5)
        assert len(masked.hits) < len(everything.hits)

    def test_a_bare_string_is_analysed_with_the_same_analyzer(self, index: InvertedIndex) -> None:
        """One function over both sides, so document and query analysis cannot drift."""
        assert index.search("Laplace")

    def test_an_exhausted_flag_reports_a_cut_pool(self, index: InvertedIndex) -> None:
        """The planner needs this: a bundle whose candidates were cut is truncated even if it returned few."""
        wide = index.lookup(TermQuery(terms=query_terms("fourier periodica senos armonicos")), limit=1)
        assert wide.exhausted is False

    def test_out_of_vocabulary_is_computed_from_the_real_vocabulary(self, index: InvertedIndex) -> None:
        """The planner's best intent feature, and free: it falls out of the document frequencies."""
        vocabulary = index.vocabulary()
        assert out_of_vocabulary(query_terms("Laplace"), vocabulary) == 0.0
        assert out_of_vocabulary(query_terms("criptomoneda blockchain"), vocabulary) == 1.0

    def test_frequent_terms_are_reported_for_the_planner(self, index: InvertedIndex) -> None:
        """A term in most of the module cannot filter, so the planner masks before scoring rather than after."""
        frequent = index.frequent_terms(3)
        assert frequent
        assert frequent[0][1] >= frequent[-1][1]


class TestInvertedStatistics:
    def test_the_fragment_carries_the_vocabulary(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        index = InvertedIndex(MemoryType.SEMANTIC)
        index.build(semantic_blocks, content)
        stats = index.fragment().terms

        assert stats is not None
        assert stats.doc_count == 4
        assert stats.vocabulary > 0
        assert stats.average_length > 0
        assert stats.frequency("t:fourier") >= 1
        assert stats.frequency("t:criptomoned") == 0

    def test_out_of_vocabulary_ratio_comes_from_the_fragment(
        self, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        index = InvertedIndex(MemoryType.SEMANTIC)
        index.build(semantic_blocks, content)
        stats = index.fragment().terms
        assert stats is not None
        assert stats.out_of_vocabulary_ratio(("t:criptomoned",)) == 1.0
        assert stats.out_of_vocabulary_ratio(()) == 0.0


class TestInvertedPersistence:
    def test_the_postings_file_contains_no_floats(
        self, tmp_path: Path, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        """The reason tf is a scaled integer: a float would make the bytes depend on the platform's FPU."""
        index = InvertedIndex(MemoryType.SEMANTIC, tmp_path)
        index.build(semantic_blocks, content)

        body = index._dump_state()

        def floats(value: object) -> bool:
            if isinstance(value, float):
                return True
            if isinstance(value, dict):
                return any(floats(item) for item in value.values())
            if isinstance(value, (list, tuple)):
                return any(floats(item) for item in value)
            return False

        assert not floats(body), "a float in the postings breaks byte reproducibility"

    def test_the_bytes_depend_only_on_the_block_set(
        self, tmp_path: Path, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        first = InvertedIndex(MemoryType.SEMANTIC, tmp_path / "a")
        first.build(semantic_blocks, content)
        second = InvertedIndex(MemoryType.SEMANTIC, tmp_path / "b")
        second.build(list(reversed(semantic_blocks)), content)

        assert (tmp_path / "a" / "semantic.inverted.vidx").read_bytes() == (
            tmp_path / "b" / "semantic.inverted.vidx"
        ).read_bytes()

    def test_a_reloaded_index_answers_identically(
        self, tmp_path: Path, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        first = InvertedIndex(MemoryType.SEMANTIC, tmp_path)
        first.build(semantic_blocks, content)

        second = InvertedIndex(MemoryType.SEMANTIC, tmp_path)
        query = TermQuery(terms=query_terms("Fourier"))
        assert [(h.block_id, round(h.score, 6)) for h in second.lookup(query, limit=5).hits] == [
            (h.block_id, round(h.score, 6)) for h in first.lookup(query, limit=5).hits
        ]

    def test_the_analyzer_identity_is_recorded_in_the_header(
        self, tmp_path: Path, semantic_blocks: list[SemanticBlock], content: MemoryContent
    ) -> None:
        index = InvertedIndex(MemoryType.SEMANTIC, tmp_path)
        index.build(semantic_blocks, content)
        assert index.header().analyzer_id == analyzer_id()
