"""Typed lifecycle results: the snapshot, and the state that carries it.

The second vertical slice, after reconciliation. It is this one next because the snapshot is the most-read result
in the runtime -- ``state``, ``verify``, ``history``, every distribution operation and the diagnosis all embed it
through :func:`vitruvio.runtime.wire.snapshot` -- so one declaration here types the field names for all of them at
once. It is also the shape nothing else could tell you: `snapshot.digest` and `snapshot.block_count` are
*computed* from the document rather than stored in it, which is exactly the kind of key that goes missing when
somebody edits the wire function and nothing complains.

Same idiom as :mod:`vitruvio.runtime.reconcile_result`, deliberately: ``TypedDict`` for a static contract over a
payload that stays a plain dictionary, and ``cast`` at the one place a pydantic dump becomes it. The alternative
-- validating on the way out -- would make every read pay for a check that the operation already guarantees.
"""

from __future__ import annotations

from typing import TypedDict


class ActorResult(TypedDict):
    """Who this brain attributes writes to."""

    id: str
    kind: str
    name: str | None


class OriginResult(TypedDict):
    """Where an installed brain was pulled from."""

    reference: str
    tag: str
    snapshot: str
    partial: bool


class ModuleResult(TypedDict):
    """One memory module's reference inside a snapshot."""

    memory_type: str
    root: str
    block_count: int
    composition: str
    layout: str
    index_digest: str | None
    embedding_model: str | None
    tombstones: list[str] | None


class SnapshotResult(TypedDict):
    """A version, with the two fields computed rather than stored.

    ``digest`` cannot be in the document -- a snapshot would have to contain its own hash -- and ``block_count``
    is the sum over the module references. Both are why :func:`vitruvio.runtime.wire.snapshot` exists, and both
    are the fields a `model_dump` alone silently omits.
    """

    digest: str
    block_count: int
    installed: list[str]
    boltzmann: int
    created_at: str
    parents: list[str]
    modules: dict[str, ModuleResult]
    labels: dict[str, str] | None
    trust_root: str | None


class BrainStateResult(TypedDict):
    """The head pointer file, as the SDK writes it."""

    boltzmann: int
    snapshot: str
    retained: list[str]
    origin: OriginResult | None


class StateResult(TypedDict):
    """What is installed, at which version, pulled from where."""

    brain: str
    brain_origin: str
    state: BrainStateResult
    snapshot: SnapshotResult
    installed: list[str]
    block_count: int
    origin: OriginResult | None
    ancestry: list[str]
    actor: ActorResult


__all__ = [
    "ActorResult",
    "BrainStateResult",
    "ModuleResult",
    "OriginResult",
    "SnapshotResult",
    "StateResult",
]
