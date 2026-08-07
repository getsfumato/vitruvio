"""The on-disk envelope every vitruvio index shares.

An index file is a header followed by a msgpack body. The header exists to make three failures *loud* instead of
silent, because each of them otherwise produces wrong answers rather than errors:

* **Wrong file.** A magic string, so a truncated or unrelated file is rejected rather than parsed into an empty
  index. An empty index is the worst outcome available: it does not announce itself, so a planner consulting it
  gets no candidates and reports a confident nothing.
* **Wrong version.** A format version and a body digest. A body that does not hash to its header was written by a
  crashed process, and rebuilding is cheap for every structural index.
* **Wrong composition.** The Merkle root and leaf fingerprint the index was built against. An index whose binding
  does not match the module is stale, and stale means rebuild -- not "use anyway".

Writes are atomic: a temporary file in the same directory, then a rename. A half-written index that still parses
would be indistinguishable from a complete one.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import msgspec
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from pathlib import Path

MAGIC = b"VITRUVIO-IDX\x00"
"""Leading bytes of every index file. Fixed length, so the header can be located without parsing."""

FORMAT_VERSION = 1
"""The envelope's version. Independent of any index's own body version."""

SUFFIX = ".vidx"
"""Extension for a persisted index."""


class IndexFormatError(Exception):
    """A file is not a vitruvio index, or is not one this build can read."""


class IndexStaleError(Exception):
    """A file is a valid index, but was built against a different composition."""


class Header(BaseModel):
    """
    What a persisted index says about itself.

    Attributes:
        format_version (int): The envelope's version.
        kind (str): Which of the six index kinds.
        memory_type (str): Which module it indexes.
        body_version (int): The index's own body schema version, independent of the envelope.
        body_sha256 (str): Digest of the body bytes, so a truncated write is detectable.
        merkle_root (str | None): The module root this was built against.
        leaf_fingerprint (str): Fingerprint of the identities indexed. Catches a redaction, which leaves the
            root unchanged.
        population (int): How many blocks are represented. **Required rather than optional**: an empty index is
            silently wrong, so every index must be able to say it holds nothing.
        engine (str): Which implementation wrote the body, e.g. ``pyroaring`` or ``python-set``.
        analyzer_id (str | None): The text analyzer, when one was involved. Unicode tables move between Python
            releases, so this is what turns a subtle scoring change into a detectable rebuild.
        projection_id (str | None): The field-extraction policy.
        chunker_id (str | None): The chunking policy. Different chunks are different embedded strings.
        model_tag (str | None): The embedding model, for a vector index.
        built_at (str): RFC3339.
        extra (dict[str, Any]): Per-kind parameters worth reporting, e.g. HNSW connectivity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    format_version: int = FORMAT_VERSION
    kind: str
    memory_type: str
    body_version: int = 1
    body_sha256: str = ""
    merkle_root: str | None = None
    leaf_fingerprint: str = ""
    population: int = 0
    engine: str = "python"
    analyzer_id: str | None = None
    projection_id: str | None = None
    chunker_id: str | None = None
    model_tag: str | None = None
    built_at: str = ""
    extra: dict[str, Any] = {}


def encode(header: Header, body: dict[str, Any]) -> bytes:
    """
    Serialize a header and body into the file's bytes.

    The body digest is computed over the encoded body and written into the header, so the two cannot disagree
    after the fact.

    Args:
        header (Header): What to record about the index.
        body (dict[str, Any]): The index's own state.

    Returns:
        bytes: ``MAGIC | uint32 header length | header | body``.
    """
    body_bytes = msgspec.msgpack.encode(body)
    stamped = header.model_copy(update={"body_sha256": hashlib.sha256(body_bytes).hexdigest()})
    header_bytes = msgspec.msgpack.encode(stamped.model_dump(mode="json"))
    return MAGIC + len(header_bytes).to_bytes(4, "big") + header_bytes + body_bytes


def decode(data: bytes) -> tuple[Header, dict[str, Any]]:
    """
    Parse an index file.

    Args:
        data (bytes): The file's contents.

    Returns:
        tuple[Header, dict[str, Any]]: The header and body.

    Raises:
        IndexFormatError: If the magic, the length prefix, the version or the body digest does not check out.
            Every one of these is reported rather than tolerated: a structural index is cheap to rebuild, and
            reading a damaged one produces wrong answers instead of errors.
    """
    if not data.startswith(MAGIC):
        raise IndexFormatError("not a vitruvio index file (bad magic)")

    offset = len(MAGIC)
    if len(data) < offset + 4:
        raise IndexFormatError("truncated index file (no header length)")
    header_length = int.from_bytes(data[offset : offset + 4], "big")
    offset += 4

    if len(data) < offset + header_length:
        raise IndexFormatError("truncated index file (header shorter than declared)")
    try:
        header = Header.model_validate(msgspec.msgpack.decode(data[offset : offset + header_length]))
    except Exception as error:
        raise IndexFormatError(f"unreadable index header: {error}") from error
    offset += header_length

    if header.format_version != FORMAT_VERSION:
        raise IndexFormatError(
            f"index format version {header.format_version} cannot be read by this build (expected {FORMAT_VERSION})"
        )

    body_bytes = data[offset:]
    if header.body_sha256 and hashlib.sha256(body_bytes).hexdigest() != header.body_sha256:
        raise IndexFormatError("index body does not match its digest; the write did not complete")

    try:
        body = msgspec.msgpack.decode(body_bytes)
    except Exception as error:
        raise IndexFormatError(f"unreadable index body: {error}") from error
    if not isinstance(body, dict):
        raise IndexFormatError("index body is not a mapping")
    return header, body


def write(path: Path, header: Header, body: dict[str, Any]) -> Path:
    """
    Write an index atomically.

    Args:
        path (Path): Destination. Parent directories are created.
        header (Header): The header.
        body (dict[str, Any]): The body.

    Returns:
        Path: The file written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(encode(header, body))
    temporary.replace(path)
    return path


def read(path: Path) -> tuple[Header, dict[str, Any]] | None:
    """
    Read an index, returning ``None`` when there is nothing readable there.

    Absence and damage are deliberately the same outcome for the caller, because the response to both is
    identical: rebuild. What must not happen is a damaged file being read as an empty index.

    Args:
        path (Path): The file.

    Returns:
        tuple[Header, dict[str, Any]] | None: The header and body, or ``None``.

    Raises:
        IndexFormatError: If the file exists and is unreadable. Callers that prefer to rebuild catch this; the
            error is raised rather than swallowed so that a corrupt index is never mistaken for an empty one.
    """
    if not path.is_file():
        return None
    return decode(path.read_bytes())
