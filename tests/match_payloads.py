"""Hand-built match payloads, shared by the suites that drive a renderer without a brain behind it.

Here rather than in a ``conftest``: ``apps/cli/tests`` and ``packages/runtime/tests`` are separate rootdirs with no
common fixture directory, and ``tests`` is the one package both already import (see ``wire_contract``). Written once
because two copies of "what a match looks like" is how a field gets added to the type and to one of them.
"""

from __future__ import annotations

from typing import Any, cast

from vitruvio.runtime.retrieval_result import MatchResult

BLOCK_ID = "sha256:" + "a" * 64


def match(memory_type: str, content: dict[str, Any], **flags: Any) -> MatchResult:
    """One match of the given memory type, complete in every field ``_Match`` requires.

    ``flags`` overrides any of them, so a test that is about ``superseded_by`` says only that and inherits the rest.
    The ``cast`` is the cost ADR-0023 names: a hand-built dictionary is not assignable to a ``TypedDict``, and it
    falls here, on a test, rather than on a reader.
    """
    return cast(
        MatchResult,
        {
            "block_id": BLOCK_ID,
            "memory_type": memory_type,
            "score": "1.00",
            "sources": [],
            "verified": True,
            "resolvable": True,
            "superseded_by": None,
            "content": content,
            **flags,
        },
    )
