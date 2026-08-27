"""Pull-impact certainty and set-difference semantics."""

from __future__ import annotations

import pytest

from vitruvio.runtime.pull_impact import CompositionMembers, compare_members


@pytest.mark.parametrize(
    ("current", "baseline", "blocks"),
    [
        ({"new"}, {"old"}, 1),
        (set(), {"old"}, 0),
        ({"shared", "new"}, {"shared", "old"}, 1),
    ],
    ids=("equal-size-replacement", "removal", "simultaneous-add-remove"),
)
def test_planned_impact_uses_membership_not_total_counts(current: set[str], baseline: set[str], blocks: int) -> None:
    impact = compare_members(
        CompositionMembers(frozenset(current)),
        CompositionMembers(frozenset(baseline)),
        planned=True,
    )

    assert impact.certainty == "approximate"
    assert impact.blocks == blocks


def test_completed_impact_is_exact_when_both_compositions_are_readable() -> None:
    impact = compare_members(
        CompositionMembers(frozenset({"shared", "local"})),
        CompositionMembers(frozenset({"shared", "remote"})),
        planned=False,
    )

    assert impact.certainty == "exact"
    assert impact.blocks == 1
    assert impact.block_ids == ("local",)


def test_an_unreadable_composition_makes_the_count_unknown() -> None:
    impact = compare_members(
        CompositionMembers(frozenset({"known"})),
        CompositionMembers(frozenset(), ("canonical (missing)",)),
        planned=False,
    )

    assert impact.certainty == "unknown"
    assert impact.blocks is None
    assert impact.unreadable == ("canonical (missing)",)
