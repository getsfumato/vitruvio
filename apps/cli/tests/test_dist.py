"""Distribution command rendering."""

from __future__ import annotations

import pytest

from vitruvio.cli.commands.dist import _impact_count


@pytest.mark.parametrize(
    ("impact", "expected"),
    [
        ({"certainty": "exact", "blocks": 2}, "2 blocks"),
        ({"certainty": "approximate", "blocks": 2}, "approximately 2 blocks"),
        ({"certainty": "unknown", "blocks": None}, "an unknown number of blocks (impact unknown)"),
    ],
)
def test_human_output_names_pull_impact_certainty(impact: dict[str, object], expected: str) -> None:
    assert _impact_count(impact) == expected
