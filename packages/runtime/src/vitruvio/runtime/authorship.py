"""Join provenance, snapshot history, and SSH authentication into one audit projection.

Blocks are immutable but they are not signed one by one.  A creation claim lives in provenance, the provenance
record enters through a snapshot, and the snapshot is what detached SSH signatures authorize.  Keeping that join
here gives the CLI, TUI, and future protocol adapters one honest answer instead of three approximations.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.brain import Brain
from boltzmann.identity.digest import BlockId
from boltzmann.module.composition import Composition
from boltzmann.module.snapshot import Snapshot

from vitruvio.runtime.provenance import ProvenanceRead, decode_record

CREATION_RECORDS = frozenset({"registration", "derivation"})


class AuthorshipAudit:
    """A request-scoped audit cache over one opened brain."""

    def __init__(self, brain: Brain, *, policy: Any) -> None:
        self.brain = brain
        self.policy = policy
        self._snapshots: dict[str, Snapshot] | None = None
        self._members: dict[str, set[str] | None] = {}
        self._introduced_by: dict[str, list[str]] | None = None
        self._reports: dict[str, Any] = {}

    def claims(self, read: ProvenanceRead) -> dict[str, dict[str, Any]]:
        """Project creation records returned for a set of block subjects, grouped by subject."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for provenance_id, record in read.records:
            if record.get("record_type") not in CREATION_RECORDS:
                continue
            subject = record.get("block")
            actor = record.get("actor")
            if not isinstance(subject, str) or not isinstance(actor, dict):
                continue
            grouped.setdefault(subject, []).append(self._claim(provenance_id, record))

        return {
            block_id: {
                "complete": read.complete and all(claim["complete"] for claim in claims),
                "provenance": read.metadata(),
                "claims": claims,
            }
            for block_id, claims in grouped.items()
        }

    def empty(self, read: ProvenanceRead) -> dict[str, Any]:
        """The explicit no-claim shape, preserving whether the lookup was complete."""
        return {"complete": read.complete, "provenance": read.metadata(), "claims": []}

    def participants(self, snapshot: Snapshot) -> dict[str, Any]:
        """Actors and assisting parties introduced by one snapshot relative to its first parent."""
        members = self._introduced(snapshot)
        actors: dict[str, dict[str, Any]] = {}
        assisted: dict[str, dict[str, Any]] = {}
        gaps: list[str] = []
        for identity in sorted(members):
            parsed = BlockId.parse(identity)
            if not self.brain.store.is_resolvable(parsed):
                gaps.append(identity)
                continue
            try:
                record = decode_record(self.brain.store.get_block(parsed))
            except Exception:
                record = None
            if record is None:
                gaps.append(identity)
                continue
            actor = record.get("actor")
            if isinstance(actor, dict) and isinstance(actor.get("id"), str):
                actors[actor["id"]] = actor
            for collaborator in record.get("assisted_by") or ():
                if isinstance(collaborator, dict) and isinstance(collaborator.get("id"), str):
                    assisted[collaborator["id"]] = collaborator
        return {
            "actors": [actors[key] for key in sorted(actors)],
            "assisted_by": [assisted[key] for key in sorted(assisted)],
            "complete": not gaps,
            "evidence_gaps": gaps,
        }

    def authenticate(self, snapshot: Snapshot) -> dict[str, Any]:
        """Authenticate and verify one resolvable historical snapshot, cached by digest."""
        key = str(snapshot.digest)
        if key in self._reports:
            return self._reports[key]
        report = self.brain.authenticate(snapshot.digest, policy=self.policy)
        historical = Brain(
            self.brain.store,
            actor=self.brain.actor,
            snapshot=snapshot,
            assisted_by=self.brain.assisted_by,
            policy=self.brain.policy,
        )
        payload = report.model_dump(mode="json")
        payload["state"] = report.state.value
        payload["integrity"] = historical.verify()
        self._reports[key] = payload
        return payload

    def snapshots(self) -> dict[str, Snapshot]:
        """Every reachable and resolvable snapshot, including merged-in history."""
        if self._snapshots is not None:
            return self._snapshots
        found: dict[str, Snapshot] = {}
        reachable = self.brain.reachable_history()
        if not reachable:
            self._snapshots = found
            return found
        current = self.brain.snapshot()
        found[str(current.digest)] = current
        for digest in reachable:
            key = str(digest)
            if key in found or not self.brain.store.is_resolvable(digest):
                continue
            try:
                found[key] = Snapshot.from_document(self.brain.store.get_bytes(digest))
            except Exception:
                continue
        self._snapshots = found
        return found

    def unresolved_history(self) -> list[str]:
        """Reachable snapshot digests whose documents are not readable."""
        resolved = self.snapshots()
        return sorted(str(digest) for digest in self.brain.reachable_history() if str(digest) not in resolved)

    def _claim(self, provenance_id: str, record: dict[str, Any]) -> dict[str, Any]:
        candidates = (self._introduction_map()).get(provenance_id, [])
        snapshot = self._oldest(candidates)
        actor = record.get("actor") or {}
        actor_id = actor.get("id") if isinstance(actor, dict) else None
        result: dict[str, Any] = {
            "actor": actor,
            "assisted_by": record.get("assisted_by") or [],
            "record_type": record.get("record_type"),
            "provenance": provenance_id,
            "snapshot": str(snapshot.digest) if snapshot else None,
            "actor_verified": None,
            "snapshot_authenticity": None,
            "snapshot_authorized": None,
            "signature_subjects": [],
            "trust_root": None,
            "pinned": False,
            "complete": snapshot is not None,
        }
        if snapshot is None:
            return result

        try:
            report = self.authenticate(snapshot)
        except Exception:
            result["complete"] = False
            return result
        attribution = report.get("attribution") or {}
        if actor_id in attribution.get("verified", []):
            result["actor_verified"] = True
        elif actor_id in attribution.get("asserted", []):
            result["actor_verified"] = False
        result.update(
            snapshot_authenticity=report.get("state"),
            snapshot_authorized=report.get("state") == "authorized",
            signature_subjects=sorted(
                {
                    verdict["subject"]
                    for verdict in report.get("signatures", [])
                    if verdict.get("subject") and verdict.get("outcome") in {"valid", "valid_as_proposal"}
                }
            ),
            trust_root=report.get("trust_root"),
            pinned=bool(report.get("pinned")),
            complete=result["complete"] and not attribution.get("evidence_gaps"),
        )
        return result

    def _oldest(self, digests: Iterable[str]) -> Snapshot | None:
        snapshots = self.snapshots()
        candidates = [snapshots[digest] for digest in digests if digest in snapshots]
        return min(candidates, key=lambda item: (str(item.created_at), str(item.digest)), default=None)

    def _introduction_map(self) -> dict[str, list[str]]:
        if self._introduced_by is not None:
            return self._introduced_by
        found: dict[str, list[str]] = {}
        for digest, snapshot in self.snapshots().items():
            for identity in self._introduced(snapshot):
                found.setdefault(identity, []).append(digest)
        self._introduced_by = found
        return found

    def _introduced(self, snapshot: Snapshot) -> set[str]:
        current = self._membership(snapshot)
        if current is None:
            return set()
        if snapshot.first_parent is None:
            return current
        parent = self.snapshots().get(str(snapshot.first_parent))
        inherited = self._membership(parent) if parent is not None else None
        return current - (inherited or set())

    def _membership(self, snapshot: Snapshot | None) -> set[str] | None:
        if snapshot is None:
            return None
        key = str(snapshot.digest)
        if key in self._members:
            return self._members[key]
        reference = snapshot.modules.get(MemoryType.PROVENANCE)
        if reference is None:
            self._members[key] = set()
            return set()
        try:
            composition = Composition.from_document(self.brain.store.get_bytes(reference.composition))
        except Exception:
            self._members[key] = None
            return None
        members = {str(identity) for identity in composition.block_ids}
        self._members[key] = members
        return members


__all__ = ["CREATION_RECORDS", "AuthorshipAudit"]
