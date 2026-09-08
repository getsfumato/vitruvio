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
        """Test a registry from synchronous code."""
        return self.remote._run(
            self.registry_check_async(
                reference,
                username=username,
                token=token,
                anonymous=anonymous,
                insecure=insecure,
                local=local,
            )
        )

    async def registry_check_async(
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
        from vitruvio.runtime.distribution import preflight

        remote = self.remote._prepare(
            reference,
            username=username,
            token=token,
            anonymous=anonymous,
            insecure=insecure,
            local=local,
        )
        with self.session.pinned(Capability.INSPECT) as brain:
            result = await self.remote._request(preflight(remote.reference, remote.client, brain.store))
        return {**result, "warnings": remote.warnings}

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
        """Publish the brain from synchronous code."""
        return self.remote._run(
            self.push_async(
                reference,
                tag=tag,
                modules=modules,
                force=force,
                username=username,
                token=token,
                anonymous=anonymous,
                insecure=insecure,
                local=local,
            )
        )

    async def push_async(
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
        from vitruvio.runtime.vouch import vouch_travelling

        self._require_publishable()
        chosen = [coerce_memory_type(item) for item in modules] if modules else None
        remote = self.remote._prepare(
            reference,
            tag=tag,
            username=username,
            token=token,
            anonymous=anonymous,
            insecure=insecure,
            local=local,
        )

        with self.session.write() as brain:
            vouched = vouch_travelling(brain, chosen)
            digest = await self.remote._request(
                brain.push(
                    remote.client,
                    reference=remote.effective,
                    tag=remote.tag,
                    force=force,
                    modules=chosen,
                )
            )
        return {
            "reference": remote.reference,
            "effective": remote.effective,
            "tag": remote.tag,
            "digest": str(digest),
            "vouched": vouched,
            "warnings": remote.warnings,
        }

    @staticmethod
    def _refusal(config: ResolvedConfig) -> str | None:
        """
        Why this brain may not be published, or ``None``.

        One reading of the declaration, two responses to it: :meth:`push` raises, :meth:`push_all` skips. They
        used to be two readings, in two packages, and they had already drifted into meaning different things.

        Args:
            config (ResolvedConfig): The brain's resolved configuration.

        Returns:
            str | None: The reason, short enough for a table cell.
        """
        return None if config.publish_allowed else "publish = false"

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

        if self._refusal(self.config) is None:
            return
        name = self.config.brain_name or str(self.config.brain)
        raise PublishForbiddenError(
            f"brain {name!r} declares publish = false, so it is not published from here",
            hint=(
                "this is usually somebody else's upstream. If you really mean to publish a fork, set "
                f"publish = true under [brains.{name}] and give it its own `reference` first"
            ),
        )

    def push_all(
        self,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        force: bool = False,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Publish every brain in the project, each to the repository it derives.

        Keeps going after a failure rather than stopping at the first, for the reason ``pull_all`` gives: being
        told which one of six failed is better than stopping at the first and leaving four that would have worked
        unpublished and unmentioned.

        The loop lives here rather than in the CLI, which is where it was. ``ops/sources.py`` and ``ops/compound.py``
        both declined to copy it and said so in their docstrings, and the duplication had already produced two
        answers to one question: this module raised on ``publish = false`` while the CLI skipped. Both now read
        :meth:`_refusal`.

        Resolved once. Every brain in a project shares its actor, its policy and its registry, so the only thing
        that varies is which layout is open -- and re-reading the file per brain would let a project change
        underneath a half-finished publish.

        Args:
            tag (str | None): The tag to publish under.
            modules (Iterable[str] | None): Which modules travel. All of them when unsaid.
            force (bool): Allow a push that is not a fast-forward.
            anonymous (bool): Ignore stored credentials.
            insecure (bool | None): Allow plain HTTP.
            local (Path | None): Publish into a local OCI layout instead of a registry.

        Returns:
            dict[str, Any]: A record per brain -- published, skipped with a reason, or failed with a code -- plus
            the counts and whether every one that was attempted succeeded. Carried even when some failed, because
            a caller reading JSON needs to know *which*.

        Raises:
            ConfigError: If the project holds no brain that exists on disk.
        """
        from vitruvio.kernel import ConfigError, VitruvioError
        from vitruvio.runtime.ops.lifecycle import LifecycleOps
        from vitruvio.runtime.ops.projects import ProjectOps

        base = self.config
        brains = [brain for brain in ProjectOps(self.session).project()["brains"] if brain["exists"]]
        if not brains:
            raise ConfigError(
                "this project holds no brains to publish",
                hint="add one with `vitruvio project add <name>`",
            )

        results: list[dict[str, Any]] = []
        for brain in brains:
            name = str(brain["name"])
            config = base.model_copy(update={"brain": Path(str(brain["path"])), "brain_name": name})
            session = BrainSession(config)

            # A brain declared unpublishable is skipped rather than attempted, for the same reason an empty one
            # is: it is the project working as configured, and reporting it as a failure would make this exit
            # non-zero on a project holding one upstream brain, which is the normal shape for a team.
            reason = self._refusal(config) or (
                "nothing committed yet" if LifecycleOps(session).state()["block_count"] == 0 else None
            )
            if reason is not None:
                results.append({"brain": name, "ok": True, "skipped": True, "reason": reason})
                continue

            try:
                outcome = PublishOps(session).push(
                    None, tag=tag, modules=modules, force=force, anonymous=anonymous, insecure=insecure, local=local
                )
                results.append({"brain": name, "ok": True, "skipped": False, **outcome})
            except VitruvioError as error:
                results.append(
                    {
                        "brain": name,
                        "ok": False,
                        "skipped": False,
                        "error": error.message,
                        "code": error.code,
                    }
                )

        return {
            "brains": results,
            "published": sum(1 for item in results if item["ok"] and not item["skipped"]),
            "skipped": sum(1 for item in results if item["skipped"]),
            "failed": sum(1 for item in results if not item["ok"]),
            "ok": all(bool(item["ok"]) for item in results),
        }

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
        remote = self.remote._prepare(
            reference,
            username=username,
            token=token,
            anonymous=anonymous,
            insecure=insecure,
            local=local,
        )
        from boltzmann.exceptions import DistributionError, ReferenceNotFoundError

        lister = getattr(remote.client, "tags", None)
        try:
            if lister is None:
                found = sorted(remote.client.registry.get_tags(remote.effective))
            else:
                found = sorted(lister(remote.effective))
        except (DistributionError, ReferenceNotFoundError):
            # A repository with nothing published is the ordinary state before a first push, and "no tags" is the
            # answer -- not an error, and certainly not an internal one, which is what an unwrapped raise produced.
            found = []
        except Exception as error:
            raise translate(error) from error

        return {
            "reference": remote.reference,
            "tags": found,
            "warnings": remote.warnings,
            "published": bool(found),
        }
