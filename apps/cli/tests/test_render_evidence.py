"""The evidence table names a match by the runtime's one rule, and says ``(unnamed)`` when nothing does.

Unreachable through the CLI with a small brain -- a provenance match and a label-free relation need a governed
catalog to arise -- so the renderer is driven directly, the way ``test_compound`` drives the compound views.
"""

from __future__ import annotations

from typing import Any, cast

from rich.console import Console

from tests.match_payloads import match as _match
from vitruvio.cli import render
from vitruvio.runtime.retrieval_result import SearchResult


def _rendered(parts: list[Any]) -> str:
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    for part in parts:
        console.print(part)
    return console.export_text()


def test_a_match_is_named_the_way_a_browse_row_is() -> None:
    record = {"record_type": "registration", "block": "sha256:" + "1" * 64}
    relation = {"kind": "relation", "relations": [{"predicate": "classified_as", "target": "sha256:" + "2" * 64}]}
    data = cast(
        SearchResult,
        {
            "matches": [
                _match("provenance", {"record": record}),
                _match("semantic", relation),
                _match("canonical", {}, resolvable=False),
            ],
            "verified_against": {},
            "truncated": False,
            "authorship": None,
            "all_verified": True,
        },
    )

    text = _rendered(render.bundle(data))

    assert "registration" in text
    assert "Relation · classified_as" in text
    assert "(unnamed)" in text
    assert "not resolvable" in text
    assert "no identifying field" not in text
