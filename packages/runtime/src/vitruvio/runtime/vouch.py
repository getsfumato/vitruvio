"""Telling the SDK that a locally-held vector index describes the installed version.

This module exists to work around one real gap, and it is deliberately the only place in vitruvio that touches an SDK
private.

## The gap

``Brain._vouched`` is the set of memory types whose non-rebuildable index this client may publish, and it is populated
in exactly two places (``brain.py``):

* ``_build``, when it feeds a **non-rebuildable** index -- which happens on the *write* path;
* ``_load_index``, when a vector layer is restored from an artifact already in ``index.json``.

``rebuild_indices`` deliberately skips non-rebuildable indices, and ``_restore_travelling`` only inspects manifests
that already exist. So there is a state the SDK cannot reach: vitruvio holds a perfectly current vector index, loaded
from its own sidecar, whose ``bound_root`` matches the module root exactly -- and ``pack()`` omits the layer, because
nothing told ``Brain`` the index is trustworthy. A brain that has never been packed has no manifest to restore from,
so the first publish of any brain silently ships without the one index a consumer cannot rebuild.

The protocol is explicit that this is the worse failure: an artifact whose layer claims a vector index and carries none
is worse than one that omits the layer, because a consumer can detect an absence and cannot detect an emptiness. Here
the absence is silent on the *producer* side, which is the same problem one step earlier.

## What to ask upstream

``Brain.vouch(memory_type)``, or ``rebuild_indices(..., include_travelling=True)``. It is the natural counterpart to
``travelling_indices``: a client that holds a non-rebuildable index for the installed composition should be able to say
so. Small, backward-compatible, and it would delete this module.

## What this does in the meantime

Verifies the binding itself -- the index's ``bound_root`` must equal the module's current root -- and then calls
``Brain._build`` for that one index, which is the SDK's own vouching path. Rebuilding is cheap because every vector
comes back from the embedding cache: no model call, no network.

Pinned to an SDK version, and covered by ``packages/runtime/tests/test_vouch.py``, which fails loudly if either
private moves. That guard is the point, and it is not optional: :func:`supported` degrades to a *reported* warning,
so without it the private going away is a green suite and a brain that quietly publishes nothing.
"""

from __future__ import annotations

from collections.abc import Iterable

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.brain import Brain
from boltzmann.indices.base import IndexKind

VOUCHED_ATTRIBUTE = "_vouched"
"""The SDK private this depends on. Named once, so the test that guards it and the code that uses it agree."""


def supported(brain: Brain) -> bool:
    """
    Whether this SDK version still has the private this workaround needs.

    Args:
        brain (Brain): The brain.

    Returns:
        bool: Whether vouching can be performed. ``False`` means the SDK changed and either grew a public method or
        removed the mechanism -- either way, the caller should report rather than guess.
    """
    return hasattr(brain, VOUCHED_ATTRIBUTE)


def vouch_travelling(brain: Brain, memory_types: Iterable[MemoryType] | None = None) -> dict[str, str]:
    """
    Vouch for every locally-held vector index whose binding matches its module.

    Args:
        brain (Brain): The brain.
        memory_types (Iterable[MemoryType] | None): Restrict to these modules.

    Returns:
        dict[str, str]: Memory type to outcome -- ``vouched``, or why not. Reported rather than raised: a brain with one
        stale vector index should still be able to publish the rest, and the caller decides whether an omission matters.
    """
    if not supported(brain):
        return {
            "*": (
                "this SDK version does not expose the vouching mechanism; a vector index cannot be published without "
                "either a public Brain.vouch() or a prior pack"
            )
        }

    outcome: dict[str, str] = {}
    wanted = set(memory_types) if memory_types is not None else set(MemoryType)
    # Only installed modules. A vector index can be *registered* for a module that holds nothing yet -- the default
    # index set covers all five -- and asking the brain for an uninstalled module raises. Vouching for a module that
    # does not exist is not a thing to attempt, let alone to fail on.
    installed = set(brain.snapshot().installed)

    for memory_type in sorted(wanted & installed, key=lambda item: item.value):
        indices = [index for index in brain.indices.get(memory_type, []) if index.kind is IndexKind.VECTOR]
        if not indices:
            continue

        module = brain.module(memory_type)
        vouched: set[MemoryType] = getattr(brain, VOUCHED_ATTRIBUTE)

        for index in indices:
            # The SDK's write path builds a non-rebuildable index on every commit but never records *which* composition
            # it built from -- `bind` is vitruvio's, and `build` has no root to bind to. So an index with vectors and no
            # binding was just built from this module by that path, and binding it is recording a fact rather than
            # asserting one. Refusing instead would leave every post-commit publish without its vector index.
            if getattr(index, "population", 0) and getattr(index, "bound_root", None) is None:
                binder = getattr(index, "bind", None)
                if callable(binder):
                    binder(str(module.root))

            refusal = _refusal(index, str(module.root))
            if refusal is None:
                # The SDK's own vouching path. Free in practice: every vector returns from the embedding cache, so this
                # is bookkeeping rather than re-embedding.
                brain._build(module, [index])
                # `_build` vouches for *any* non-rebuildable index it feeds, whatever the index turned out to hold. So
                # the result is re-checked: whether an index comes out empty is only knowable after building it.
                refusal = _refusal(index, str(module.root))

            if refusal is None:
                outcome[memory_type.value] = "vouched"
                continue

            # **Un-vouch**, do not merely report. The SDK's write path already vouches on every ``register`` and
            # ``commit``, so a module whose blocks project to nothing embeddable arrives here *already* vouched for an
            # empty index -- and ``pack()`` then raises when it tries to dump one. Reporting without discarding left the
            # publish broken and the message accurate, which is the worst combination.
            vouched.discard(memory_type)
            outcome[memory_type.value] = refusal

    return outcome


def _refusal(index: object, root: str) -> str | None:
    """
    Why an index must not be published, or ``None`` when it may be.

    One place, so the pre-build check and the post-build re-check cannot disagree about what publishable means.

    Args:
        index (object): The vector index.
        root (str): The module's current Merkle root.

    Returns:
        str | None: The reason, or ``None``.
    """
    if not getattr(index, "population", 0):
        # The reason this check exists at all: vouching for an empty index is how an artifact comes to claim a vector
        # index and carry none, which a consumer cannot detect.
        return "the module's blocks project to nothing embeddable, so there is no vector index to publish"

    held = getattr(index, "bound_root", None)
    if held is None:
        return "the index does not record which composition it was built against"
    if held != root:
        return (
            f"the index was built against {held[:19]}... and the module is at {root[:19]}...; "
            f"run `vitruvio index build`"
        )
    return None
