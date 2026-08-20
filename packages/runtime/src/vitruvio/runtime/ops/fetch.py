"""The parts of one pull: dedup, the redaction guard, and registering what came back."""

from __future__ import annotations

from typing import Any

from boltzmann.brain import Brain

from vitruvio.kernel import ResolvedConfig, VitruvioError
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession


class FetchOps:
    """One item of a pull, decided and registered.

    Named `fetch` rather than `pull` on purpose: `install.pull` is *installing a brain from a registry*, and two
    unrelated meanings of "pull" one module apart is how a reader ends up in the wrong file.
    """

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

    def _source(self, name: str, spec: Any) -> Any:
        """Construct one declared source, with its path resolved against the configuration file."""
        from vitruvio.ingest.sources import resolve_source

        return resolve_source(
            name,
            spec,
            root=self.config.source_root(name),
            cwd=self.config.config_file.parent if self.config.config_file else None,
        )

    def _kind_provenance(self, kind: str) -> str | None:
        """Where a kind came from -- built-in, a plugin file, an entry point -- for ``source status``."""
        from vitruvio.ingest.sources import kinds

        found = kinds().get(kind)
        return found.provenance if found else None

    @staticmethod
    def _item_row(item: Any) -> dict[str, Any]:
        """The reportable part of an item, before anything has been decided about it."""
        return {"id": item.id, "origin": item.origin, "title": item.title, "media_type": item.media_type}

    def _pull_one(
        self,
        brain: Brain,
        source: Any,
        spec: Any,
        item: Any,
        *,
        dry_run: bool,
        refetch: bool,
    ) -> dict[str, Any]:
        """
        Decide about one item, and register it if it is new.

        The order is the whole point: the cheap checks come first, so a repeated pull over a hundred unchanged
        files performs a hundred hash-map probes and no downloads.
        """
        from vitruvio.ingest.sources import FetchResult
        from vitruvio.kernel import SourceError, VitruvioError

        row = self._item_row(item)
        listed_media_type = spec.media_type or item.media_type
        media_type = listed_media_type or "application/octet-stream"

        if not refetch:
            held = self._registered_as(brain, item.origin)
            if held is not None:
                if listed_media_type is None:
                    # A source whose cheap listing cannot name the type may discover it while fetching. A generic
                    # old registration is therefore not enough to skip: fetch once so it can be corrected. Once a
                    # specific type is held, origin dedup is cheap again on every later pull.
                    held_media_type = held.get("media_type")
                    matches = held_media_type not in (None, "application/octet-stream") and self._matches_declaration(
                        held, str(held_media_type), spec.normalize_with
                    )
                else:
                    matches = self._matches_declaration(held, media_type, spec.normalize_with)
                if matches:
                    return {
                        **row,
                        "media_type": held.get("media_type") or row["media_type"],
                        "outcome": "skipped",
                        "reason": "origin already registered",
                        "block": held["block"],
                    }

        if dry_run:
            return {**row, "outcome": "would-fetch"}

        try:
            fetched = source.fetch(item)
        except (SourceError, OSError) as error:
            # Per-item, and accumulated rather than fatal: one unreadable file in a folder of forty must not cost
            # the other thirty-nine their registration.
            return {**row, "outcome": "failed", "reason": str(error)}

        if isinstance(fetched, FetchResult):
            data = fetched.data
            media_type = spec.media_type or fetched.media_type or item.media_type or "application/octet-stream"
            row = {
                **row,
                "title": fetched.title or row["title"],
                "media_type": media_type,
            }
        else:
            data = fetched

        guarded = self._tombstoned(brain, data)
        if guarded is not None:
            return {**row, "outcome": "skipped", "reason": guarded}

        try:
            result = self._register_bytes(brain, data, media_type=media_type, origin=item.origin, spec=spec)
        except VitruvioError as error:
            return {**row, "outcome": "failed", "reason": str(error)}
        return {**row, **result}

    def _register_bytes(
        self,
        brain: Brain,
        data: bytes,
        *,
        media_type: str,
        origin: str,
        spec: Any,
    ) -> dict[str, Any]:
        """Register fetched bytes as canonical evidence, under the source's declared licence and pipeline."""
        from boltzmann.ingest.register import RegistrationRequest

        with translated():
            registration = brain.register(
                data,
                RegistrationRequest(
                    media_type=media_type,
                    actor=self.config.actor(),
                    origin=origin,
                    license=spec.license,
                    normalize_with=spec.normalize_with,
                ),
            )
        return {
            "outcome": "duplicate" if registration.duplicate else "registered",
            "block": str(registration.block_id),
            "size": len(data),
        }

    @staticmethod
    def _tombstoned(brain: Brain, data: bytes) -> str | None:
        """
        Whether these bytes were redacted, checked *before* anything writes them back.

        The one check here that cannot be moved or merged into another. ``Brain.register`` stores the blob before it
        decides whether the block is a duplicate, and a tombstoned digest still answers ``has()`` with True while
        its file is gone -- so calling ``register`` with redacted bytes re-materialises exactly what a retention
        policy destroyed, and then reports a duplicate as though nothing had happened. A scheduled pull would undo
        every redaction, quietly, on a schedule.

        Returns:
            str | None: Why these bytes must not be registered, or ``None`` when it is safe.
        """
        from boltzmann.identity.digest import OciDigest

        digest = OciDigest.of(data)
        store = brain.store
        if store.has(digest) and not store.is_resolvable(digest):
            return (
                f"{digest} was redacted; re-registering would restore the bytes a retention policy destroyed. "
                f"Undo the redaction deliberately if that is what you want"
            )
        return None

    def _registered_as(self, brain: Brain, origin: str) -> dict[str, Any] | None:
        """
        What was registered from this origin before, or ``None``.

        One hash-map probe on the provenance module, which is the reason ``origin`` is projected at all. Falls back
        to ``None`` -- never to a scan -- when no such index is registered: a pull that silently became O(n) per
        item over a large brain would look like a hang, and content addressing still catches the duplicate.
        """
        from boltzmann.blocks.memory_type import MemoryType

        from vitruvio.indices import HashMapIndex, IdentityKey, IdQuery, fold

        index = next(
            (
                candidate
                for candidate in brain.indices.get(MemoryType.PROVENANCE, [])
                if isinstance(candidate, HashMapIndex)
            ),
            None,
        )
        if index is None or not index.population:
            return None

        results = index.lookup(IdQuery(keys=((IdentityKey.ORIGIN, fold(origin)),)))
        for identity in results.identities():
            record = self._registration_record(brain, identity)
            if record is not None:
                return record
        return None

    def _registration_record(self, brain: Brain, provenance_id: str) -> dict[str, Any] | None:
        """The canonical block one registration record talks about, with what it was registered as."""
        from boltzmann.identity.digest import BlockId

        try:
            with translated():
                block = brain.resolve(BlockId.parse(provenance_id))
        except VitruvioError:
            # A record whose provenance block is no longer resolvable tells us nothing useful, and a pull is not
            # the place to raise about it.
            return None
        record: Any = getattr(block, "record", None)
        target = getattr(record, "block", None)
        if target is None:
            return None
        return {"block": str(target), **self._canonical_shape(brain, str(target))}

    def _canonical_shape(self, brain: Brain, block_id: str) -> dict[str, Any]:
        """
        The two parts of a canonical block's identity that a declaration can change: media type and view.

        Read so that editing a source's ``media_type`` or ``normalize_with`` re-registers instead of being skipped
        by the origin check. Both are inputs to the block's identity, so a silent skip would make the correction do
        nothing -- and the block that was meant to be fixed would still be wrong.

        ``normalized`` is whether a view exists, not which pipeline produced it. A view carries only its blob,
        media type and size; the pipeline's name lives in a separate ``NormalizationRecord`` keyed by the canonical
        block, and nothing indexes that key -- reading it would make this the per-item scan that
        :meth:`_registered_as` refuses to become. ``readable`` is kept apart from the two values because "no view"
        and "could not be read" are different facts, and only one of them is evidence about a declaration.
        """
        from boltzmann.identity.digest import BlockId

        try:
            with translated():
                block = brain.resolve(BlockId.parse(block_id))
        except VitruvioError:  # pragma: no cover - a record pointing at an absent block
            return {"media_type": None, "normalized": None, "readable": False}
        return {
            "media_type": getattr(block, "media_type", None),
            "normalized": getattr(block, "normalized_view", None) is not None,
            "readable": True,
        }

    @staticmethod
    def _matches_declaration(held: dict[str, Any], media_type: str, normalize_with: str | None) -> bool:
        """
        Whether what is already registered is what the declaration now asks for.

        A ``False`` here is what turns "I fixed the media type in vitruvio.toml" into a new block rather than into
        nothing at all. Nothing is compared when the held block could not be read, because an unreadable block is
        not evidence that the declaration changed.

        Normalization is compared as presence, which decides the two cases that occur in practice: declaring
        ``normalize_with`` on a source whose blocks predate it, and removing it from one whose blocks have a view.
        Both converge -- the block registered next matches what is now declared. *Swapping* one pipeline name for
        another is not detectable here, because which pipeline produced an existing view is not knowable in O(1)
        (see :meth:`_canonical_shape`); that correction needs ``--refetch``.
        """
        if not held.get("readable", True):
            return True
        if held.get("media_type") is not None and held["media_type"] != media_type:
            return False
        view = held.get("normalized")
        return view is None or view == (normalize_with is not None)
