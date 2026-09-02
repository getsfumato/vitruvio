"""Project selected module identities into auditable browse rows.

The projection is intentionally below the operations layer. Browsing and catalog navigation both need the same
answer for a canonical source, but making one operations object call another couples two public workflows and
turns a small catalog directory into a full-module browse. This seam accepts the identities already selected by
the caller, so its provenance and authorship work stays proportional to that selection.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.brain import Brain

from vitruvio.runtime import browse
from vitruvio.runtime.authorship import AuthorshipAudit
from vitruvio.runtime.provenance import ProvenanceReader, registration_origins


def project_rows(
    brain: Brain,
    kind: MemoryType,
    identities: Sequence[Any],
    *,
    policy: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Read only the selected identities and attach the evidence needed to judge their creator.

    A provenance row is itself evidence, not a subject whose creation is recursively attributed. Marking that
    distinction explicitly prevents a UI from presenting the absence of recursive provenance as an incomplete
    lookup. All other modules receive the same stable authorship shape, including unreadable blocks.
    """
    module = brain.module(kind)
    resolvable = module.resolvable()
    if kind is MemoryType.PROVENANCE:
        return [
            _entry(module, kind, identity, resolvable=resolvable, origins={}, authorship=None)
            for identity in identities
        ], None

    subjects = {str(identity) for identity in identities}
    if not subjects:
        return [], None
    read = ProvenanceReader(brain).by_subjects(subjects, read_limit=max(1, len(subjects) * 32))
    origins = registration_origins(read)
    audit = AuthorshipAudit(brain, policy=policy)
    claims = audit.claims(read)
    return (
        [
            _entry(
                module,
                kind,
                identity,
                resolvable=resolvable,
                origins=origins,
                authorship=claims.get(str(identity), audit.empty(read)),
            )
            for identity in identities
        ],
        read.metadata(),
    )


def _entry(
    module: Any,
    kind: MemoryType,
    identity: Any,
    *,
    resolvable: dict[Any, bool],
    origins: dict[str, str],
    authorship: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep composition membership visible even when the referenced block cannot be read."""
    block_id = str(identity)
    if not resolvable.get(identity, True):
        entry = browse.unreadable(block_id, kind.value, "not resolvable (redacted or not installed)")
    else:
        try:
            entry = browse.row(module.get(identity), kind, origin=origins.get(block_id))
        except Exception as error:
            entry = browse.unreadable(block_id, kind.value, f"{type(error).__name__}: {error}")
    entry["authorship"] = (
        authorship
        if authorship is not None
        else {"applicable": False, "complete": True, "provenance": None, "claims": []}
    )
    return entry


__all__ = ["project_rows"]
