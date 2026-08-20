"""What a block looks like *in a list*, and what it points at.

Reading rather than retrieval, and the distinction is the reason this is separate from `ops/retrieval.py`: nothing
here ranks. A browse answers "what is in this module" and "what does this block cite", which are questions about
the composition, not about a query -- so no planner runs and no embedder is constructed.

Row construction itself lives in :mod:`vitruvio.runtime.browse`; what is here is the operations that call it.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.brain import Brain

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import memory_type as coerce_memory_type
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession


class BrowsingOps:
    """Browsing, as operations."""

    def __init__(self, session: BrainSession) -> None:
        """
        Args:
            session (BrainSession): The shared session.
        """
        self.session = session

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration, read through the session that owns it."""
        return self.session.config

    # Reading a brain rather than querying it. `search` ranks and `explain` justifies; these three answer "what
    # is in here", which the planner has no opinion about and which no index is consulted for. They exist in the
    # service rather than in the TUI because the same three questions are what an MCP `brain/list` tool and an
    # HTTP `GET /module/{kind}` will ask, and one answer shared is one answer to keep correct.

    def blocks(
        self,
        memory_type: str,
        *,
        limit: int = 100,
        offset: int = 0,
        contains: str | None = None,
    ) -> dict[str, Any]:
        """
        One module's blocks, as rows, in the module's own order.

        Resolution is per block and failure is per block: a version that names a block whose bytes are gone --
        tombstoned under an erasure policy, or never installed by a selective pull -- still lists it, marked
        unreadable. Dropping those rows would make a redacted brain look like a smaller one.

        ``contains`` filters the rows after they are read. That is a filter and not a query: it names no index,
        cannot rank, and is bounded by ``limit`` the same way an unfiltered page is. Text retrieval is
        :meth:`search`, where there is a cost model behind the choice.

        Args:
            memory_type (str): Which module.
            limit (int): How many rows to return.
            offset (int): How many matching rows to skip.
            contains (str | None): Case-insensitive substring the row must contain.

        Returns:
            dict[str, Any]: The module's shape, the rows, and whether more remain.
        """
        from vitruvio.runtime import browse

        brain = self.session.brain(Capability.INSPECT)
        with translated():
            kind = coerce_memory_type(memory_type)
            if kind not in brain.snapshot().installed:
                # Not an error. A module absent from a selectively pulled brain is a permanent, legitimate state,
                # and browsing one has to answer "nothing is here" rather than fail -- an interface that raised
                # would make a partial install look like a broken brain, which is the one confusion the protocol
                # is explicit about avoiding.
                return {
                    "memory_type": kind.value,
                    "root": None,
                    "block_count": 0,
                    "matched": 0,
                    "offset": offset,
                    "limit": limit,
                    "rows": [],
                    "truncated": False,
                    "filter": contains,
                    "installed": False,
                }

            module = brain.module(kind)
            identities = module.block_ids
            resolvable = module.resolvable()
            # A canonical block is bytes plus a media type and holds no name -- deliberately, because its identity
            # must not depend on what anyone called the file. The name a reader recognises lives in the
            # registration record, so it is read from provenance and attached here rather than invented. One scan
            # of an append-only module, and only for the one memory type that needs it.
            origins = self._origins(brain) if kind is MemoryType.CANONICAL else {}

            rows: list[dict[str, Any]] = []
            seen = 0
            for identity in identities:
                if resolvable.get(identity, True):
                    try:
                        entry = browse.row(module.get(identity), kind, origin=origins.get(str(identity)))
                    except Exception as error:  # the store disagreed with the composition; say so, do not stop
                        entry = browse.unreadable(str(identity), kind.value, f"{type(error).__name__}: {error}")
                else:
                    entry = browse.unreadable(str(identity), kind.value, "not resolvable (redacted or not installed)")
                if contains and not browse.matches(entry, contains):
                    continue
                seen += 1
                if seen > offset and len(rows) < limit:
                    rows.append(entry)

            return {
                "memory_type": kind.value,
                "root": str(module.root),
                "block_count": len(identities),
                "matched": seen,
                "offset": offset,
                "limit": limit,
                "rows": rows,
                "truncated": seen > offset + len(rows),
                "filter": contains,
                "installed": True,
            }

    @staticmethod
    def _origins(brain: Brain) -> dict[str, str]:
        """
        Where each canonical block came from, according to its registration record.

        Args:
            brain (Brain): The opened brain.

        Returns:
            dict[str, str]: Block identity to origin. Empty when provenance is not installed -- a selectively
            pulled brain can hold canonical evidence with no provenance beside it, and that is a brain whose
            blocks are shown by media type rather than one that fails to list.
        """
        found: dict[str, str] = {}
        with suppress(Exception):
            module = brain.module(MemoryType.PROVENANCE)
            resolvable = module.resolvable()
            for identity in module.block_ids:
                if not resolvable.get(identity, True):
                    continue
                record = module.get(identity).payload().get("record")
                if not isinstance(record, dict) or record.get("record_type") != "registration":
                    continue
                block, origin = record.get("block"), record.get("origin")
                if isinstance(block, str) and isinstance(origin, str) and origin:
                    found[block] = origin
        return found

    def content(self, digest: str) -> bytes:
        """
        The bytes a block names, verified against the digest on the way out of the store.

        The one method here that does not return a dictionary, and the reason is that a preview needs *bytes*: a
        PDF page to rasterize, an image to draw, a transcript to display. Base64 inside an envelope would be a
        different operation with a different cost, and a caller that wants that is calling
        :meth:`export_content` and reading the file.

        Content is not evidence. A canonical block names the original it describes and the normalized view of
        it, and both are addressed here by the digest the block carries -- so what comes back is what the block
        says it is, or nothing.

        Args:
            digest (str): A ``sha256:...`` content address, as carried by ``blob`` or ``normalized_view.blob``.

        Returns:
            bytes: The content.

        Raises:
            VitruvioError: If the digest is malformed, or the store cannot produce those bytes.
        """
        from boltzmann.identity.digest import OciDigest

        brain = self.session.brain(Capability.INSPECT)
        with translated():
            return brain.store.get_bytes(OciDigest.parse(digest))

    def export_content(self, digest: str, destination: Path) -> dict[str, Any]:
        """
        Write the bytes a block names to a file.

        For everything a terminal cannot draw: a video to hand to a player, a spreadsheet to open, an original
        PDF to keep. The brain stays the authority -- this is a copy out, not a move, and nothing about the
        block changes.

        Args:
            digest (str): The content address.
            destination (Path): Where to write. A directory is written into, under the digest's hex.

        Returns:
            dict[str, Any]: The digest, the path written, and how many bytes it holds.
        """
        data = self.content(digest)
        target = destination
        if destination.is_dir():
            target = destination / digest.replace(":", "-")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"digest": digest, "path": str(target), "size": len(data)}

    def related(self, block_id: str, *, limit: int = 50) -> dict[str, Any]:
        """
        The provenance records that name a block: how it got here, and what was done to it since.

        This is the brain's own link graph rather than a similarity neighbourhood. Registration, derivation,
        normalization, supersession, demotion and removal all land in provenance as records naming a block, so
        reading them back is how a reader answers "where did this come from" without trusting a summary of it.

        The provenance module is scanned. There is no index from block to the records about it -- provenance is
        append-only and small relative to canonical evidence -- and a scan that is honest about its cost is
        better than an index that has to be kept true.

        Args:
            block_id (str): The block to look up.
            limit (int): How many records to return.

        Returns:
            dict[str, Any]: The records naming it, most recently written last, and how many were found. Empty
            when provenance is not installed, which is a brain whose history was not pulled rather than a
            failure to read one.
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            kind = coerce_memory_type("provenance")
            if kind not in brain.snapshot().installed:
                return {"block": block_id, "records": [], "count": 0, "truncated": False}
            module = brain.module(kind)
            resolvable = module.resolvable()
            found: list[dict[str, Any]] = []
            for identity in module.block_ids:
                if not resolvable.get(identity, True):
                    continue
                payload = module.get(identity).payload()
                record = payload.get("record")
                if not isinstance(record, dict) or block_id not in mentions(record):
                    continue
                found.append({"block_id": str(identity), "record": record})
            return {
                "block": block_id,
                "records": found[:limit],
                "count": len(found),
                "truncated": len(found) > limit,
            }


def mentions(record: dict[str, Any]) -> set[str]:
    """
    Every block identity a provenance record names, at any depth.

    Walked rather than read field by field because the six record types name blocks under six different keys --
    ``block``, ``derived_from``, ``supersedes``, ``removed`` -- and a new record type would otherwise be a
    record whose links silently stop appearing. What is collected is anything that looks like an identity, which
    is a decision to over-report rather than to miss an edge.

    Args:
        record (dict[str, Any]): The record's payload.

    Returns:
        set[str]: The identities it mentions.
    """
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
