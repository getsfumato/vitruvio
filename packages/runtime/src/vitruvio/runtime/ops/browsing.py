"""What a block looks like *in a list*, and what it points at.

Reading rather than retrieval, and the distinction is the reason this is separate from `ops/retrieval.py`: nothing
here ranks. A browse answers "what is in this module" and "what does this block cite", which are questions about
the composition, not about a query -- so no planner runs and no embedder is constructed.

Row construction itself lives in :mod:`vitruvio.runtime.browse`; what is here is the operations that call it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from boltzmann.blocks.memory_type import MemoryType

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import memory_type as coerce_memory_type
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.provenance import ProvenanceRead, ProvenanceReader, registration_origins
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

        brain = self.session.brain(Capability.BROWSE)
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
                    "provenance": None,
                }

            module = brain.module(kind)
            identities = module.block_ids
            resolvable = module.resolvable()
            origins: dict[str, str] = {}
            authorship: dict[str, dict[str, Any]] = {}
            provenance: dict[str, Any] | None = None
            provenance_read = None
            if kind is not MemoryType.PROVENANCE:
                # Only records about identities this operation may render are requested. An ordinary page therefore
                # performs work proportional to the page, while the explicitly scanning `contains` path requests
                # the identities it already has to inspect.
                targets = identities if contains is not None else identities[offset : offset + limit]
                provenance_read = ProvenanceReader(brain).by_subjects(
                    {str(identity) for identity in targets},
                    read_limit=max(1, len(targets) * 32),
                )
                origins = registration_origins(provenance_read)
                provenance = provenance_read.metadata()
                from vitruvio.runtime.authorship import AuthorshipAudit

                authorship = AuthorshipAudit(brain, policy=self.config.project.authenticity.build()).claims(
                    provenance_read
                )

            rows: list[dict[str, Any]] = []
            if contains is None:
                # Only the page is read. Without a filter every row matches, so `matched` is the module's own count
                # and there is nothing to learn from the rest -- while the walk below resolves a block per identity,
                # which on a large module meant tens of thousands of store reads to return a hundred rows.
                seen = len(identities)
                rows = [
                    self._entry(
                        module,
                        kind,
                        identity,
                        resolvable,
                        origins,
                        authorship=authorship,
                        provenance_read=provenance_read,
                    )
                    for identity in identities[offset : offset + limit]
                ]
            else:
                # With a filter the scan is the answer: `matched` is how many rows match in the whole module, and
                # that is not knowable from a page. The cost is stated in this method's own docstring -- a filter is
                # not a query, and `search` is where an index decides what to read.
                seen = 0
                for identity in identities:
                    entry = self._entry(
                        module,
                        kind,
                        identity,
                        resolvable,
                        origins,
                        authorship=authorship,
                        provenance_read=provenance_read,
                    )
                    if not browse.matches(entry, contains):
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
                "provenance": provenance,
            }

    @staticmethod
    def _entry(
        module: Any,
        kind: MemoryType,
        identity: Any,
        resolvable: Mapping[Any, bool],
        origins: Mapping[str, str],
        *,
        authorship: Mapping[str, dict[str, Any]],
        provenance_read: ProvenanceRead | None,
    ) -> dict[str, Any]:
        """
        One row, whether or not the block behind it can be read.

        Failure is per block on purpose: a version naming a block whose bytes are gone -- tombstoned under an
        erasure policy, or never installed by a selective pull -- still lists, marked unreadable. Dropping those
        rows would make a redacted brain look like a smaller one.

        Args:
            module (Module): The module being listed.
            kind (MemoryType): Which module, for the row's own label.
            identity (BlockId): The block.
            resolvable (Mapping[Any, bool]): The module's resolvability map, read once by the caller.
            origins (Mapping[str, str]): Block identity to origin, empty for every module but canonical.

        Returns:
            dict[str, Any]: The row.
        """
        from vitruvio.runtime import browse

        block_id = str(identity)
        if not resolvable.get(identity, True):
            entry = browse.unreadable(block_id, kind.value, "not resolvable (redacted or not installed)")
        else:
            try:
                entry = browse.row(module.get(identity), kind, origin=origins.get(block_id))
            except Exception as error:  # the store disagreed with the composition; say so, do not stop
                entry = browse.unreadable(block_id, kind.value, f"{type(error).__name__}: {error}")
        if kind is not MemoryType.PROVENANCE and provenance_read is not None:
            entry["authorship"] = authorship.get(
                block_id,
                {"complete": provenance_read.complete, "provenance": provenance_read.metadata(), "claims": []},
            )
        return entry

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

    def export_content(self, digest: str, destination: Path, *, overwrite: bool = True) -> dict[str, Any]:
        """
        Write the bytes a block names to a file.

        For everything a terminal cannot draw: a video to hand to a player, a spreadsheet to open, an original
        PDF to keep. The brain stays the authority -- this is a copy out, not a move, and nothing about the
        block changes.

        Args:
            digest (str): The content address.
            destination (Path): Where to write. A directory is written into, under the digest's hex.
            overwrite (bool): Whether an existing target may be replaced. Defaults to ``True`` for explicit
                command-line exports; callers that derive a destination should disable it.

        Returns:
            dict[str, Any]: The digest, the path written, and how many bytes it holds.
        """
        data = self.content(digest)
        target = destination
        if destination.is_dir():
            target = destination / digest.replace(":", "-")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb" if overwrite else "xb") as exported:
            exported.write(data)
        return {"digest": digest, "path": str(target), "size": len(data)}

    def related(self, block_id: str, *, limit: int = 50) -> dict[str, Any]:
        """
        The provenance records that name a block: how it got here, and what was done to it since.

        This is the brain's own link graph rather than a similarity neighbourhood. Registration, derivation,
        normalization, supersession, demotion and removal all land in provenance as records naming a block, so
        reading them back is how a reader answers "where did this come from" without trusting a summary of it.

        Uses the provenance subject index when it is ready. Without one, the fallback scan is capped and the result
        says it is incomplete rather than turning one lookup into an unbounded walk.

        Args:
            block_id (str): The block to look up.
            limit (int): How many records to return.

        Returns:
            dict[str, Any]: The records naming it, most recently written last, and how many were found. Empty
            when provenance is not installed, which is a brain whose history was not pulled rather than a
            failure to read one.
        """
        brain = self.session.brain(Capability.BROWSE)
        with translated():
            kind = coerce_memory_type("provenance")
            if kind not in brain.snapshot().installed:
                return {
                    "block": block_id,
                    "records": [],
                    "count": 0,
                    "count_exact": False,
                    "truncated": False,
                    "provenance": {"state": "absent", "complete": False, "scanned": 0, "unreadable": 0},
                }
            read = ProvenanceReader(brain).by_subjects({block_id}, read_limit=max(limit + 1, 1))
            found = [{"block_id": identity, "record": record} for identity, record in read.records]
            return {
                "block": block_id,
                "records": found[:limit],
                "count": len(found),
                "count_exact": read.complete,
                "truncated": len(found) > limit or not read.complete,
                "provenance": read.metadata(),
            }
