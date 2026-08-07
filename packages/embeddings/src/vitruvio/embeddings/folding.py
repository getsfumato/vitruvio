"""Folding and token splitting -- the one definition, in the layer where everything that needs it can see it.

Two consumers need to agree on what a token is: the inverted index's analyzer (:mod:`vitruvio.indices.text`, which
adds language detection, stemming and the two term spaces on top) and the hashing embedder, whose vectors *are* a bag
of these tokens. ``indices`` sits above ``embeddings``, so the shared primitive lives here and ``indices.text``
re-exports it. Defining it twice would be the worse arrangement: two tokenizers that agree today and drift silently
later is precisely the failure mode that ruled out SQLite FTS5 for the inverted index.

Nothing here knows about a language. Folding and splitting are the parts of analysis that are the same for every
language, which is why they are also the parts that can live below the analyzer.
"""

from __future__ import annotations

import re
import unicodedata

FOLDING_VERSION = 1
"""Bumped when folding or token splitting changes.

Any change here moves every vector the hashing embedder produces and every term the inverted index stores, so it is
part of both their identities -- ``analyzer_id()`` and :class:`~vitruvio.embeddings.tag.ModelTag` each carry it.
"""

MINIMUM_LENGTH = 2
"""Tokens shorter than this are dropped.

A lone character is not a retrieval signal -- it matches too much to filter, and it is usually punctuation that
survived normalisation. Compound identifiers like ``a_n`` are unaffected, because :data:`TOKEN` keeps them whole.
"""

TOKEN = re.compile(r"[^\W_]+(?:_[^\W_]+)*", re.UNICODE)
"""Runs of letters and digits, Unicode-aware, with underscore treated as a *joiner* rather than a separator.

The joiner matters more than it looks. Splitting on underscore turns ``a_n`` into ``a`` and ``n``, both of which are
then dropped for being too short -- so a subscripted coefficient, which is exactly the kind of token a mathematical
brain needs to find, would become unsearchable.
"""


def normalize(text: str) -> str:
    """
    NFKC-normalize and case-fold.

    Args:
        text (str): Raw text.

    Returns:
        str: The folded form.
    """
    return unicodedata.normalize("NFKC", text).casefold()


def tokenize(text: str) -> list[str]:
    """
    Split normalized text into tokens.

    Args:
        text (str): Raw text; normalisation happens here.

    Returns:
        list[str]: Tokens of at least :data:`MINIMUM_LENGTH` characters, in order. Order is kept because phrase
        matching needs positions.
    """
    return [token for token in TOKEN.findall(normalize(text)) if len(token) >= MINIMUM_LENGTH]
