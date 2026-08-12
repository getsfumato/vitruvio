"""The inverted index: BM25 over pure-Python postings.

**Why not tantivy.** A large native wheel whose segment bytes are not reproducible, which wants to own a directory,
and whose tokenizer and scoring versioning are outside our control. For an index whose requirements are "cheap to
rebuild, deterministic, no native dependency in the CLI", that is all cost.

**Why not SQLite FTS5.** ``sqlite3`` is standard library but FTS5 is a *compile-time option*, and ``unicode61``
tokenisation varies with whichever SQLite each Python build bundles. Two clients would then tokenise the same text
differently -- and silently, producing different results rather than an error, which is the worst way to break the
paper's promise that any client can rebuild a structural index.

**Why BM25 rather than tf-idf.** These documents are short and wildly unequal in length: a ``SemanticBlock``
statement, a forty-step procedure, and a 1600-character canonical chunk sit in the same module. Term-frequency
saturation and length normalisation are exactly the two corrections tf-idf lacks, and they are what stop a long
procedure from outranking a precise one-line definition on a shared term.

**No float ever enters the postings file.** Term frequency is stored as an integer of *weighted* occurrences
scaled by :data:`WEIGHT_SCALE`, and the average length as an integer of thousandths. BM25 is computed at query time
from those integers, so the serialized body is byte-reproducible regardless of the FPU -- which is what makes a
golden fixture stable and a published digest meaningful.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

from boltzmann.indices.base import IndexKind

from vitruvio.indices import format as envelope
from vitruvio.indices.base import VitruvioIndex
from vitruvio.indices.projection import Projection
from vitruvio.indices.queries import Combine, Results, TermQuery
from vitruvio.indices.text import (
    analyze,
    analyzer_id,
    is_stem,
    phrase_terms,
    query_terms,
)
from vitruvio.stats import TermStats

K1 = 1.2
"""BM25 term-frequency saturation. The standard value; above it, repeated terms keep mattering for too long."""

B = 0.75
"""BM25 length normalisation. The standard value."""

WEIGHT_SCALE = 1000
"""Field weights are folded into an integer term frequency at this scale.

The point is not precision, it is that ``tf`` stays an ``int``. A float in the postings would make the file's bytes
depend on the platform's floating-point behaviour, and the reproducibility the paper asks of a structural index
would be gone.
"""

TOP_TERMS = 100
"""How many of the most frequent terms to report in the statistics.

Enough for the planner to notice that a query term is too common to filter on -- which is when it should apply a
bitmap mask before scoring rather than after.
"""

VOCABULARY_LIMIT = 200_000
"""Above this, only the head's document frequencies are reported exactly and the tail is bounded.

An exact table over a million terms would dominate the statistics file for no benefit: what the planner needs from
a rare term is "rare", and a bound gives it that.
"""


class InvertedIndex(VitruvioIndex):
    """
    Lexical retrieval with BM25 over two term spaces.

    Attributes:
        memory_type (MemoryType): Which module this indexes.
        language (str | None): Force a stemmer rather than guessing per document.
    """

    KIND: ClassVar[IndexKind] = IndexKind.INVERTED
    REBUILDABLE: ClassVar[bool] = True
    BODY_VERSION: ClassVar[int] = 1
    ENGINE: ClassVar[str] = "python-postings"

    def __init__(self, *args: Any, language: str | None = None, **kwargs: Any) -> None:
        """
        Build the index.

        Args:
            language (str | None): Force a stemmer for every document, rather than guessing per document. Worth
                setting for a single-language brain: the guess is deterministic but a short document with no
                function words falls back to the default.
        """
        self.language = language
        super().__init__(*args, **kwargs)

    def _reset(self) -> None:
        """Discard the postings and the length table."""
        # term -> ordinal -> weighted integer tf
        self._postings: dict[str, dict[int, int]] = {}
        # term -> ordinal -> positions, only for the raw space, only where a phrase could need them
        self._positions: dict[str, dict[int, tuple[int, ...]]] = {}
        self._lengths: dict[int, int] = {}
        self._languages: dict[int, str] = {}
        self._total_length = 0

    def _apply(self, projection: Projection) -> None:
        """Analyse every weighted field and fold it into the postings."""
        ordinal = self._table.ordinal(projection.block_id)
        if ordinal is None or not projection.fields:
            return

        length = 0
        language = self.language or "en"
        offset = 0
        for field in projection.fields:
            if not field.text.strip():
                continue
            analysis = analyze(field.text, self.language)
            language = analysis.language
            length += analysis.length
            weight = round(field.weight * WEIGHT_SCALE)

            for term in analysis.terms:
                self._postings.setdefault(term, {})
                self._postings[term][ordinal] = self._postings[term].get(ordinal, 0) + weight

            # Positions are recorded only for the raw space, and offset by the fields already consumed, so a
            # phrase cannot match across a field boundary -- "Fourier" ending one field and "series" starting the
            # next is not a phrase, and treating it as one would be a false positive nobody could explain.
            for term, positions in analysis.positions.items():
                self._positions.setdefault(term, {})
                existing = self._positions[term].get(ordinal, ())
                self._positions[term][ordinal] = existing + tuple(offset + position for position in positions)
            offset += analysis.length + 1

        if length:
            self._lengths[ordinal] = length
            self._languages[ordinal] = language
            self._total_length += length

    @property
    def average_length(self) -> float:
        """Mean document length in tokens, for BM25's normalisation."""
        return self._total_length / len(self._lengths) if self._lengths else 0.0

    def _capability_extra(self) -> dict[str, Any]:
        """Nothing kind-specific beyond what the base reports."""
        return {}

    def _fragment_extra(self) -> dict[str, Any]:
        """
        The vocabulary view the planner costs a lexical plan against.

        ``document_frequency`` is exact for the head and bounded for the tail. What the planner needs from a term
        is its selectivity, and for a rare term a bound answers that; an exact table over a million terms would
        dominate the statistics file to say the same thing.
        """
        frequencies = {term: len(postings) for term, postings in self._postings.items() if is_stem(term)}
        ordered = sorted(frequencies.items(), key=lambda pair: (-pair[1], pair[0]))
        head = dict(ordered[:VOCABULARY_LIMIT])
        tail = ordered[VOCABULARY_LIMIT:]

        return {
            "terms": TermStats(
                doc_count=len(self._lengths),
                vocabulary=len(frequencies),
                average_length=self.average_length,
                document_frequency=head,
                postings=sum(len(postings) for postings in self._postings.values()),
                tail_max_frequency=tail[0][1] if tail else 0,
            )
        }

    def _header_extra(self) -> dict[str, Any]:
        """The analyzer's identity, and the scoring parameters, so a file is never ambiguous."""
        return {
            "analyzer": analyzer_id(),
            "k1": K1,
            "b": B,
            "weight_scale": WEIGHT_SCALE,
            "terms": len(self._postings),
            "languages": sorted(set(self._languages.values())),
        }

    def header(self) -> envelope.Header:
        """The base header, with the analyzer recorded where a reader will look for it."""
        return super().header().model_copy(update={"analyzer_id": analyzer_id()})

    def _dump_state(self) -> dict[str, Any]:
        """
        Sorted terms, sorted postings, integers throughout.

        ``avgdl`` is stored in thousandths for the same reason ``tf`` is scaled: keeping every number in the body
        an integer is what makes the bytes independent of the platform.
        """
        return {
            "postings": {
                term: {str(ordinal): weight for ordinal, weight in sorted(postings.items())}
                for term, postings in sorted(self._postings.items())
            },
            "positions": {
                term: {str(ordinal): list(positions) for ordinal, positions in sorted(entries.items())}
                for term, entries in sorted(self._positions.items())
            },
            "lengths": {str(ordinal): length for ordinal, length in sorted(self._lengths.items())},
            "languages": {str(ordinal): language for ordinal, language in sorted(self._languages.items())},
            "average_length_milli": round(self.average_length * 1000),
        }

    def _load_body(self, body: dict[str, Any]) -> None:
        """Restore the postings."""
        self._reset()
        for term, postings in body.get("postings", {}).items():
            self._postings[term] = {int(ordinal): int(weight) for ordinal, weight in postings.items()}
        for term, entries in body.get("positions", {}).items():
            self._positions[term] = {int(ordinal): tuple(positions) for ordinal, positions in entries.items()}
        self._lengths = {int(ordinal): int(length) for ordinal, length in body.get("lengths", {}).items()}
        self._languages = {int(ordinal): str(language) for ordinal, language in body.get("languages", {}).items()}
        self._total_length = sum(self._lengths.values())

    # --- Scoring --------------------------------------------------------------

    def document_frequency(self, term: str) -> int:
        """How many blocks contain a term. Zero for one the index has never seen."""
        return len(self._postings.get(term, ()))

    def _idf(self, term: str) -> float:
        """
        BM25's inverse document frequency.

        The ``+ 0.5`` smoothing keeps this positive for a term present in more than half the corpus, where the
        unsmoothed form goes negative and starts *penalising* a match.
        """
        documents = len(self._lengths)
        frequency = self.document_frequency(term)
        if not documents or not frequency:
            return 0.0
        return math.log(1 + (documents - frequency + 0.5) / (frequency + 0.5))

    def _score(self, ordinal: int, terms: tuple[str, ...]) -> float:
        """
        BM25 for one block against a set of terms.

        Normalised by the maximum achievable score for this query, so a perfect match is ``1.0`` and scores are
        comparable *across* queries. That comparability is what makes fusion with another index's scores meaningful
        rather than arbitrary.
        """
        average = self.average_length or 1.0
        length = self._lengths.get(ordinal, 0)
        denominator_length = K1 * (1 - B + B * length / average)

        total = 0.0
        ceiling = 0.0
        for term in terms:
            idf = self._idf(term)
            if idf <= 0.0:
                continue
            # The best any block could do on this term: saturated tf, shortest plausible length.
            ceiling += idf * (K1 + 1) / (K1 * (1 - B) + 1)
            weighted = self._postings.get(term, {}).get(ordinal)
            if not weighted:
                continue
            frequency = weighted / WEIGHT_SCALE
            total += idf * (frequency * (K1 + 1)) / (frequency + denominator_length)

        if not ceiling:
            return 0.0
        # Clamped, because the ceiling assumes an unweighted term frequency and a field weight above 1.0 can beat
        # it. Reporting 1.02 would contradict the documented range and, worse, would break fusion downstream --
        # reciprocal-rank fusion tolerates any scale, but a normalised-score path assumes a bounded one.
        return min(1.0, total / ceiling)

    def _phrase_matches(self, ordinal: int, phrase: tuple[str, ...]) -> bool:
        """
        Whether a block contains a phrase, by positional intersection over the raw space.

        A **filter**, never a score bonus. Keeping it out of the score means the ranking stays pure BM25 over the
        phrase's terms, which is a number that can be explained; a phrase bonus is a magic constant.
        """
        if not phrase:
            return True
        first = self._positions.get(phrase[0], {}).get(ordinal)
        if not first:
            return False
        for start in first:
            if all(
                (start + offset) in self._positions.get(term, {}).get(ordinal, ())
                for offset, term in enumerate(phrase[1:], start=1)
            ):
                return True
        return False

    def lookup(self, query: TermQuery, limit: int = 10) -> Results:
        """
        Score blocks against a lexical query.

        Args:
            query (TermQuery): Terms, combination, an optional phrase, and an optional ordinal mask.
            limit (int): How many to return. Zero means all.

        Returns:
            Results: Scored hits. ``exhausted`` is false when the limit cut candidates, because the planner needs
            to know a bundle may be missing matches even when it returned fewer than asked for.
        """
        terms = query.terms
        if terms and not any(term.startswith(("t:", "x:")) for term in terms):
            # A caller who passed raw words rather than analysed terms: analyse them with the *same* analyzer the
            # documents went through, which is the only way the two sides cannot drift.
            terms = query_terms(" ".join(terms), query.language)
        if not terms and not query.phrase:
            return Results(consulted=self.KIND.value)

        candidates: set[int] = set()
        stemmed = [term for term in terms if is_stem(term)]
        for term in terms:
            candidates.update(self._postings.get(term, {}))

        if query.combine is Combine.ALL:
            # One token expands into several terms -- a stem per candidate language, plus the raw form -- so
            # intersecting the flat term list would require a block to contain every expansion of every token,
            # which nothing can. Groups preserve the token boundary: OR within a token, AND across tokens.
            groups = query.groups or tuple((term,) for term in stemmed)
            for group in groups:
                matched: set[int] = set()
                for term in group:
                    matched |= set(self._postings.get(term, {}))
                candidates &= matched

        if query.allow is not None:
            # Applied *before* scoring. Filter-then-score is the whole reason a bitmap prefilter is cheap: scoring
            # a candidate costs a BM25 evaluation, and discarding it afterwards wastes all of it.
            candidates &= set(query.allow)

        phrase = phrase_terms(query.phrase) if query.phrase else ()
        if phrase:
            candidates = {ordinal for ordinal in candidates if self._phrase_matches(ordinal, phrase)}

        scored = [(ordinal, self._score(ordinal, terms)) for ordinal in candidates]
        scored = [(ordinal, score) for ordinal, score in scored if score > 0.0]
        return self._results(scored, limit=limit)

    def search(self, query: Any, limit: int = 10) -> list[tuple[Any, float]]:
        """
        The SDK's entry point.

        Accepts a :class:`~vitruvio.indices.queries.TermQuery` or a bare query string.

        Args:
            query (Any): The query.
            limit (int): How many to return.

        Returns:
            list[tuple[Any, float]]: Block identities and scores.
        """
        from boltzmann.identity.digest import BlockId

        if isinstance(query, str):
            query = TermQuery(terms=query_terms(query, self.language))
        if not isinstance(query, TermQuery):
            return []

        results = self.lookup(query, limit=limit)
        return [(BlockId.parse(hit.block_id), hit.score) for hit in results.hits]

    def vocabulary(self) -> tuple[str, ...]:
        """Every stemmed term held, for the planner's out-of-vocabulary check."""
        return tuple(sorted(term for term in self._postings if is_stem(term)))

    def frequent_terms(self, count: int = TOP_TERMS) -> list[tuple[str, int]]:
        """
        The most common terms and their document frequencies.

        A term present in most of the module cannot filter, and the planner's response is to apply a mask before
        scoring rather than to widen the candidate pool.

        Args:
            count (int): How many to return.

        Returns:
            list[tuple[str, int]]: Term and document frequency, most frequent first.
        """
        frequencies = [(term, len(postings)) for term, postings in self._postings.items() if is_stem(term)]
        frequencies.sort(key=lambda pair: (-pair[1], pair[0]))
        return frequencies[:count]
