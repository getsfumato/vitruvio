"""Text analysis: one function over documents and queries, so the two cannot drift.

Every decision here is subordinated to a single requirement from the paper: a structural index is a
**deterministic function of the blocks**, so any client must be able to rebuild it and get the same thing. That
rules out most of the obvious choices.

* **No language detection library.** ``langdetect`` and friends change their output between releases, which would
  silently change which stemmer ran, which would silently change what matches. Language is guessed by counting
  hits against two fixed stopword lists -- a pure function of the text, and one whose failure mode is a wrong
  stemmer rather than a nondeterministic one.
* **Snowball for stemming.** Pure Python, vendored algorithms, versioned. The alternative -- hand-rolled suffix
  stripping -- is English-only in practice: the SDK sandbox's version mangles ``-ando`` and ``-ación``, and this
  brain is written in Spanish and English at once.
* **Two term spaces.** ``t:`` holds stems and drives BM25; ``x:`` holds the raw folded token and drives phrases
  and exact forms. Without the second space, stemming makes ``CUIT``, ``Art. 3`` and formula tokens unfindable;
  without the first, *ganancia* and *ganancias* stop matching each other.
* **The analyzer identity carries its dependencies.** Unicode tables move between Python releases, changing what
  ``\\w`` matches, and Snowball's algorithms are versioned. Both go into :func:`analyzer_id`, so a drift becomes a
  detectable rebuild rather than a silent change in scoring.

Stopwords are dropped from **queries** and kept in the **index**. That asymmetry is deliberate: a query of nothing
but function words has no signal, but a phrase like ``impuesto a las ganancias`` needs its function words present
in the postings to match positionally.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

# Reused rather than reimplemented. Divergence from the SDK's own list would show up as a differential-test
# failure that looks like a scoring bug, and the list itself is chosen by grammatical role rather than frequency.
from boltzmann.query.scan import STOPWORDS as ENGLISH_STOPWORDS

# Folding and splitting live one layer down, in `embeddings`, because the hashing embedder is a bag of exactly these
# tokens and `embeddings` cannot import `indices`. Re-exported here so this module stays the single place the rest of
# `indices` asks about text.
from vitruvio.embeddings.folding import (
    FOLDING_VERSION,
    MINIMUM_LENGTH,
    TOKEN,
    normalize,
    tokenize,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "MINIMUM_LENGTH",
    "TOKEN",
    "Analysis",
    "analyze",
    "analyzer_id",
    "guess_language",
    "is_stem",
    "normalize",
    "out_of_vocabulary",
    "phrase_terms",
    "query_groups",
    "query_terms",
    "stem",
    "strip_prefix",
    "tokenize",
]

ANALYZER_VERSION = 1
"""Bumped when stemming or term-space policy changes.

Folding and splitting have their own version, :data:`~vitruvio.embeddings.folding.FOLDING_VERSION`, because they are
shared with the hashing embedder; :func:`analyzer_id` carries both.
"""

STEM_PREFIX = "t:"
"""Marks a stemmed term. Scored by BM25."""

RAW_PREFIX = "x:"
"""Marks a raw folded token. Carries phrases and exact forms that stemming would destroy."""


# Spanish function words, mirroring the SDK's English list in intent: chosen by grammatical role, nothing
# domain-specific. Used both for the language guess and for dropping query terms.
SPANISH_STOPWORDS = frozenset(
    (
        # determiners
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "lo",
        "al",
        "del",
        "este",
        "esta",
        "estos",
        "estas",
        "ese",
        "esa",
        "esos",
        "esas",
        "aquel",
        "aquella",
        # connectives
        "y",
        "e",
        "o",
        "u",
        "pero",
        "sino",
        "aunque",
        "porque",
        "pues",
        "entonces",
        "tambien",
        "si",
        # prepositions
        "de",
        "a",
        "en",
        "con",
        "sin",
        "por",
        "para",
        "sobre",
        "entre",
        "hasta",
        "desde",
        "hacia",
        "segun",
        "tras",
        "durante",
        "mediante",
        "como",
        # auxiliaries and very common verbs
        "es",
        "son",
        "era",
        "eran",
        "ser",
        "sido",
        "siendo",
        "soy",
        "esta",
        "estan",
        "estar",
        "fue",
        "fueron",
        "haber",
        "ha",
        "han",
        "habia",
        "hay",
        "tiene",
        "tienen",
        "tener",
        "puede",
        "pueden",
        "debe",
        "deben",
        "sera",
        "seran",
        # pronouns
        "yo",
        "tu",
        "el",
        "ella",
        "nosotros",
        "ustedes",
        "ellos",
        "ellas",
        "me",
        "te",
        "se",
        "nos",
        "les",
        "le",
        "su",
        "sus",
        "mi",
        "mis",
        "nuestro",
        "nuestra",
        # interrogatives
        "que",
        "cual",
        "cuales",
        "quien",
        "quienes",
        "cuando",
        "donde",
        "como",
        "cuanto",
        "por que",
        # negation and place
        "no",
        "ni",
        "alli",
        "aqui",
        "ahi",
    )
)
"""Spanish function words. Same rationale as the SDK's English list: role, not frequency."""

LANGUAGES = ("en", "es")
"""Which stemmers this analyzer can select. Order is the tie-break, so it is not incidental."""

STOPWORDS_BY_LANGUAGE = {"en": ENGLISH_STOPWORDS, "es": SPANISH_STOPWORDS}


@lru_cache(maxsize=1)
def _snowball_version() -> str:
    """
    The stemmer library's version, or ``none`` when it is absent.

    Read from distribution metadata rather than from a ``__version__`` attribute, because snowballstemmer does not
    expose one. Getting this wrong is not cosmetic: the version is part of the analyzer identity, and an identity
    that says ``unknown`` cannot detect the stemmer drift it exists to detect.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("snowballstemmer")
    except PackageNotFoundError:  # pragma: no cover - a declared dependency
        return "none"


@lru_cache(maxsize=4)
def _stemmer(language: str):
    """A cached Snowball stemmer, or ``None`` when the library is absent."""
    try:
        import snowballstemmer
    except ModuleNotFoundError:  # pragma: no cover - a declared dependency
        return None
    try:
        return snowballstemmer.stemmer({"en": "english", "es": "spanish"}[language])
    except Exception:  # pragma: no cover - an unknown language name
        return None


def analyzer_id() -> str:
    """
    This analyzer's identity, including what it depends on.

    Recorded in every index header. Unicode's tables move between Python releases -- changing what ``\\w``
    matches, and therefore what a token is -- and Snowball is versioned. Both belong in the identity, so a drift
    shows up as a stale index that gets rebuilt rather than as scores that quietly changed.

    Returns:
        str: e.g. ``vitruvio-analyzer/1.1+unicode15.1.0+snowball2.2.0``. The version is
        ``<analyzer>.<folding>``: folding is versioned separately because the hashing embedder shares it, and both
        halves move an index's contents.
    """
    return (
        f"vitruvio-analyzer/{ANALYZER_VERSION}.{FOLDING_VERSION}"
        f"+unicode{unicodedata.unidata_version}+snowball{_snowball_version()}"
    )


def guess_language(tokens: Sequence[str]) -> str:
    """
    Which language's stemmer to use, by counting function words.

    A pure function of the tokens: no model, no network, no library whose behaviour changes between releases. The
    tie-break is the first entry of :data:`LANGUAGES`, so a document with no function words at all -- a formula, a
    list of labels -- gets a stable answer rather than an arbitrary one.

    Args:
        tokens (Sequence[str]): Already-normalized tokens.

    Returns:
        str: ``en`` or ``es``.
    """
    if not tokens:
        return LANGUAGES[0]
    counts = {
        language: sum(1 for token in tokens if token in STOPWORDS_BY_LANGUAGE[language]) for language in LANGUAGES
    }
    best = max(counts.values())
    if best == 0:
        return LANGUAGES[0]
    # max() over a dict is insertion-ordered, and LANGUAGES fixes that order, so ties resolve identically
    # everywhere rather than depending on hash iteration.
    for language in LANGUAGES:
        if counts[language] == best:
            return language
    return LANGUAGES[0]


def stem(token: str, language: str) -> str:
    """
    Stem one token.

    Args:
        token (str): A normalized token.
        language (str): Which stemmer.

    Returns:
        str: The stem, or the token unchanged when no stemmer is available -- which degrades recall rather than
        breaking, and is reflected in the analyzer id so an index built that way is detectably different.
    """
    stemmer = _stemmer(language)
    if stemmer is None:
        return token
    stemmed: str = stemmer.stemWord(token)
    return stemmed or token


@dataclass(frozen=True, slots=True)
class Analysis:
    """
    What the analyzer produced for one piece of text.

    Attributes:
        language (str): Which stemmer ran.
        tokens (tuple[str, ...]): Normalized tokens, in order.
        terms (tuple[str, ...]): Prefixed terms, in token order, with both spaces interleaved -- so a position in
            this tuple corresponds to a token position, which is what phrase matching needs.
        positions (dict[str, tuple[int, ...]]): Where each raw term occurs, for positional phrase matching.
    """

    language: str
    tokens: tuple[str, ...]
    terms: tuple[str, ...]
    positions: dict[str, tuple[int, ...]]

    @property
    def length(self) -> int:
        """Token count, which BM25 uses to normalise for document length."""
        return len(self.tokens)


def analyze(text: str, language: str | None = None) -> Analysis:
    """
    Analyse text into both term spaces.

    Args:
        text (str): Raw text.
        language (str | None): Force a stemmer rather than guessing.

    Returns:
        Analysis: Tokens, terms and positions.
    """
    tokens = tokenize(text)
    chosen = language if language in STOPWORDS_BY_LANGUAGE else guess_language(tokens)

    terms: list[str] = []
    positions: dict[str, list[int]] = {}
    for position, token in enumerate(tokens):
        raw = f"{RAW_PREFIX}{token}"
        terms.append(f"{STEM_PREFIX}{stem(token, chosen)}")
        terms.append(raw)
        positions.setdefault(raw, []).append(position)

    return Analysis(
        language=chosen,
        tokens=tuple(tokens),
        terms=tuple(terms),
        positions={term: tuple(where) for term, where in positions.items()},
    )


def query_terms(text: str, language: str | None = None) -> tuple[str, ...]:
    """
    The terms of a query that carry retrieval signal.

    Function words are dropped here and **kept** in the index. Including them makes the filter stop filtering --
    the SDK measured ``an`` alone matching fourteen of fifteen blocks in a brain that knew nothing about the
    subject -- but they must still be *in* the postings for a phrase to match positionally.

    A query of nothing but function words keeps them, matching the SDK's rule: the alternative is a query that
    analyses to nothing, and "no terms" and "no matches" are different answers.

    Args:
        text (str): The query.
        language (str | None): Force a stemmer.

    Returns:
        tuple[str, ...]: Prefixed terms, both spaces.
    """
    tokens = tokenize(text)
    if not tokens:
        return ()

    if language in STOPWORDS_BY_LANGUAGE:
        languages: tuple[str, ...] = (language,)
    else:
        detected = guess_language(tokens)
        confident = any(token in STOPWORDS_BY_LANGUAGE[detected] for token in tokens)
        # A query with no function words carries no language signal. Committing to one stemmer there loses recall
        # in the other outright -- `armonico ortogonal` stemmed as English does not match the same words stemmed as
        # Spanish -- and a two-word query is the common case. So stem under *both* and union the terms: two extra
        # stems per token, and the failure mode disappears. Documents keep a single language, because a document
        # long enough to index almost always has function words.
        languages = (detected,) if confident else LANGUAGES

    stopwords = STOPWORDS_BY_LANGUAGE[languages[0]]
    content = [token for token in tokens if token not in stopwords]
    if not content:
        content = tokens

    terms: list[str] = []
    for token in content:
        for candidate in languages:
            stemmed = f"{STEM_PREFIX}{stem(token, candidate)}"
            if stemmed not in terms:
                terms.append(stemmed)
        raw = f"{RAW_PREFIX}{token}"
        if raw not in terms:
            terms.append(raw)
    return tuple(terms)


def query_groups(text: str, language: str | None = None) -> tuple[tuple[str, ...], ...]:
    """
    The query's terms, grouped by the token each came from.

    What :func:`query_terms` returns flattened. The grouping is what lets an ``ALL`` combination mean "every token
    appears somewhere" rather than "every expansion of every token appears", which nothing satisfies.

    Args:
        text (str): The query.
        language (str | None): Force a stemmer.

    Returns:
        tuple[tuple[str, ...], ...]: One group per content token.
    """
    tokens = tokenize(text)
    if not tokens:
        return ()

    if language in STOPWORDS_BY_LANGUAGE:
        languages: tuple[str, ...] = (language,)
    else:
        detected = guess_language(tokens)
        confident = any(token in STOPWORDS_BY_LANGUAGE[detected] for token in tokens)
        languages = (detected,) if confident else LANGUAGES

    stopwords = STOPWORDS_BY_LANGUAGE[languages[0]]
    content = [token for token in tokens if token not in stopwords] or tokens

    groups: list[tuple[str, ...]] = []
    for token in content:
        group = [f"{STEM_PREFIX}{stem(token, candidate)}" for candidate in languages]
        group.append(f"{RAW_PREFIX}{token}")
        groups.append(tuple(dict.fromkeys(group)))
    return tuple(groups)


def phrase_terms(phrase: str) -> tuple[str, ...]:
    """
    The raw-space terms of a phrase, in order, function words included.

    No language parameter: the raw space is unstemmed by definition, so a phrase does not depend on which stemmer
    would have run. Function words stay, which is the whole reason a phrase needs the raw space -- "impuesto a las
    ganancias" is not a phrase without its prepositions.

    Args:
        phrase (str): The phrase.

    Returns:
        tuple[str, ...]: Raw terms, in order.
    """
    return tuple(f"{RAW_PREFIX}{token}" for token in tokenize(phrase))


def is_stem(term: str) -> bool:
    """Whether a term belongs to the stemmed space."""
    return term.startswith(STEM_PREFIX)


def strip_prefix(term: str) -> str:
    """The term without its space prefix, for display."""
    for prefix in (STEM_PREFIX, RAW_PREFIX):
        if term.startswith(prefix):
            return term[len(prefix) :]
    return term


def out_of_vocabulary(terms: Iterable[str], known: Iterable[str]) -> float:
    """
    The fraction of query terms a vocabulary has never seen.

    The planner's single best intent feature, and free. A query whose terms have zero document frequency cannot be
    answered lexically, and that is a fact about *this brain* rather than a guess about language.

    Args:
        terms (Iterable[str]): Query terms.
        known (Iterable[str]): The index's vocabulary.

    Returns:
        float: In ``[0, 1]``. Zero when there are no terms.
    """
    candidates = [term for term in terms if is_stem(term)]
    if not candidates:
        return 0.0
    vocabulary = set(known)
    return sum(1 for term in candidates if term not in vocabulary) / len(candidates)
