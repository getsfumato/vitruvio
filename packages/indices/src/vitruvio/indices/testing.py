"""Test doubles that ship, rather than living in a test directory.

``MemoryContent`` is here and not in a ``conftest`` because anything implementing an index against this protocol
needs a ``ContentReader`` stub -- the SDK's own reader is a block store, and standing one up to assert something
about tokenisation is more setup than the assertion is worth. Shipping it also means the index tests, the planner
tests and a downstream implementation all use the *same* stub, so a behaviour one of them relies on cannot quietly
differ from another's.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from boltzmann.identity.digest import BlockId, Digest, OciDigest


class MemoryContent:
    """
    A ``ContentReader`` over a dictionary.

    Raises for an absent blob rather than returning empty bytes, because that is what a store does and because
    "the view could not be read" and "the view is empty" must stay distinguishable.

    Attributes:
        blobs (dict[str, bytes]): Digest string to bytes.
    """

    def __init__(self, blobs: dict[str, bytes] | None = None) -> None:
        """
        Build a reader.

        Args:
            blobs (dict[str, bytes] | None): Pre-loaded content, keyed by digest string.
        """
        self.blobs: dict[str, bytes] = dict(blobs or {})

    def add(self, data: bytes) -> OciDigest:
        """
        Store bytes under their own digest.

        Args:
            data (bytes): The content.

        Returns:
            OciDigest: What it is addressed by.
        """
        digest = OciDigest.of(data)
        self.blobs[str(digest)] = data
        return digest

    def get_bytes(self, digest: Digest) -> bytes:
        """
        Read content back.

        Typed as the base ``Digest`` rather than ``OciDigest`` because that is what the ``ContentReader`` Protocol
        declares. A parameter type may widen in an implementation and never narrow, and narrowing here would make
        this stub silently fail to satisfy the Protocol it exists to stand in for.

        Args:
            digest (Digest): What to read.

        Returns:
            bytes: The content.

        Raises:
            FileNotFoundError: If the blob is absent, as a store would.
        """
        try:
            return self.blobs[str(digest)]
        except KeyError as error:
            raise FileNotFoundError(str(digest)) from error


def blob_id(text: str) -> OciDigest:
    """
    A stable blob digest derived from a label.

    Args:
        text (str): Any label.

    Returns:
        OciDigest: The digest, identical on every run and every machine.
    """
    return OciDigest.from_raw(hashlib.sha256(text.encode("utf-8")).digest())


def block_id(text: str) -> BlockId:
    """
    A stable *block* identity derived from a label.

    Deliberately not interchangeable with :func:`blob_id`. The protocol keeps block identity, Merkle root and OCI
    digest as three distinct types precisely so a blob digest cannot stand in for a block, and a helper that
    blurred them would let a test assert something the protocol forbids.

    Args:
        text (str): Any label.

    Returns:
        BlockId: The identity.
    """
    return BlockId.from_raw(hashlib.sha256(text.encode("utf-8")).digest())


def content_over(*texts: str) -> MemoryContent:
    """
    A reader pre-loaded with some text blobs.

    Args:
        *texts: Content to store.

    Returns:
        MemoryContent: The reader.
    """
    reader = MemoryContent()
    for text in texts:
        reader.add(text.encode("utf-8"))
    return reader


def digests_of(reader: MemoryContent) -> Iterable[str]:
    """Every digest the reader holds, for asserting what a projection asked for."""
    return sorted(reader.blobs)
