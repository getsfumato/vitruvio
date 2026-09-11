"""The evidence table names a match by the runtime's one rule, and says ``(unnamed)`` when nothing does.

Unreachable through the CLI with a small brain -- a provenance match and a label-free relation need a governed
catalog to arise -- so the renderer is driven directly, the way ``test_compound`` drives the compound views.
"""

from __future__ import annotations

from typing import Any, cast

from rich.console import Console

from tests.match_payloads import match as _match
from vitruvio.cli import render
from vitruvio.cli.render import theme
from vitruvio.runtime.browse_result import BlocksResult, ProjectedRowResult
from vitruvio.runtime.retrieval_result import SearchResult


def _rendered(parts: list[Any]) -> str:
    console = Console(record=True, width=120, force_terminal=False, color_system=None, theme=theme.THEME)
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


def _row(**fields: Any) -> ProjectedRowResult:
    base = {
        "block_id": "sha256:" + "b" * 64,
        "memory_type": "semantic",
        "title": "a diagram",
        "detail": "",
        "resolvable": True,
        "authorship": {"applicable": False, "complete": True, "provenance": None, "claims": []},
    }
    return cast(ProjectedRowResult, {**base, **fields})


def _page(*rows: ProjectedRowResult) -> BlocksResult:
    return cast(
        BlocksResult,
        {
            "memory_type": "semantic",
            "root": "sha256:" + "r" * 64,
            "block_count": len(rows),
            "matched": len(rows),
            "offset": 0,
            "limit": 100,
            "rows": list(rows),
            "truncated": False,
            "filter": None,
            "installed": True,
            "provenance": None,
        },
    )


def test_the_browse_table_names_the_bytes_a_derived_block_cites() -> None:
    """The type column falls through to the content a derived block names, which was the TUI's rule alone.

    A semantic block whose datum is a diagram has a media type the same way a canonical PDF does, and the CLI
    table printed nothing there while the TUI printed it -- the same row, two answers. One rule now, in
    ``BrowseRowView``, and this is the cell that used to be blank.
    """
    page = _page(_row(content={"blob": "sha256:" + "c" * 64, "media_type": "image/png", "size": 2048}))
    assert "image/png" in _rendered(render.rows(page))


def test_the_browse_table_prefers_the_rows_own_media_type_to_the_one_it_cites() -> None:
    """A canonical block *is* its bytes; a derived one only names some. The row's own type wins where both exist."""
    page = _page(
        _row(
            memory_type="canonical",
            media_type="application/pdf",
            content={"blob": "sha256:" + "c" * 64, "media_type": "image/png", "size": 2048},
        )
    )
    rendered = _rendered(render.rows(page))
    assert "application/pdf" in rendered
    assert "image/png" not in rendered
