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
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.ops.remote import RemoteOps, require_vector_index_ignore
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
        """
        Report what a pull would transfer, before transferring it.

        A canonical layer can be gigabytes, so "how much will this cost" has to be answerable without paying it.

        Reports ``local_work`` as well as the transfer, because cost is not the only thing worth knowing before a
        pull: an install adopts the remote composition, so anything committed here since the last pull stops being a
        member of it. Answered from the local head and nothing else, so it costs no extra round trip.

        Returns:
            dict[str, Any]: The plan, with the byte count taken from the resolved manifest.
        """
        import asyncio

        target = self.remote.reference_for(reference)
        chosen = [coerce_memory_type(item) for item in modules] if modules else None
        client, effective, warnings = self.remote._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        wanted_tag = tag or self.config.project.registry.tag

        brain = self.session.brain(Capability.INSPECT)
        with translated():
            manifest = asyncio.run(client.resolve(effective, wanted_tag))
            if ignore_vector_indices:
                require_vector_index_ignore(brain.plan_pull)
                plan = asyncio.run(
                    brain.plan_pull(
                        client,
                        effective,
                        wanted_tag,
                        modules=chosen,
                        ignore_vector_indices=True,
                    )
                )
            else:
                # Keep the ordinary pull compatible with the previous SDK API. Only the new opt-in path requires
                # the SDK release that added `ignore_vector_indices`.
                plan = asyncio.run(brain.plan_pull(client, effective, wanted_tag, modules=chosen))
        return {
            "reference": target,
            "tag": wanted_tag,
            **wire.install_plan(plan, manifest),
            "local_work": self._local_work(brain),
            "warnings": warnings,
        }

    def pull(
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
        Install a published brain.

        Returns:
            dict[str, Any]: The snapshot now installed.
        """
        import asyncio

        target = self.remote.reference_for(reference)
        chosen = [coerce_memory_type(item) for item in modules] if modules else None
        client, effective, warnings = self.remote._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        wanted_tag = tag or self.config.project.registry.tag

        with self.session.write() as brain:
            # Captured before, because after the pull the composition is the remote's and there is nothing left to
            # compare against. This is the only place the count can be exact rather than estimated.
            before, unreadable_before = self._composition_ids(brain)
            ignored: list[str] = []
            with translated():
                if ignore_vector_indices:
                    require_vector_index_ignore(brain.pull)
                    manifest = asyncio.run(client.resolve(effective, wanted_tag))
                    wanted = chosen if chosen is not None else manifest.modules
                    ignored = [
                        memory_type.value
                        for memory_type in wanted
                        if manifest.vector_index_for(memory_type) is not None
                    ]
                    snapshot = asyncio.run(
                        brain.pull(
                            client,
                            effective,
                            wanted_tag,
                            modules=chosen,
                            ignore_vector_indices=True,
                        )
                    )
                else:
                    snapshot = asyncio.run(brain.pull(client, effective, wanted_tag, modules=chosen))
            after, unreadable_after = self._composition_ids(brain)
            orphaned = sorted(before - after)
        unreadable = sorted(set(unreadable_before) | set(unreadable_after))
        if unreadable:
            # Said rather than folded into the number. `discarded` is what a caller reads to find out whether a
            # pull cost them evidence, and a scan that skipped a module cannot produce it exactly.
            warnings.append(
                f"could not enumerate {', '.join(unreadable)} while comparing what this pull replaced, so "
                "`discarded` is approximate; `vitruvio brain verify` reports what is actually resolvable"
            )
        if ignored:
            named = ", ".join(ignored)
            warnings.append(
                f"ignored published vector indices for {named}; run `vitruvio index build --force` to build "
                "compatible local vectors before relying on semantic retrieval"
            )
        return {
            "reference": target,
            "tag": wanted_tag,
            "snapshot": wire.snapshot(snapshot),
            "partial": chosen is not None,
            "discarded": len(orphaned),
            "discarded_blocks": orphaned[:20],
            "ignored_vector_indices": ignored,
            "warnings": warnings,
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
        import asyncio

        from vitruvio.runtime.ops.reconcile import ReconcileOps

        target = self.remote.reference_for(reference)
        chosen = [coerce_memory_type(item) for item in modules] if modules else None
        client, effective, warnings = self.remote._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        wanted_tag = tag or self.config.project.registry.tag

        # WRITE rather than INSPECT: a fetch puts blocks in the store. It moves no pointer, which is the
        # property that makes it safe, but it is not a read.
        with self.session.write() as brain, translated():
            fetched = asyncio.run(brain.fetch(client, effective, wanted_tag, modules=chosen))

        payload = {
            "reference": target,
            "tag": wanted_tag,
            **wire.fetch_result(fetched),
            "warnings": warnings,
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

        Answered from ``Origin``, which records the snapshot digest of the last pull, so the question "did I commit
        anything since?" is a local comparison and costs no round trip. The count is a delta between two snapshot
        documents rather than a set difference, because a plan must not download a composition to answer it.

        Args:
            brain (Brain): The opened brain.

        Returns:
            dict[str, Any]: ``diverged``, how many blocks are at stake, and which snapshot holds them.
        """
        snapshot = brain.snapshot()
        installed = sum(reference.block_count for reference in snapshot.modules.values())
        origin = brain.origin
        clean = {"diverged": False, "blocks": 0, "snapshot": None, "pulled": None}

        if installed == 0:
            return clean
        if origin is None:
            # Never pulled, and it holds blocks: everything in it is local, and a pull replaces the lot.
            return {"diverged": True, "blocks": installed, "snapshot": str(snapshot.digest), "pulled": None}
        if str(snapshot.digest) == str(origin.snapshot):
            return clean

        baseline = self._snapshot_at(brain, str(origin.snapshot))
        blocks = None if baseline is None else max(installed - baseline, 0)
        return {
            "diverged": True,
            "blocks": blocks,
            "snapshot": str(snapshot.digest),
            "pulled": str(origin.snapshot),
        }

    @staticmethod
    def _snapshot_at(brain: Brain, digest: str) -> int | None:
        """
        How many blocks one retained snapshot held, or ``None`` when it can no longer be read.

        ``None`` rather than zero: a missing baseline means the size of the local work is *unknown*, and reporting
        an unknown as "nothing" is the failure this whole report exists to prevent.
        """
        from boltzmann.brain import Snapshot
        from boltzmann.identity.digest import OciDigest

        try:
            document = brain.store.get_bytes(OciDigest.parse(digest))
        # Broad on purpose: a pruned or unreadable blob is not an error here, it is an unknown.
        except Exception:
            return None
        try:
            return sum(reference.block_count for reference in Snapshot.model_validate_json(document).modules.values())
        except ValueError:  # pragma: no cover - a blob that is not a snapshot document
            return None

    @staticmethod
    def _composition_ids(brain: Brain) -> tuple[set[str], list[str]]:
        """
        Every block identity currently a member of some installed module, and which modules could not be read.

        The second value is returned rather than suppressed because the caller subtracts two of these to report how
        many blocks a pull discarded. A module skipped on the way *in* makes that count too small; one skipped on
        the way *out* makes it too large. Either way it is the number that tells someone they lost evidence, so a
        count taken over an incomplete scan has to say so rather than look exact.

        Args:
            brain (Brain): The brain to read.

        Returns:
            tuple[set[str], list[str]]: The identities, and one entry per module that would not enumerate.
        """
        found: set[str] = set()
        unreadable: list[str] = []
        for kind in brain.snapshot().installed:
            try:
                found.update(str(identity) for identity in brain.module(kind).block_ids)
            except Exception as error:  # a module that will not enumerate is a fact about the count, not a failure
                unreadable.append(f"{kind.value} ({type(error).__name__})")
        return found, unreadable
