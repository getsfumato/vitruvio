"""Packing a brain and pushing it to a registry.

Publishing only. Installing is :mod:`vitruvio.runtime.ops.install`, and the two are separate because the failures
are: a push is refused for diverging history or for a policy that forbids publication, and a pull replaces the head
under everything already open.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime import wire
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import memory_type as coerce_memory_type
from vitruvio.runtime.mapping import translate, translated
from vitruvio.runtime.ops.remote import RemoteOps
from vitruvio.runtime.session import BrainSession


class PublishOps:
    """Publishing, as operations."""

    def __init__(self, session: BrainSession) -> None:
        """
        Args:
            session (BrainSession): The shared session.
        """
        self.session = session
        self.remote = RemoteOps(session)

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration, read through the session that owns it."""
        return self.session.config

    def pack(self, *, tag: str | None = None, modules: Iterable[str] | None = None) -> dict[str, Any]:
        """
        Build the OCI artifact locally, without pushing.

        Vouches for the vector index first: without that, ``pack`` silently omits the one layer a consumer cannot
        rebuild. See :mod:`vitruvio.runtime.vouch`.

        Args:
            tag (str | None): The tag to file it under.
            modules (Iterable[str] | None): Publish only these modules.

        Returns:
            dict[str, Any]: The manifest, with the digest a registry would file it under.
        """
        from vitruvio.runtime.vouch import vouch_travelling

        chosen = [coerce_memory_type(item) for item in modules] if modules else None
        with self.session.write() as brain:
            vouched = vouch_travelling(brain, chosen)
            with translated():
                manifest = brain.pack(tag=tag or self.config.project.registry.tag, modules=chosen)
        return {**wire.manifest(manifest), "vouched": vouched}

    def registry_check(
        self,
        reference: str | None = None,
        *,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Test a registry with an artifact shaped exactly like a brain.

        Answers the question that a first push otherwise answers the hard way: does this registry accept a custom
        ``config.mediaType``? Checked rather than assumed, because the manifest's shape is fixed by the protocol.

        Returns:
            dict[str, Any]: Per-check outcomes, and a hint naming the real alternatives when it fails.
        """
        import asyncio

        from vitruvio.runtime.distribution import preflight

        target = self.remote.reference_for(reference)
        client, _, warnings = self.remote._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            result = asyncio.run(preflight(target, client, brain.store))
        return {**result, "warnings": warnings}

    def push(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        force: bool = False,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Publish the brain.

        The SDK's own guards apply: a push that would narrow the module set is refused, and a push that is not a
        fast-forward is refused -- the latter failing *closed* on any error that is not a 404, so a refusal that looks
        like an absence cannot disable the check.

        Returns:
            dict[str, Any]: The digest the registry filed the manifest under.

        Raises:
            PublishForbiddenError: If the brain declares ``publish = false``. Checked first, before the reference is
                resolved and before a credential is read, because a refusal that happens after a credential lookup
                has already told a keyring what you were about to do.
        """
        import asyncio

        from vitruvio.runtime.vouch import vouch_travelling

        self._require_publishable()
        target = self.remote.reference_for(reference)
        chosen = [coerce_memory_type(item) for item in modules] if modules else None
        client, effective, warnings = self.remote._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )

        with self.session.write() as brain:
            vouched = vouch_travelling(brain, chosen)
            with translated():
                digest = asyncio.run(
                    brain.push(
                        client,
                        reference=effective,
                        tag=tag or self.config.project.registry.tag,
                        force=force,
                        modules=chosen,
                    )
                )
        return {
            "reference": target,
            "effective": effective,
            "tag": tag or self.config.project.registry.tag,
            "digest": str(digest),
            "vouched": vouched,
            "warnings": warnings,
        }

    def _require_publishable(self) -> None:
        """
        Refuse a push the project declared off-limits.

        The mistake this prevents is one command long and made by someone who does not expect to make it. A pulled
        brain is a working copy like any other -- nothing in the protocol distinguishes a brain you authored from one
        you installed -- so a stray ``dist push`` publishes a fork of somebody else's brain under whichever
        repository this project derives, and the two lineages diverge with nobody informed.

        Raises:
            PublishForbiddenError: If the selected brain declares ``publish = false``.
        """
        from vitruvio.kernel import PublishForbiddenError

        if self.config.publish_allowed:
            return
        name = self.config.brain_name or str(self.config.brain)
        raise PublishForbiddenError(
            f"brain {name!r} declares publish = false, so it is not published from here",
            hint=(
                "this is usually somebody else's upstream. If you really mean to publish a fork, set "
                f"publish = true under [brains.{name}] and give it its own `reference` first"
            ),
        )

    def tags(
        self,
        reference: str | None = None,
        *,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Which tags a repository holds.

        Returns:
            dict[str, Any]: The tags, or an explanation when the registry does not offer a listing.
        """
        target = self.remote.reference_for(reference)
        client, effective, warnings = self.remote._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        from boltzmann.exceptions import DistributionError, ReferenceNotFoundError

        lister = getattr(client, "tags", None)
        try:
            if lister is None:
                found = sorted(client.registry.get_tags(effective))
            else:
                found = sorted(lister(effective))
        except (DistributionError, ReferenceNotFoundError):
            # A repository with nothing published is the ordinary state before a first push, and "no tags" is the
            # answer -- not an error, and certainly not an internal one, which is what an unwrapped raise produced.
            found = []
        except Exception as error:
            raise translate(error) from error

        return {"reference": target, "tags": found, "warnings": warnings, "published": bool(found)}
