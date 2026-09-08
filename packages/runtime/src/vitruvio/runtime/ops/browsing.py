"""What a block looks like *in a list*, and what it points at.

Reading rather than retrieval, and the distinction is the reason this is separate from `ops/retrieval.py`: nothing
here ranks. A browse answers "what is in this module" and "what does this block cite", which are questions about
the composition, not about a query -- so no planner runs and no embedder is constructed.

Row construction itself lives in :mod:`vitruvio.runtime.browse`; what is here is the operations that call it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vitruvio.kernel import EvidenceRefusedError, ResolvedConfig, UsageError
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import memory_type as coerce_memory_type
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.provenance import ProvenanceReader
from vitruvio.runtime.session import BrainSession

MAX_WINDOW = 8 << 20
"""The most a single window will put in an envelope. A caller reads a larger object in several."""

_CHUNK = 1 << 20


def _window(store: Any, digest: Any, *, offset: int, length: int) -> tuple[bytes, int]:
    """
    A window onto stored bytes, and the size of the whole, without ever holding the whole.

    Verification is what forces the entire blob to be read: a sha256 is not checkable from a slice of it, and
    handing back unverified bytes would make this the one read in the runtime that does not. What it does not
    force is *keeping* the blob -- the hash is folded a megabyte at a time and only the requested window is
    retained, so a one-byte window off a gigabyte costs a gigabyte of reading and a byte of memory.

    Falls back to :meth:`get_bytes` for a store that is not an OCI layout on disk, and defers to it for
    resolvability so that a missing or tombstoned digest fails exactly as it does everywhere else.

    Args:
        store (Any): The block store.
        digest (Any): The parsed content address.
        offset (int): Where the window starts.
        length (int): How long it is, at most.

    Returns:
        tuple[bytes, int]: The window, and the total size.
    """
    import hashlib

    blobs = getattr(store, "blobs_dir", None)
    if blobs is None or not store.is_resolvable(digest):
        data = store.get_bytes(digest)
        return data[offset : offset + length], len(data)

    from boltzmann.exceptions import BlockIntegrityError

    end = offset + length
    digester = hashlib.sha256()
    kept = bytearray()
    size = 0
    with (blobs / digest.hex).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digester.update(chunk)
            start, size = size, size + len(chunk)
            lower, upper = max(offset - start, 0), min(end - start, len(chunk))
            if lower < upper:
                kept += chunk[lower:upper]
    if digester.hexdigest() != digest.hex:
        raise BlockIntegrityError(
            f"stored bytes for {digest.KIND} {digest.short} do not hash to it: the store is corrupt"
        )
    return bytes(kept), size


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

            from vitruvio.runtime.block_rows import project_rows

            module = brain.module(kind)
            identities = module.block_ids

            rows: list[dict[str, Any]] = []
            if contains is None:
                # Only the page is read. Without a filter every row matches, so `matched` is the module's own count
                # and there is nothing to learn from the rest -- while the walk below resolves a block per identity,
                # which on a large module meant tens of thousands of store reads to return a hundred rows.
                seen = len(identities)
                rows, provenance = project_rows(
                    brain,
                    kind,
                    identities[offset : offset + limit],
                    policy=self.config.project.authenticity.build(),
                )
            else:
                # With a filter the scan is the answer: `matched` is how many rows match in the whole module, and
                # that is not knowable from a page. The cost is stated in this method's own docstring -- a filter is
                # not a query, and `search` is where an index decides what to read.
                candidates, provenance = project_rows(
                    brain,
                    kind,
                    identities,
                    policy=self.config.project.authenticity.build(),
                )
                seen = 0
                for entry in candidates:
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

    def content_range(self, digest: str, *, offset: int = 0, length: int | None = None) -> dict[str, Any]:
        """
        A bounded window onto the bytes a block names, as text a caller elsewhere can be handed.

        What :meth:`export_content` is for a caller on this machine, this is for one that is not. A remote
        caller has no destination path here, and it does not want a whole video in a JSON envelope either, so it
        asks for a window and is told how big the whole thing is.

        A window is capped at :data:`MAX_WINDOW`, so ``length=None`` means "to the end or to the cap, whichever
        comes first". Comparing ``offset + length`` with ``size`` says whether to ask again -- which is how a
        range read works, and the reason the operation reports the total at all.

        Args:
            digest (str): The content address.
            offset (int): Where to start, in bytes.
            length (int | None): How many bytes at most. ``None`` means to the end, within the cap.

        Returns:
            dict[str, Any]: The digest, the window's ``offset`` and ``length``, the content's total ``size``,
            and the window itself as base64 in ``content``.

        Raises:
            UsageError: The window starts before zero, or asks for more than the cap.
        """
        import base64

        from boltzmann.identity.digest import OciDigest

        if offset < 0 or (length is not None and length < 0):
            raise UsageError("a content window cannot start or end before zero")
        if length is not None and length > MAX_WINDOW:
            raise UsageError(f"a content window is at most {MAX_WINDOW} bytes", hint="read it in several windows")
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            window, size = _window(brain.store, OciDigest.parse(digest), offset=offset, length=length or MAX_WINDOW)
        return {
            "digest": digest,
            "offset": offset,
            "length": len(window),
            "size": size,
            "content": base64.b64encode(window).decode("ascii"),
        }

    def export_content(
        self, digest: str, destination: Path, *, overwrite: bool = False, within: Path | None = None
    ) -> dict[str, Any]:
        """
        Write the bytes a block names to a file on this machine.

        For everything a terminal cannot draw: a video to hand to a player, a spreadsheet to open, an original
        PDF to keep. The brain stays the authority -- this is a copy out, not a move, and nothing about the
        block changes.

        The default is to refuse an existing target rather than replace it, which is the opposite of what it was.
        A default that overwrites is correct for exactly one caller -- a person who typed ``--out`` and meant it --
        and wrong for every caller that *derives* a destination, which is the shape a bug takes: issue #19 was the
        browser exporting over a file in the working directory.

        Args:
            digest (str): The content address.
            destination (Path): Where to write. A directory is written into, under the digest's hex.
            overwrite (bool): Whether an existing target may be replaced.
            within (Path | None): A directory the destination must be inside, for a caller that did not type it.

        Returns:
            dict[str, Any]: The digest, the path written, and how many bytes it holds.

        Raises:
            EvidenceRefusedError: The target exists and may not be replaced, or is outside ``within``.
        """
        data = self.content(digest)
        target = destination / digest.replace(":", "-") if destination.is_dir() else destination
        resolved = target.expanduser().resolve()
        if within is not None and not resolved.is_relative_to(within.expanduser().resolve()):
            raise EvidenceRefusedError(f"{resolved} is outside {within}", hint="choose a destination inside it")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        try:
            # `xb` rather than a prior `exists()`: between the check and the write another caller can create the
            # file, and `write_bytes` would then truncate it under a refusal it was promised.
            with resolved.open("wb" if overwrite else "xb") as exported:
                exported.write(data)
        except FileExistsError as collision:
            raise EvidenceRefusedError(
                f"{resolved} already exists", hint="ask for a replacement explicitly"
            ) from collision
        return {"digest": digest, "path": str(resolved), "size": len(data)}

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
