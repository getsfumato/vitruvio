"""Composing the evidence several brains returned for one query, without pretending their scores compare.

Each brain plans, executes, verifies and ranks on its own, and each bundle's scores are normalised so that *its*
best match reads ``1.00`` (:func:`vitruvio.planner.fusion.normalize`). Two ``1.00`` from two brains therefore say
nothing about each other, and sorting a concatenation by score would rank a weak best-in-brain above a strong
second-in-brain. Nothing here reads a score to order anything.

Two shapes over one payload:

* **grouped** -- every brain's list intact, in its own order, one after the other. Nothing is merged, so a block two
  brains both hold appears once per brain. This is the default because it claims nothing the bundles did not.
* **fused** -- reciprocal-rank fusion across brains, the same rule the planner uses across generators inside one
  brain: ``1 / (K + rank)`` per brain that returned the block, absence contributing zero. A block held by two
  brains is one block -- its identity is the hash of its content, with no actor, time or task in it -- so it
  accumulates from both and rises. That is the cross-brain signal a compound exists to surface.

Stateless: this module opens no brain and works over the dictionaries :func:`vitruvio.runtime.wire.evidence`
produced. ``vitruvio.planner.fusion`` is imported inside the function that needs it, because importing the planner
package pulls ``vitruvio.stats`` onto the eager path and ``test_import_cost`` forbids that.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

Member = tuple[str, Mapping[str, Any]]
"""A brain's name and the Evidence Bundle payload it returned."""


def summarize(name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """
    One brain's contribution, without its matches.

    ``verified_against`` stays here rather than being merged upward: two brains holding the same memory type have two
    roots, and a citation has to name the one it verified against.

    Args:
        name (str): The brain.
        payload (Mapping[str, Any]): What ``RetrievalOps.search`` returned for it.

    Returns:
        dict[str, Any]: Count, truncation, verification, roots and the plan that ran, if one did.
    """
    return {
        "brain": name,
        "count": len(payload.get("matches", [])),
        "truncated": bool(payload.get("truncated", False)),
        "all_verified": bool(payload.get("all_verified", True)),
        "verified_against": dict(payload.get("verified_against", {})),
        "plan": payload.get("plan"),
    }


def _origin(name: str, rank: int, match: Mapping[str, Any]) -> dict[str, Any]:
    """Where a match came from: which brain, at what rank there, with the score that brain gave it."""
    return {"brain": name, "rank": rank, "score": match.get("score")}


def grouped(members: Sequence[Member]) -> list[dict[str, Any]]:
    """
    Every brain's matches, brain by brain, each list in its own order and with its own scores.

    Args:
        members (Sequence[Member]): The brains and their payloads, in the order given.

    Returns:
        list[dict[str, Any]]: Matches, each carrying a one-element ``brains`` list naming where it came from.
    """
    composed: list[dict[str, Any]] = []
    for name, payload in members:
        for rank, match in enumerate(payload.get("matches", []), start=1):
            composed.append({**match, "brains": [_origin(name, rank, match)]})
    return composed


def fused(members: Sequence[Member], *, k: int | None = None) -> list[dict[str, Any]]:
    """
    One ranking across brains, by reciprocal rank.

    A block returned by several brains is one match: its block dictionary is the first brain's (content-addressed,
    so the payload is identical everywhere) and ``brains`` lists every brain that returned it with its rank there.
    The fused score is rescaled so the top match reads ``1.00`` and rendered the way the protocol carries a score,
    as a decimal string -- it is agreement between brains and strategies, not a probability.

    Args:
        members (Sequence[Member]): The brains and their payloads.
        k (int | None): The RRF constant. Defaults to the planner's own, so a compound and a single brain fuse by
            the same rule.

    Returns:
        list[dict[str, Any]]: Matches, best first, ordered on the full float before rendering.
    """
    from vitruvio.planner.fusion import RRF_K, render

    constant = RRF_K if k is None else k
    totals: dict[str, float] = {}
    first: dict[str, Mapping[str, Any]] = {}
    origins: dict[str, list[dict[str, Any]]] = {}
    for name, payload in members:
        for rank, match in enumerate(payload.get("matches", []), start=1):
            block = str(match["block_id"])
            totals[block] = totals.get(block, 0.0) + 1.0 / (constant + rank)
            first.setdefault(block, match)
            origins.setdefault(block, []).append(_origin(name, rank, match))

    # Ordered on the full float, before rendering collapses it to two decimals; the identity breaks ties so the
    # order is deterministic across runs and machines.
    ordered = sorted(totals, key=lambda block: (-totals[block], block))
    top = totals[ordered[0]] if ordered else 0.0
    return [
        {**first[block], "score": render(totals[block] / top if top > 0 else 0.0), "brains": origins[block]}
        for block in ordered
    ]


def compose(
    project: str | None,
    members: Sequence[Member],
    *,
    fuse: bool,
    skipped: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """
    The compound payload, in one shape for both modes.

    A consumer branches on ``fused`` and nothing else: ``members`` always carries each brain's own summary, and
    ``matches`` always carries ``brains`` per match.

    Args:
        project (str | None): The project the brains belong to.
        members (Sequence[Member]): The brains and their payloads, in the order given.
        fuse (bool): Fuse across brains rather than group by brain.
        skipped (Sequence[Mapping[str, Any]]): Declared brains that were not consulted, and why.

    Returns:
        dict[str, Any]: The payload.
    """
    summaries = [summarize(name, payload) for name, payload in members]
    payload: dict[str, Any] = {
        "project": project,
        "brains": [name for name, _ in members],
        "skipped": list(skipped),
        "fused": fuse,
        "members": summaries,
        "matches": fused(members) if fuse else grouped(members),
        "truncated": any(item["truncated"] for item in summaries),
        "all_verified": all(item["all_verified"] for item in summaries),
    }
    return payload


__all__ = ["Member", "compose", "fused", "grouped", "summarize"]
