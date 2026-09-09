"""Evidence on its way in: bytes, what they are, and where they came from.

The runtime used to take a ``Path`` for every one of these -- ``register``, ``replace``, ``put_content``,
``ingest_run`` -- open it itself, and, when no origin was given, record the absolute path it opened as the origin.
That last part is the sharp end. An origin is provenance: it is permanent, it is what a consumer audits, and it is
the dedup key the pull path looks up. A caller elsewhere has no meaningful path on this machine, and recording one
on its behalf writes a fact about *this* filesystem into somebody else's brain.

The SDK never wanted the path. ``Brain.register`` takes ``bytes``, and so does everything under it. The seam that
does this correctly already existed one file over: :class:`~vitruvio.ingest.sources.Item` requires an origin
because it is the dedup key, :class:`~vitruvio.ingest.sources.FetchResult` carries bytes and a media type, and
``source pull`` has never seen a caller's path. This is the same shape for the operations a person drives.

:func:`contain` is the other half. A declared source has refused symlinks, escapes, FIFOs and oversized files
since ADR-0011; manual registration refused a missing file and nothing else, which is the wrong way round -- the
path a person types is the one that has a symlink in it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from vitruvio.ingest.media import FALLBACK_MEDIA_TYPE, media_type_for
from vitruvio.kernel import EvidenceRefusedError


def contain(
    path: Path,
    *,
    root: Path | None = None,
    max_bytes: int | None = None,
    allow_symlinks: bool = False,
) -> Path:
    """
    Check that a path is a real file, inside ``root`` when there is one, and small enough to read.

    Four refusals, each for a failure that has a name:

    * **outside the root** -- a glob that followed a link out of its directory turns "ingest this folder" into
      "ingest whatever that points at", and a canonical block is content-addressed and Merkle-committed before
      anyone notices;
    * **a symlink**, before resolution, because a link inside the root pointing inside the root still means the
      same bytes get registered twice under two origins;
    * **not a regular file** -- and this one is not theoretical: ``read_bytes()`` on a FIFO blocks forever, and
      a FIFO is something a glob will happily hand you;
    * **too large**, checked against ``stat().st_size`` *before* the read rather than after, which is the
      difference between a refusal and an out-of-memory kill.

    Args:
        path (Path): The candidate.
        root (Path | None): The directory it must be inside, when one is declared.
        max_bytes (int | None): The declared ceiling, when there is one.
        allow_symlinks (bool): Permit a symlink whose target is still inside the root.

    Returns:
        Path: The resolved path, safe to read.

    Raises:
        EvidenceRefusedError: If any of the four refusals applies.
    """
    if not allow_symlinks and path.is_symlink():
        raise EvidenceRefusedError(
            f"refuses the symlink {path}",
            hint="a link registers the same bytes under a second origin; register the target directly",
        )

    resolved = path.expanduser().resolve()
    if root is not None and not resolved.is_relative_to(root):
        raise EvidenceRefusedError(
            f"refuses {resolved}, which is outside {root}",
            hint="only paths inside the declared directory may be read",
        )
    if not resolved.is_file():
        detail = "does not exist" if not resolved.exists() else "is not a regular file"
        raise EvidenceRefusedError(f"{resolved} {detail}", hint="pass the path of a file to register")

    size = resolved.stat().st_size
    if max_bytes is not None and size > max_bytes:
        raise EvidenceRefusedError(f"{resolved} is {size} bytes, over the declared max_bytes ({max_bytes})")
    return resolved


@dataclass(frozen=True, slots=True)
class Evidence:
    """
    Bytes to incorporate, and everything the protocol has to record about them.

    Attributes:
        data (bytes): The content, unchanged. What the block is addressed by.
        media_type (str): What the bytes are. Part of the block's identity, so getting it wrong now means a
            second block later rather than a correction.
        origin (str): Where it came from. **Required**, and never defaulted from a path by an operation -- see
            the module docstring. A caller that genuinely has no origin says so by passing one that says so.
        license (str | None): Under what licence it is held.
        retention_policy (str | None): Under what retention policy.
        normalize_with (str | None): The normalization pipeline to run, under the runtime's usual policy:
            ``None`` means the one suggested for the media type, ``none`` means no view at all.
    """

    data: bytes
    media_type: str
    origin: str
    license: str | None = None
    retention_policy: str | None = None
    normalize_with: str | None = None

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        origin: str,
        media_type: str | None = None,
        filename: str | None = None,
        license: str | None = None,
        retention_policy: str | None = None,
        normalize_with: str | None = None,
        max_bytes: int | None = None,
    ) -> Evidence:
        """
        Evidence a caller already holds, with no file anywhere.

        Args:
            data (bytes): The content.
            origin (str): Where it came from.
            media_type (str | None): What it is. Guessed from ``filename`` when absent, and
                ``application/octet-stream`` when there is no filename either -- a caller elsewhere often knows
                the name of what it is uploading and nothing more.
            filename (str | None): The name it had, used only to guess the media type.
            license (str | None): Under what licence.
            retention_policy (str | None): Under what retention policy.
            normalize_with (str | None): The pipeline to run.
            max_bytes (int | None): The declared ceiling.

        Returns:
            Evidence: The evidence.

        Raises:
            EvidenceRefusedError: The content is over the declared ceiling.
        """
        if max_bytes is not None and len(data) > max_bytes:
            raise EvidenceRefusedError(f"{len(data)} bytes, over the declared max_bytes ({max_bytes})")
        resolved = media_type or (media_type_for(Path(filename)) if filename else FALLBACK_MEDIA_TYPE)
        return cls(
            data=data,
            media_type=resolved,
            origin=origin,
            license=license,
            retention_policy=retention_policy,
            normalize_with=normalize_with,
        )

    @classmethod
    def from_path(
        cls,
        path: Path,
        *,
        origin: str | None = None,
        media_type: str | None = None,
        license: str | None = None,
        retention_policy: str | None = None,
        normalize_with: str | None = None,
        root: Path | None = None,
        max_bytes: int | None = None,
        allow_symlinks: bool = False,
    ) -> Evidence:
        """
        Evidence read from a file the caller can name, checked before it is read.

        Args:
            path (Path): The file.
            origin (str | None): Where it came from. Defaults to the path, which is what a person registering a
                local file means by it.
            media_type (str | None): What it is. Guessed from the name when absent.
            license (str | None): Under what licence.
            retention_policy (str | None): Under what retention policy.
            normalize_with (str | None): The pipeline to run.
            root (Path | None): A directory the file must be inside.
            max_bytes (int | None): The declared ceiling, checked before the read.
            allow_symlinks (bool): Permit a symlink.

        Returns:
            Evidence: The evidence.

        Raises:
            EvidenceRefusedError: The path is not a readable, contained, small-enough regular file.
        """
        resolved = contain(path, root=root, max_bytes=max_bytes, allow_symlinks=allow_symlinks)
        return cls(
            data=resolved.read_bytes(),
            media_type=media_type_for(resolved, media_type),
            origin=origin or str(resolved),
            license=license,
            retention_policy=retention_policy,
            normalize_with=normalize_with,
        )

    def bounded(self, max_bytes: int | None) -> Evidence:
        """
        The same evidence, refused if it is over a ceiling.

        Called by the operations rather than only by the constructors, so that evidence assembled in memory
        cannot exceed what evidence read from a file may not.

        Args:
            max_bytes (int | None): The declared ceiling.

        Returns:
            Evidence: This evidence.

        Raises:
            EvidenceRefusedError: The content is over the ceiling.
        """
        if max_bytes is not None and len(self.data) > max_bytes:
            raise EvidenceRefusedError(
                f"{self.origin} is {len(self.data)} bytes, over the declared max_bytes ({max_bytes})"
            )
        return self

    def normalized_with(self, pipeline: str | None) -> Evidence:
        """The same evidence with the pipeline the runtime resolved for its media type."""
        return replace(self, normalize_with=pipeline)


__all__ = ["Evidence", "contain"]
