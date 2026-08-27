"""Bounded provenance lookup, decoding, and honest degradation for every runtime consumer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.brain import Brain

PROVENANCE_SCAN_LIMIT = 512
"""Maximum records read when no usable subject index exists."""


def mentions(record: dict[str, Any]) -> set[str]:
    """Return every block identity a provenance record names, at any depth."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if value.startswith("sha256:"):
                found.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(record)
    return found


def decode_record(block: Any) -> dict[str, Any] | None:
    """Decode the record from one provenance block under the shared runtime rules."""
    try:
        payload = block.payload()
    except Exception:
        return None
    record = payload.get("record") if isinstance(payload, dict) else None
    return record if isinstance(record, dict) else None


@dataclass(frozen=True, slots=True)
class ProvenanceRead:
    """Decoded records plus whether the lookup can honestly claim completeness."""

    records: tuple[tuple[str, dict[str, Any]], ...]
    state: Literal["absent", "unreadable", "indexed", "bounded"]
    complete: bool
    scanned: int
    unreadable: int

    def metadata(self) -> dict[str, Any]:
        """The stable degradation shape returned by browse operations."""
        return {
            "state": self.state,
            "complete": self.complete,
            "scanned": self.scanned,
            "unreadable": self.unreadable,
        }


class ProvenanceReader:
    """One indexed-first, bounded-fallback reader over the provenance module."""

    def __init__(self, brain: Brain) -> None:
        self.brain = brain

    def by_subjects(self, subjects: set[str], *, read_limit: int) -> ProvenanceRead:
        """Read records mentioning any requested block, without an unbounded provenance walk."""
        module, unavailable = self._module()
        if module is None:
            return ProvenanceRead((), unavailable, False, 0, 0)

        index = self._subject_index()
        if index is not None:
            from vitruvio.indices import IdentityKey, IdQuery

            identities: set[str] = set()
            for subject in subjects:
                identities.update(
                    str(identity)
                    for identity in index.lookup(IdQuery(keys=((IdentityKey.RECORD_SUBJECT, subject),))).identities()
                )
            ordered = sorted(identities)
            selected = ordered[:read_limit]
            records, unreadable = self._decode(module, selected, subjects)
            return ProvenanceRead(
                tuple(records),
                "indexed",
                len(selected) == len(ordered) and unreadable == 0,
                len(selected),
                unreadable,
            )

        available = list(module.block_ids)
        selected = available[: min(read_limit, PROVENANCE_SCAN_LIMIT)]
        records, unreadable = self._decode(module, selected, subjects)
        return ProvenanceRead(
            tuple(records),
            "bounded",
            len(selected) == len(available) and unreadable == 0,
            len(selected),
            unreadable,
        )

    def registrations_by_origin(self, origin: str, *, read_limit: int = 8) -> ProvenanceRead:
        """Read registration records for an exact origin through the same decoding seam."""
        module, unavailable = self._module()
        if module is None:
            return ProvenanceRead((), unavailable, False, 0, 0)
        index = self._origin_index()
        if index is None:
            return ProvenanceRead((), "bounded", False, 0, 0)

        from vitruvio.indices import IdentityKey, IdQuery, fold

        identities = list(index.lookup(IdQuery(keys=((IdentityKey.ORIGIN, fold(origin)),))).identities())
        selected = identities[:read_limit]
        records, unreadable = self._decode(module, selected, None)
        registrations = [item for item in records if item[1].get("record_type") == "registration"]
        return ProvenanceRead(
            tuple(registrations),
            "indexed",
            len(selected) == len(identities) and unreadable == 0,
            len(selected),
            unreadable,
        )

    def _module(self) -> tuple[Any | None, Literal["absent", "unreadable"]]:
        if MemoryType.PROVENANCE not in self.brain.snapshot().installed:
            return None, "absent"
        try:
            return self.brain.module(MemoryType.PROVENANCE), "absent"
        except Exception:
            return None, "unreadable"

    def _subject_index(self) -> Any | None:
        return self._hash_index("record_subject")

    def _origin_index(self) -> Any | None:
        return self._hash_index("origin")

    def _hash_index(self, key: str) -> Any | None:
        from vitruvio.indices import HashMapIndex

        for candidate in self.brain.indices.get(MemoryType.PROVENANCE, []):
            if not isinstance(candidate, HashMapIndex) or not candidate.population:
                continue
            capability = candidate.capability()
            if capability.usable and key in capability.keys:
                return candidate
        return None

    @staticmethod
    def _decode(
        module: Any,
        identities: list[Any],
        subjects: set[str] | None,
    ) -> tuple[list[tuple[str, dict[str, Any]]], int]:
        from boltzmann.identity.digest import BlockId

        resolvable = module.resolvable()
        found: list[tuple[str, dict[str, Any]]] = []
        unreadable = 0
        for identity in identities:
            parsed = BlockId.parse(identity) if isinstance(identity, str) else identity
            if not resolvable.get(parsed, True):
                unreadable += 1
                continue
            try:
                record = decode_record(module.get(parsed))
            except Exception:
                unreadable += 1
                continue
            if record is None:
                unreadable += 1
                continue
            if subjects is not None and not (mentions(record) & subjects):
                continue
            found.append((str(parsed), record))
        return found, unreadable


def registration_origins(read: ProvenanceRead) -> dict[str, str]:
    """Project registration records into canonical block-to-origin mappings."""
    found: dict[str, str] = {}
    for _, record in read.records:
        if record.get("record_type") != "registration":
            continue
        block, origin = record.get("block"), record.get("origin")
        if isinstance(block, str) and isinstance(origin, str) and origin:
            found[block] = origin
    return found


__all__ = [
    "PROVENANCE_SCAN_LIMIT",
    "ProvenanceRead",
    "ProvenanceReader",
    "decode_record",
    "mentions",
    "registration_origins",
]
