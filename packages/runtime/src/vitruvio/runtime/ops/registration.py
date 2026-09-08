"""Registering canonical evidence: the original bytes, and what supersedes them.

Every operation here opens at ``WRITE``, because each one commits. What they do *not* do is decide anything about
content -- the bytes arrive already chosen, and a canonical block names the original it describes rather than
interpreting it. That is why they take :class:`~vitruvio.ingest.evidence.Evidence` rather than a path: the caller
has already decided what the bytes are and where they came from, and whether it read them from a file, an upload
or a stream is not this layer's business. See ADR-0021.
"""

from __future__ import annotations

from typing import Any

from vitruvio.ingest.evidence import Evidence
from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime import wire
from vitruvio.runtime.coerce import pipeline as coerce_pipeline
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

    def register(self, evidence: Evidence) -> dict[str, Any]:
        """
        Register evidence as canonical.

        Registering does not declare the source *true*. The canonical module asserts that evidence was
        incorporated and preserved; every interpretation of it is a separate, cited block.

        Args:
            evidence (Evidence): The bytes, what they are, and where they came from. Its ``normalize_with``
                follows the usual policy: ``None`` applies the pipeline suggested for the media type, ``none``
                registers the bytes with no view. The view is part of the block's identity, so the same bytes
                under a different pipeline are a different block.

        Returns:
            dict[str, Any]: The block's identity, whether it was a duplicate, the new version, and the pipeline that
            ran -- ``None`` when no view was produced.
        """
        from boltzmann.ingest.register import RegistrationRequest

        bounded = evidence.bounded(self.config.project.ingest.max_bytes)
        pipeline = coerce_pipeline(bounded.normalize_with, bounded.media_type)
        with self.session.write() as brain, translated():
            request = RegistrationRequest(
                media_type=bounded.media_type,
                actor=self.config.actor(),
                origin=bounded.origin,
                license=bounded.license,
                retention_policy=bounded.retention_policy,
                normalize_with=pipeline,
            )
            return {**wire.registration(brain.register(bounded.data, request)), "pipeline": pipeline}

    def replace(self, evidence: Evidence, *, supersedes: str) -> dict[str, Any]:
        """
        Register a newer edition of a source, and record that it supersedes the old one.

        There is no in-place edit of evidence: a new edition is a new block, and the precedence between them is
        a provenance edge rather than a field of either.

        Args:
            evidence (Evidence): The new edition. Its ``normalize_with`` follows the same policy as
                :meth:`register`; a newer edition without a view while the old one had one would be a regression
                of the very thing the view exists for.
            supersedes (str): The block the new edition takes precedence over.

        Returns:
            dict[str, Any]: The new block's identity, the version this produced, and the pipeline that ran.
        """
        from boltzmann.identity.digest import BlockId
        from boltzmann.ingest.register import RegistrationRequest

        bounded = evidence.bounded(self.config.project.ingest.max_bytes)
        pipeline = coerce_pipeline(bounded.normalize_with, bounded.media_type)
        with self.session.write() as brain, translated():
            request = RegistrationRequest(
                media_type=bounded.media_type,
                actor=self.config.actor(),
                origin=bounded.origin,
                license=bounded.license,
                normalize_with=pipeline,
            )
            result = brain.replace(bounded.data, request, BlockId.parse(supersedes))
            return {**wire.registration(result), "supersedes": supersedes, "pipeline": pipeline}

    def put_content(self, evidence: Evidence) -> dict[str, Any]:
        """
        Store bytes addressably without registering a canonical block.

        For content a block will *reference* -- a normalized view produced elsewhere, an image a canonical
        block points at -- rather than content that is itself evidence. Nothing is committed, so the origin
        the evidence carries is not recorded anywhere.

        Args:
            evidence (Evidence): The bytes and what they are.

        Returns:
            dict[str, Any]: The content reference.
        """
        bounded = evidence.bounded(self.config.project.ingest.max_bytes)
        with self.session.write() as brain, translated():
            reference = brain.put_content(bounded.data, bounded.media_type)
            return reference.model_dump(mode="json")
