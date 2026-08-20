"""Registering canonical evidence: the original bytes, and what supersedes them.

Every operation here opens at ``WRITE``, because each one commits. What they do *not* do is decide anything about
content -- the bytes arrive already chosen, and a canonical block names the original it describes rather than
interpreting it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime import wire
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession


class RegistrationOps:
    """Canonical registration, as operations."""

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

    def register(
        self,
        path: Path,
        *,
        media_type: str,
        origin: str | None = None,
        license_id: str | None = None,
        retention_policy: str | None = None,
        normalize_with: str | None = None,
    ) -> dict[str, Any]:
        """
        Register a source as canonical evidence.

        Registering does not declare the source *true*. The canonical module asserts that evidence was
        incorporated and preserved; every interpretation of it is a separate, cited block.

        Args:
            path (Path): The file to read.
            media_type (str): What the bytes are.
            origin (str | None): Where it came from.
            license_id (str | None): Under what licence it is held.
            retention_policy (str | None): Under what retention policy.
            normalize_with (str | None): A normalization pipeline to produce a deterministic view.

        Returns:
            dict[str, Any]: The block's identity, whether it was a duplicate, and the new version.
        """
        from boltzmann.ingest.register import RegistrationRequest

        brain = self.session.brain(Capability.WRITE)
        with translated():
            data = path.read_bytes()
            request = RegistrationRequest(
                media_type=media_type,
                actor=self.config.actor(),
                origin=origin or str(path),
                license=license_id,
                retention_policy=retention_policy,
                normalize_with=normalize_with,
            )
            return wire.registration(brain.register(data, request))

    def replace(
        self,
        path: Path,
        *,
        supersedes: str,
        media_type: str,
        origin: str | None = None,
        license_id: str | None = None,
        normalize_with: str | None = None,
    ) -> dict[str, Any]:
        """
        Register a newer edition of a source, and record that it supersedes the old one.

        There is no in-place edit of evidence: a new edition is a new block, and the precedence between them is
        a provenance edge rather than a field of either.

        Args:
            path (Path): The new file.
            supersedes (str): The block the new edition takes precedence over.
            media_type (str): What the bytes are.
            origin (str | None): Where it came from.
            license_id (str | None): Under what licence.
            normalize_with (str | None): A normalization pipeline.

        Returns:
            dict[str, Any]: The new block's identity and the version this produced.
        """
        from boltzmann.identity.digest import BlockId
        from boltzmann.ingest.register import RegistrationRequest

        brain = self.session.brain(Capability.WRITE)
        with translated():
            request = RegistrationRequest(
                media_type=media_type,
                actor=self.config.actor(),
                origin=origin or str(path),
                license=license_id,
                normalize_with=normalize_with,
            )
            result = brain.replace(path.read_bytes(), request, BlockId.parse(supersedes))
            return {**wire.registration(result), "supersedes": supersedes}

    def put_content(self, path: Path, *, media_type: str) -> dict[str, Any]:
        """
        Store bytes addressably without registering a canonical block.

        For content a block will *reference* -- a normalized view produced elsewhere, an image a canonical
        block points at -- rather than content that is itself evidence.

        Args:
            path (Path): The file.
            media_type (str): What the bytes are.

        Returns:
            dict[str, Any]: The content reference.
        """
        brain = self.session.brain(Capability.WRITE)
        with translated():
            reference = brain.put_content(path.read_bytes(), media_type)
            return reference.model_dump(mode="json")
