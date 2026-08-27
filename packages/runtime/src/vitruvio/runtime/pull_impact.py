"""One honest model for the composition membership a pull may or did replace."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.brain import Brain, Snapshot
from boltzmann.identity.digest import OciDigest
from boltzmann.module import Composition


class ImpactCertainty(StrEnum):
    """How strongly a pull-impact count is supported."""

    EXACT = "exact"
    APPROXIMATE = "approximate"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CompositionMembers:
    """The membership that could be decoded from one snapshot."""

    block_ids: frozenset[str]
    unreadable: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PullImpact:
    """Blocks in one composition that are absent from its replacement."""

    certainty: ImpactCertainty
    blocks: int | None
    block_ids: tuple[str, ...]
    unreadable: tuple[str, ...]
    basis: str

    def as_dict(self, *, limit: int = 20) -> dict[str, Any]:
        """Serialize the stable runtime shape while bounding identity detail."""
        return {
            "certainty": self.certainty.value,
            "blocks": self.blocks,
            "block_ids": list(self.block_ids[:limit]),
            "unreadable": list(self.unreadable),
            "basis": self.basis,
        }


def read_snapshot(brain: Brain, digest: str) -> Snapshot | None:
    """Read one retained snapshot, returning ``None`` when it cannot be decoded."""
    try:
        document = brain.store.get_bytes(OciDigest.parse(digest))
        return Snapshot.model_validate_json(document)
    except Exception:  # a pruned or corrupt historical view makes impact unknown, not the pull itself invalid
        return None


def composition_members(
    brain: Brain,
    snapshot: Snapshot,
    modules: Iterable[MemoryType] | None = None,
) -> CompositionMembers:
    """Decode membership from a snapshot, recording every module that could not be read."""
    selected = set(modules) if modules is not None else set(snapshot.installed)
    found: set[str] = set()
    unreadable: list[str] = []
    for kind in sorted(selected, key=lambda item: item.value):
        reference = snapshot.modules.get(kind)
        if reference is None:
            continue
        try:
            composition = Composition.from_document(brain.store.get_bytes(reference.composition))
        except Exception as error:
            unreadable.append(f"{kind.value} ({type(error).__name__})")
            continue
        if composition.root != reference.root:
            unreadable.append(f"{kind.value} (root mismatch)")
            continue
        found.update(str(identity) for identity in composition.block_ids)
    return CompositionMembers(frozenset(found), tuple(unreadable))


def compare_members(
    before: CompositionMembers,
    after: CompositionMembers,
    *,
    planned: bool,
) -> PullImpact:
    """Apply the same set-difference semantics to planned and completed pulls."""
    lost = tuple(sorted(before.block_ids - after.block_ids))
    unreadable = tuple(sorted(set(before.unreadable) | set(after.unreadable)))
    if unreadable:
        certainty = ImpactCertainty.UNKNOWN
        blocks = None
    elif planned:
        certainty = ImpactCertainty.APPROXIMATE
        blocks = len(lost)
    else:
        certainty = ImpactCertainty.EXACT
        blocks = len(lost)
    return PullImpact(
        certainty=certainty,
        blocks=blocks,
        block_ids=lost,
        unreadable=unreadable,
        basis="local-changes-since-pull" if planned else "before-and-after-pull",
    )


__all__ = [
    "CompositionMembers",
    "ImpactCertainty",
    "PullImpact",
    "compare_members",
    "composition_members",
    "read_snapshot",
]
