"""Composing several brains' bundles: grouped keeps every ranking intact; fused merges by rank and never by score.

Pure dictionary tests. Nothing here opens a brain: the helper works over what ``wire.evidence`` produced, and what
is under test is the composition rule, not retrieval.
"""

from __future__ import annotations

from typing import Any

from vitruvio.runtime.cross_brain import compose, fused, grouped, summarize


def match(block: str, score: str, **extra: Any) -> dict[str, Any]:
    """One match as ``wire.evidence`` renders it."""
    return {
        "block_id": f"sha256:{block}",
        "memory_type": "semantic",
        "content": {"label": block},
        "score": score,
        "sources": [],
        "verified": True,
        "resolvable": True,
        "superseded_by": None,
        **extra,
    }


def bundle(*matches: dict[str, Any], truncated: bool = False, root: str = "sha256:root") -> dict[str, Any]:
    """One brain's payload."""
    return {
        "matches": list(matches),
        "verified_against": {"semantic": root},
        "truncated": truncated,
        "all_verified": True,
        "plan": {"signature": "SeqScan"},
    }


class TestGrouped:
    def test_every_brain_keeps_its_own_order_and_its_own_scores(self) -> None:
        members = [("a", bundle(match("x", "1.00"), match("y", "0.40"))), ("b", bundle(match("z", "1.00")))]
        composed = grouped(members)
        assert [item["block_id"] for item in composed] == ["sha256:x", "sha256:y", "sha256:z"]
        assert [item["score"] for item in composed] == ["1.00", "0.40", "1.00"]
        assert [item["brains"] for item in composed] == [
            [{"brain": "a", "rank": 1, "score": "1.00"}],
            [{"brain": "a", "rank": 2, "score": "0.40"}],
            [{"brain": "b", "rank": 1, "score": "1.00"}],
        ]

    def test_a_block_two_brains_hold_appears_once_per_brain(self) -> None:
        """Grouped means nothing is merged, so the reader sees each brain's list exactly as that brain ranked it."""
        members = [("a", bundle(match("x", "1.00"))), ("b", bundle(match("x", "1.00")))]
        composed = grouped(members)
        assert [item["block_id"] for item in composed] == ["sha256:x", "sha256:x"]
        assert [item["brains"][0]["brain"] for item in composed] == ["a", "b"]


class TestFused:
    def test_a_block_two_brains_return_outranks_one_only_one_returns(self) -> None:
        """The cross-brain signal: rank 1 in two brains is ``2/61``, rank 1 in one is ``1/61``."""
        members = [
            ("a", bundle(match("shared", "1.00"), match("only-a", "0.90"))),
            ("b", bundle(match("shared", "1.00"))),
        ]
        composed = fused(members)
        assert [item["block_id"] for item in composed] == ["sha256:shared", "sha256:only-a"]
        assert composed[0]["score"] == "1.00"
        assert composed[0]["brains"] == [
            {"brain": "a", "rank": 1, "score": "1.00"},
            {"brain": "b", "rank": 1, "score": "1.00"},
        ]
        assert composed[1]["brains"] == [{"brain": "a", "rank": 2, "score": "0.90"}]

    def test_rank_decides_and_a_brains_own_score_does_not(self) -> None:
        """Brain ``a`` scored ``y`` at 0.40 and brain ``b`` scored it 1.00: neither number is used. ``y`` is rank 2
        in one brain and rank 1 in the other, which beats ``x`` at rank 1 in one brain alone."""
        members = [("a", bundle(match("x", "1.00"), match("y", "0.40"))), ("b", bundle(match("y", "1.00")))]
        composed = fused(members)
        assert [item["block_id"] for item in composed] == ["sha256:y", "sha256:x"]

    def test_scores_are_strings_and_non_increasing_with_the_top_at_one(self) -> None:
        members = [
            ("a", bundle(match("x", "1.00"), match("y", "0.50"), match("z", "0.10"))),
            ("b", bundle(match("y", "1.00"))),
        ]
        composed = fused(members)
        scores = [item["score"] for item in composed]
        assert all(isinstance(score, str) for score in scores)
        assert scores[0] == "1.00"
        assert [float(score) for score in scores] == sorted((float(score) for score in scores), reverse=True)

    def test_ties_break_on_the_block_identity_so_the_order_is_deterministic(self) -> None:
        members = [("a", bundle(match("b-block", "1.00"))), ("b", bundle(match("a-block", "1.00")))]
        assert [item["block_id"] for item in fused(members)] == ["sha256:a-block", "sha256:b-block"]

    def test_the_block_dictionary_is_the_first_brains(self) -> None:
        """Content-addressed, so the payload is identical everywhere; the first brain in the given order supplies it."""
        members = [
            ("a", bundle(match("x", "1.00", content={"label": "from a"}))),
            ("b", bundle(match("x", "1.00", content={"label": "from b"}))),
        ]
        assert fused(members)[0]["content"] == {"label": "from a"}

    def test_no_matches_anywhere_is_an_empty_list(self) -> None:
        assert fused([("a", bundle()), ("b", bundle())]) == []


class TestCompose:
    def test_one_shape_for_both_modes(self) -> None:
        members = [("a", bundle(match("x", "1.00"))), ("b", bundle(match("x", "1.00"), root="sha256:other"))]
        for fuse in (False, True):
            payload = compose("facultad", members, fuse=fuse)
            assert set(payload) == {
                "project",
                "brains",
                "skipped",
                "fused",
                "members",
                "matches",
                "truncated",
                "all_verified",
            }
            assert payload["fused"] is fuse
            assert payload["brains"] == ["a", "b"]
            assert all("brains" in item for item in payload["matches"])

    def test_roots_stay_per_brain_and_are_never_merged(self) -> None:
        """Two brains holding the same memory type have two roots; a citation names the one it verified against."""
        members = [("a", bundle(match("x", "1.00"))), ("b", bundle(match("x", "1.00"), root="sha256:other"))]
        payload = compose(None, members, fuse=True)
        assert "verified_against" not in payload
        assert [item["verified_against"] for item in payload["members"]] == [
            {"semantic": "sha256:root"},
            {"semantic": "sha256:other"},
        ]

    def test_truncation_and_verification_aggregate(self) -> None:
        members = [("a", bundle(match("x", "1.00"), truncated=True)), ("b", bundle())]
        payload = compose(None, members, fuse=False)
        assert payload["truncated"] is True
        assert payload["all_verified"] is True
        assert [item["count"] for item in payload["members"]] == [1, 0]
        assert [item["truncated"] for item in payload["members"]] == [True, False]

    def test_skipped_brains_are_reported_not_hidden(self) -> None:
        skipped = [{"brain": "c", "reason": "no layout at /nowhere"}]
        payload = compose(None, [("a", bundle()), ("b", bundle())], fuse=False, skipped=skipped)
        assert payload["skipped"] == skipped
        assert payload["brains"] == ["a", "b"]

    def test_a_summary_carries_the_plan_when_one_ran(self) -> None:
        assert summarize("a", bundle())["plan"] == {"signature": "SeqScan"}
        assert summarize("a", {"matches": []})["plan"] is None
