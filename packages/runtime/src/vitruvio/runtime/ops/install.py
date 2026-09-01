"""Installing a brain from a registry, and saying what that would replace first.

The only module that calls :meth:`~vitruvio.runtime.session.BrainSession.invalidate`, and the reason it exists:
`pull` advances the pointer, so every brain handed out before it describes the composition that was just replaced.
`plan_pull` is a separate operation because a caller has to be able to see what it would lose before losing it.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from boltzmann.brain import Brain

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime import wire
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import memory_type as coerce_memory_type
from vitruvio.runtime.ops.remote import RemoteOps, require_vector_index_ignore
from vitruvio.runtime.pull_impact import (
    CompositionMembers,
    ImpactCertainty,
    PullImpact,
    compare_members,
    composition_members,
    read_snapshot,
)
from vitruvio.runtime.session import BrainSession


class InstallOps:
    """Installing, as operations."""

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

    def plan_pull(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        ignore_vector_indices: bool = False,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """Report what a pull would transfer from synchronous code."""
        return self.remote._run(
            self.plan_pull_async(
                reference,
                tag=tag,
                modules=modules,
                ignore_vector_indices=ignore_vector_indices,
                username=username,
                token=token,
                anonymous=anonymous,
                insecure=insecure,
                local=local,
            )
        )

    async def plan_pull_async(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        ignore_vector_indices: bool = False,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Report what a pull would transfer, before transferring it.

        A canonical layer can be gigabytes, so "how much will this cost" has to be answerable without paying it.

        Reports ``local_work`` as well as the transfer, because cost is not the only thing worth knowing before a
        pull: an install adopts the remote composition, so anything committed here since the last pull stops being a
        member of it. Answered from the local head and nothing else, so it costs no extra round trip.

        Returns:
            dict[str, Any]: The plan, with the byte count taken from the resolved manifest.
        """
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

        brain = self.session.brain(Capability.INSPECT)
        manifest = await self.remote._request(remote.client.resolve(remote.effective, remote.tag))
        if ignore_vector_indices:
            require_vector_index_ignore(brain.plan_pull)
            plan = await self.remote._request(
                brain.plan_pull(
                    remote.client,
                    remote.effective,
                    remote.tag,
                    modules=chosen,
                    ignore_vector_indices=True,
                )
            )
        else:
            # Keep the ordinary pull compatible with the previous SDK API. Only the new opt-in path requires
            # the SDK release that added `ignore_vector_indices`.
            plan = await self.remote._request(
                brain.plan_pull(remote.client, remote.effective, remote.tag, modules=chosen)
            )
        local_work = self._local_work(brain)
        return {
            "reference": remote.reference,
            "tag": remote.tag,
            **wire.install_plan(plan, manifest),
            "local_work": local_work,
            "impact": local_work["impact"],
            "warnings": remote.warnings,
        }

    def pull(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        ignore_vector_indices: bool = False,
        allow_rollback: bool = False,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """Install a published brain from synchronous code."""
        return self.remote._run(
            self.pull_async(
                reference,
                tag=tag,
                modules=modules,
                ignore_vector_indices=ignore_vector_indices,
                allow_rollback=allow_rollback,
                username=username,
                token=token,
                anonymous=anonymous,
                insecure=insecure,
                local=local,
            )
        )

    async def pull_async(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        ignore_vector_indices: bool = False,
        allow_rollback: bool = False,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Install a published brain.

        Returns:
            dict[str, Any]: The snapshot now installed.
        """
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
            before = composition_members(brain, brain.snapshot())
            ignored: list[str] = []
            if ignore_vector_indices:
                require_vector_index_ignore(brain.pull)
                manifest = await self.remote._request(remote.client.resolve(remote.effective, remote.tag))
                wanted = chosen if chosen is not None else manifest.modules
                ignored = [
                    memory_type.value for memory_type in wanted if manifest.vector_index_for(memory_type) is not None
                ]
                snapshot = await self.remote._request(
                    brain.pull(
                        remote.client,
                        remote.effective,
                        remote.tag,
                        modules=chosen,
                        ignore_vector_indices=True,
                        allow_rollback=allow_rollback,
                        verification=self.config.project.authenticity.build(),
                    )
                )
            else:
                snapshot = await self.remote._request(
                    brain.pull(
                        remote.client,
                        remote.effective,
                        remote.tag,
                        modules=chosen,
                        allow_rollback=allow_rollback,
                        verification=self.config.project.authenticity.build(),
                    )
                )
            after = composition_members(brain, brain.snapshot())
            impact = compare_members(before, after, planned=False)
        if impact.certainty is ImpactCertainty.UNKNOWN:
            remote.warnings.append(
                f"could not enumerate {', '.join(impact.unreadable)} while comparing what this pull replaced, so "
                "its impact is unknown; `vitruvio brain verify` reports what is actually resolvable"
            )
        if ignored:
            named = ", ".join(ignored)
            remote.warnings.append(
                f"ignored published vector indices for {named}; run `vitruvio index build --force` to build "
                "compatible local vectors before relying on semantic retrieval"
            )
        return {
            "reference": remote.reference,
            "tag": remote.tag,
            "snapshot": wire.snapshot(snapshot),
            "partial": chosen is not None,
            # Kept for JSON compatibility. New consumers should read `impact`, whose certainty prevents an unknown
            # scan from being mistaken for an exact zero.
            "discarded": impact.blocks or 0,
            "discarded_blocks": list(impact.block_ids[:20]),
            "impact": impact.as_dict(),
            "ignored_vector_indices": ignored,
            "warnings": remote.warnings,
        }

    def fetch(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        reconcile: bool = True,
        reason: str | None = None,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """Retrieve a remote history from synchronous code."""
        return self.remote._run(
            self.fetch_async(
                reference,
                tag=tag,
                modules=modules,
                reconcile=reconcile,
                reason=reason,
                username=username,
                token=token,
                anonymous=anonymous,
                insecure=insecure,
                local=local,
            )
        )

    async def fetch_async(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        reconcile: bool = True,
        reason: str | None = None,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Retrieve a remote history without moving the local pointer, and reconcile it when that is safe.

        Lives beside ``pull`` because it is the same transport with the same client, the same credential
        resolution and the same module coercion -- and differs in the one respect that makes it worth having:
        ``pull`` adopts the other side's composition, this holds both histories and adopts nothing yet.

        Then, when the brain declares a strategy, it goes on to reconcile. Three outcomes, and the boundary
        between them is the whole design:

        * **No declaration.** Nothing is reconciled. Choosing a strategy decides who stays on record as the
          author of the incoming work, so vitruvio reports the plan and says how to choose.
        * **A clean plan.** Every incoming block applied and nothing here leaves, so there is nothing for a
          person to decide and it commits.
        * **Anything else.** The plan is reported and *the reconciliation is not opened*. Opening one sets the
          ``reconcile`` pointer, and the SDK then refuses every ordinary write until it is resolved -- far too
          much to do to a brain from a command someone ran to look. Nothing is lost by waiting: the plan is
          recomputed rather than stored, so the resolver recomputes it when somebody is ready to answer.

        Args:
            reference (str | None): The repository.
            tag (str | None): Which tag.
            modules (Iterable[str] | None): Which modules to retrieve.
            reconcile (bool): Whether to go on to reconcile. False fetches and stops, which is what a script
                driving the steps itself wants.
            reason (str | None): Why, recorded by the reconciliation if one happens.
            username (str | None): Registry username.
            token (str | None): Registry token.
            anonymous (bool): Resolve without credentials.
            insecure (bool | None): Allow plain HTTP.
            local (Path | None): Use a filesystem registry rooted here.

        Returns:
            dict[str, Any]: What arrived, and under ``reconciliation`` what was done about it -- or why nothing
            was.
        """
        from vitruvio.runtime.ops.reconcile import ReconcileOps

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

        # WRITE rather than INSPECT: a fetch puts blocks in the store. It moves no pointer, which is the
        # property that makes it safe, but it is not a read.
        with self.session.write() as brain:
            fetched = await self.remote._request(
                brain.fetch(remote.client, remote.effective, remote.tag, modules=chosen)
            )

        payload = {
            "reference": remote.reference,
            "tag": remote.tag,
            **wire.fetch_result(fetched),
            "warnings": remote.warnings,
        }
        if not reconcile:
            return {**payload, "reconciliation": {"attempted": False, "why": "not requested"}}
        return {**payload, "reconciliation": self._auto_reconcile(ReconcileOps(self.session), fetched, reason)}

    def _auto_reconcile(self, ops: Any, fetched: Any, reason: str | None) -> dict[str, Any]:
        """
        Reconcile what a fetch brought, when doing so decides nothing on the operator's behalf.

        Separated from :meth:`fetch` because the transport and the judgment are different concerns and the
        judgment is the part with rules in it.

        Args:
            ops (Any): The reconciliation operations.
            fetched (Any): What the fetch retrieved.
            reason (str | None): Why, for the record.

        Returns:
            dict[str, Any]: ``attempted``, and either what happened or why it did not.
        """
        theirs = str(fetched.digest)
        declared = ops.declared_strategy()
        if declared is None:
            return {
                "attempted": False,
                "why": "no strategy declared",
                "theirs": theirs,
                "hint": (
                    "the three strategies land the same blocks and differ in whose name stays on the incoming "
                    "work, so this one is yours to state: set `reconcile` on the brain in vitruvio.toml, or run "
                    "`vitruvio reconcile merge|rebase|squash`"
                ),
            }

        # Asked before planning, and asked of reachability rather than of the plan. A plan against a history this
        # brain already merged still reports their blocks as additions, so trusting `is_noop` here made every
        # repeated fetch mint another snapshot of nothing. See `ReconcileOps.contains`.
        if ops.contains(theirs):
            return {"attempted": False, "why": "already contained", "theirs": theirs}

        plan = ops.plan(theirs)
        if plan["is_noop"]:
            return {"attempted": False, "why": "already contained", "theirs": theirs, "plan": plan}
        if not plan["is_clean"]:
            # Reported, not started. See `fetch`: opening the reconciliation would block every write until
            # somebody resolved it, and the plan costs nothing to recompute when they do.
            return {
                "attempted": False,
                "why": "not clean",
                "theirs": theirs,
                "plan": plan,
                "strategy": str(declared),
                # Names the command that *starts* one, because this branch deliberately did not. Pointing at
                # `resolve` would have been pointing at a screen that opens on "nothing in progress": it resolves
                # a reconciliation, it does not originate one, and it has no way to -- nothing here persists which
                # history was fetched, so the digest has to be typed once.
                "hint": (
                    f"`vitruvio reconcile {declared} {theirs} --reason ...` opens it and reports what is open; "
                    "then `vitruvio reconcile resolve` decides it. `vitruvio reconcile tree` shows the split"
                ),
            }

        result = ops.reconcile(
            theirs,
            strategy=declared,
            reason=reason or f"fetched from {fetched.reference}:{fetched.tag}",
        )
        return {"attempted": True, "why": "clean", "theirs": theirs, "strategy": str(declared), **result}

    def _local_work(self, brain: Brain) -> dict[str, Any]:
        """
        What is installed here that no pull put here.

        Answered from ``Origin``, which records the snapshot digest of the last pull, so the question is a local
        comparison and costs no registry round trip. Membership is compared by identity, so replacement and
        simultaneous addition/removal cannot disappear behind equal totals.

        Args:
            brain (Brain): The opened brain.

        Returns:
            dict[str, Any]: ``diverged``, how many blocks are at stake, and which snapshot holds them.
        """
        snapshot = brain.snapshot()
        current = composition_members(brain, snapshot)
        origin = brain.origin
        empty = CompositionMembers(frozenset())

        if origin is None:
            impact = compare_members(current, empty, planned=True)
            return self._local_work_payload(
                impact,
                diverged=bool(current.block_ids or current.unreadable),
                snapshot=str(snapshot.digest),
                pulled=None,
            )
        if str(snapshot.digest) == str(origin.snapshot):
            return self._local_work_payload(
                compare_members(current, current, planned=False),
                diverged=False,
                snapshot=None,
                pulled=None,
            )

        baseline = read_snapshot(brain, str(origin.snapshot))
        if baseline is None:
            missing = CompositionMembers(frozenset(), ("baseline snapshot (unreadable)",))
            impact = compare_members(current, missing, planned=True)
            return self._local_work_payload(
                impact,
                diverged=True,
                snapshot=str(snapshot.digest),
                pulled=str(origin.snapshot),
            )

        diverged = snapshot.modules != baseline.modules
        previous = composition_members(brain, baseline)
        impact = compare_members(current, previous, planned=diverged)
        return self._local_work_payload(
            impact,
            diverged=diverged,
            snapshot=str(snapshot.digest) if diverged else None,
            pulled=str(origin.snapshot) if diverged else None,
        )

    @staticmethod
    def _local_work_payload(
        impact: PullImpact,
        *,
        diverged: bool,
        snapshot: str | None,
        pulled: str | None,
    ) -> dict[str, Any]:
        """Preserve the old fields while attaching the explicit shared impact result."""
        detail = impact.as_dict()
        return {
            "diverged": diverged,
            "blocks": impact.blocks,
            "snapshot": snapshot,
            "pulled": pulled,
            "certainty": detail["certainty"],
            "block_ids": detail["block_ids"],
            "unreadable": detail["unreadable"],
            "impact": detail,
        }
