"""Branches: a line of work per tag, joined when somebody chooses to.

The protocol defines them (paper Section 7.5) and the SDK implements them, so what is here is the part that is
vitruvio's: which brain view each operation opens, the default tag a branch maps to under this project's
``[registry].tag``, the reconciliation strategy a brain may declare, and the shape the result is reported in.

The reason they exist is a team on one tag. The divergence check makes that safe and not workable: every push after
the first exits 8 until its writer reconciles, so ``latest`` serializes everybody. Each person on a branch of their
own publishes to ``br.<name>`` and is never refused by anyone else; joining is done once, on purpose, with
``branch merge``.

Every operation that moves the head opens the *install* view of the brain -- the one ``pull`` uses, with no indices
registered -- because moving the head is not a commit and must not re-embed the whole composition on the way. The
vector sidecar is bound to a Merkle root, so a query after a switch never reads vectors built for the other branch;
``vitruvio index build`` refreshes them, from the embedding cache. The one exception is a merge that reconciles,
which commits like ``reconcile`` does and opens the brain the same way.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from vitruvio.kernel import ReconcileStrategy, ResolvedConfig, UsageError, VitruvioError
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import strategy as coerce_strategy
from vitruvio.runtime.mapping import translate, translated
from vitruvio.runtime.ops.remote import RemoteOps
from vitruvio.runtime.session import BrainSession


class BranchOps:
    """Branches, as operations."""

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

    def branch_current(self) -> str:
        """
        The branch this brain is on.

        Read from the ref pointer without opening the brain, so it is as cheap as ``config show``.

        Returns:
            str: Its name; ``main`` for a brain that never created a branch.
        """
        from vitruvio.runtime.branches import current_branch_tag

        return current_branch_tag(self.config).branch

    def branch_list(
        self,
        *,
        remote: bool = False,
        reference: str | None = None,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """List the branches from synchronous code."""
        return self.remote._run(
            self.branch_list_async(
                remote=remote,
                reference=reference,
                username=username,
                token=token,
                anonymous=anonymous,
                insecure=insecure,
                local=local,
            )
        )

    async def branch_list_async(
        self,
        *,
        remote: bool = False,
        reference: str | None = None,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Every branch this brain holds and, when asked, every branch the registry publishes.

        Args:
            remote (bool): Also list the repository's tags and read the branches off them. A network request.
            reference (str | None): The repository, when it is not the one this brain publishes to.
            username (str | None): Registry account.
            token (str | None): Registry token.
            anonymous (bool): Ignore stored credentials.
            insecure (bool | None): Allow plain HTTP.
            local (Path | None): A local OCI layout to list instead of a registry.

        Returns:
            dict[str, Any]: ``current``, ``branches`` (local, the current one first, each saying whether its head
            is the one last pushed or pulled), and ``remote`` when asked -- each published branch and whether a
            local branch of that name exists.
        """
        from vitruvio.runtime.branches import tag_for_branch

        brain = self.session.brain(Capability.INSPECT)
        with translated():
            held = brain.branches()
            current = brain.current_branch()
        branches = [
            {
                "name": info.name,
                "current": info.current,
                "tag": tag_for_branch(self.config, info.name),
                "snapshot": str(info.snapshot),
                "published": str(info.published) if info.published is not None else None,
                "unpublished": info.published != info.snapshot,
            }
            for info in held
        ]
        result: dict[str, Any] = {"current": current, "branches": branches, "remote": None, "warnings": []}
        if not remote:
            return result

        prepared = self.remote._prepare(
            reference,
            username=username,
            token=token,
            anonymous=anonymous,
            insecure=insecure,
            local=local,
        )
        from boltzmann.branches import branch_for_tag
        from boltzmann.distribution.registry import RegistryTags

        if not isinstance(prepared.client, RegistryTags):
            raise VitruvioError(
                "this registry client cannot list tags, so it cannot discover published branches",
                hint="upgrade pyboltzmann, or name the branch: `vitruvio branch switch NAME` tracks it by its tag",
            )
        try:
            tags = await self.remote._request(prepared.client.list_tags(prepared.effective))
        except VitruvioError as error:
            if error.code != "REFERENCE_NOT_FOUND":
                raise
            # Nothing published under this reference yet -- the ordinary state before a first push, which `dist tags`
            # already reports as no tags rather than as an error.
            tags = []
        # Decoded against this project's tag for main, not the SDK's `latest` default: a project that publishes main
        # to `stable` has `stable` as its default branch's tag, and `latest` there is just a tag.
        published = {
            name: tag for tag in tags if (name := branch_for_tag(tag, self.config.project.registry.tag)) is not None
        }
        names = {info.name for info in held}
        result["remote"] = [
            {"name": name, "tag": tag, "local": name in names} for name, tag in sorted(published.items())
        ]
        result["reference"] = prepared.reference
        result["warnings"] = prepared.warnings
        return result

    def branch_create(self, name: str, *, start: str | None = None, switch: bool = False) -> dict[str, Any]:
        """
        Start a branch, at the current head unless told otherwise.

        Nothing is published. The first ``dist push`` on it creates ``br.<name>`` on the registry.

        Args:
            name (str): The new branch.
            start (str | None): Where it starts: another branch, or a snapshot digest this brain holds.
            switch (bool): Make it current straight away.

        Returns:
            dict[str, Any]: The branch, its tag, and whether it is now current.
        """
        from vitruvio.runtime.branches import align_default_branch, tag_for_branch

        with self.session.write(install=True) as brain, translated():
            info = brain.create_branch(name, at=start, checkout=switch)
            align_default_branch(brain, self.config)
            current = brain.current_branch()
        return {
            "name": info.name,
            "tag": tag_for_branch(self.config, info.name),
            "snapshot": str(info.snapshot),
            "current": current == info.name,
        }

    def branch_switch(
        self,
        name: str,
        *,
        create: bool = False,
        track: bool = True,
        reference: str | None = None,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """Switch branches from synchronous code."""
        return self.remote._run(
            self.branch_switch_async(
                name,
                create=create,
                track=track,
                reference=reference,
                username=username,
                token=token,
                anonymous=anonymous,
                insecure=insecure,
                local=local,
            )
        )

    async def branch_switch_async(
        self,
        name: str,
        *,
        create: bool = False,
        track: bool = True,
        reference: str | None = None,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """
        Make another branch current.

        Three outcomes, in order of preference. A branch held locally is checked out: the current head is recorded
        under its own branch first, so nothing unpublished is lost. A branch that does not exist is created at the
        current head when ``create`` is set. Otherwise, when ``track`` is set, the branch is pulled from the
        registry's ``br.<name>`` tag, which installs it as a new local branch -- how a teammate's branch is picked up.

        Args:
            name (str): The branch.
            create (bool): Create it at the current head when it does not exist.
            track (bool): Pull it from the registry when it exists only there.
            reference (str | None): The repository to track from.
            username (str | None): Registry account.
            token (str | None): Registry token.
            anonymous (bool): Ignore stored credentials.
            insecure (bool | None): Allow plain HTTP.
            local (Path | None): A local OCI layout to track from instead of a registry.

        Returns:
            dict[str, Any]: The branch now current, how it got there (``switched``, ``created`` or ``tracked``),
            its head, and the pull's report when it was tracked.

        Raises:
            VitruvioError: ``BRANCH_NOT_FOUND`` when it exists nowhere this was allowed to look, ``RECONCILE_OPEN``
                when a reconciliation is in progress.
        """
        from boltzmann.branches import validate_branch_name
        from boltzmann.exceptions import BranchNotFoundError

        from vitruvio.runtime.branches import align_default_branch, tag_for_branch

        brain = self.session.brain(Capability.INSPECT)
        with translated():
            validate_branch_name(name)
            held = {info.name for info in brain.branches()}

        if name in held:
            with self.session.write(install=True) as writer, translated():
                snapshot = writer.checkout(name)
            return {"branch": name, "outcome": "switched", "snapshot": str(snapshot.digest), "pull": None}

        if create:
            with self.session.write(install=True) as writer, translated():
                writer.create_branch(name, checkout=True)
                align_default_branch(writer, self.config)
                snapshot = writer.snapshot()
            return {"branch": name, "outcome": "created", "snapshot": str(snapshot.digest), "pull": None}

        if not track:
            raise translate(
                BranchNotFoundError(f"no branch named {name!r}; pass --create to start one at the current head")
            )

        from vitruvio.runtime.ops.install import InstallOps

        tag = tag_for_branch(self.config, name)
        try:
            pulled = await InstallOps(self.session).pull_async(
                reference,
                tag=tag,
                username=username,
                token=token,
                anonymous=anonymous,
                insecure=insecure,
                local=local,
            )
        except VitruvioError as error:
            if error.code != "REFERENCE_NOT_FOUND":
                raise
            # The branch that was asked for is what is missing, whether or not the repository exists. Reported as
            # such, so a caller handling `BRANCH_NOT_FOUND` recognises it, rather than with the first-push hint the
            # registry's own absence carries.
            raise translate(
                BranchNotFoundError(
                    f"no branch named {name!r} here, and the registry has no {tag!r} to track it from; "
                    "pass --create to start one at the current head"
                )
            ) from error
        return {
            "branch": self.branch_current(),
            "outcome": "tracked",
            "snapshot": pulled["snapshot"]["digest"],
            "pull": pulled,
        }

    def branch_delete(self, name: str, *, force: bool = False) -> dict[str, Any]:
        """
        Delete a local branch. Its snapshots stay until a prune finds nothing else naming them.

        The registry's ``br.<name>`` tag is left alone: OCI deletes by manifest digest, and that manifest may be the
        one another tag names.

        Args:
            name (str): The branch.
            force (bool): Delete it even when its head is the only name for that work and was never pushed.

        Returns:
            dict[str, Any]: The deleted branch and the head it was at.
        """
        with self.session.write(install=True) as brain, translated():
            info = next((entry for entry in brain.branches() if entry.name == name), None)
            brain.delete_branch(name, force=force)
        return {"deleted": name, "snapshot": str(info.snapshot) if info is not None else None}

    def branch_merge(
        self,
        name: str,
        *,
        strategy: ReconcileStrategy | str | None = None,
        reason: str | None = None,
        fast_forward: Literal["auto", "only", "never"] = "auto",
    ) -> dict[str, Any]:
        """
        Join another branch into the current one.

        Nothing happens when it is already contained. When the current branch has not moved since, the head
        fast-forwards to it and no snapshot is written. Otherwise the two diverged and this is a reconciliation,
        which needs a strategy -- given here or declared by the brain -- because all three land the same blocks and
        differ only in who stays on record as author. A reconciliation that does not apply cleanly halts, exactly
        as ``reconcile`` does, and ``reconcile resolve`` / ``continue`` finish it.

        Args:
            name (str): The branch to join in.
            strategy (ReconcileStrategy | str | None): ``merge``, ``rebase`` or ``squash``. Defaults to the brain's
                declared strategy, and to none at all.
            reason (str | None): Why, recorded with a reconciliation.
            fast_forward (Literal["auto", "only", "never"]): ``only`` refuses anything but a fast-forward; ``never`` records a
                reconciliation even where a fast-forward was possible.

        Returns:
            dict[str, Any]: ``outcome`` (``up-to-date``, ``fast-forward``, ``reconciled`` or ``halted``), the head
            afterwards, and the reconciliation's report when one ran.

        Raises:
            VitruvioError: ``USAGE`` when the branches diverged and no strategy is known.
        """
        from vitruvio.runtime.ops.reconcile import ReconcileOps
        from vitruvio.runtime.reconcile_result import halted_result, serialize_committed

        brain = self.session.brain(Capability.INSPECT)
        with translated():
            current = brain.current_branch()
            heads = {info.name: info.snapshot for info in brain.branches()}
        if name == current or name not in heads:
            # Let the SDK say which, in its words, through the same mapping every other refusal takes.
            with self.session.write(install=True) as writer, translated():
                writer.join(name)

        with translated():
            contained = heads[name] in brain.reachable_history()
        if contained:
            # Asked before anything else, because it is the answer under every `fast_forward`: `--no-ff` asks for a
            # reconciliation where a fast-forward was possible, and there is nothing to reconcile with a history this
            # one already holds.
            return {"branch": name, "into": current, "outcome": "up-to-date", "snapshot": str(brain.snapshot().digest)}

        if fast_forward != "never":
            # The cheap path first, on the view that rebuilds nothing: a fast-forward is a pointer move.
            try:
                with self.session.write(install=True) as writer, translated():
                    joined = writer.join(name, fast_forward="only")
                return {"branch": name, "into": current, "outcome": joined.outcome, "snapshot": str(joined.snapshot)}
            except VitruvioError as error:
                if error.code != "BRANCH_REFUSED" or fast_forward == "only":
                    raise

        declared = strategy if strategy is not None else self.config.reconcile_strategy
        if declared is None:
            raise UsageError(
                f"{current!r} and {name!r} diverged, so merging them is a reconciliation, and no strategy is declared",
                hint=(
                    "pass --strategy merge|rebase|squash, or declare `reconcile` for this brain. A merge keeps the "
                    "branch's snapshots and their signatures; rebase and squash re-sign the work as yours"
                ),
            )
        chosen = coerce_strategy(declared)
        with self.session.write() as writer:
            ReconcileOps._require_none_open(writer, name)
            try:
                with translated():
                    joined = writer.join(name, chosen, reason, fast_forward=fast_forward)
            except VitruvioError as error:
                if error.code != "RECONCILE_OPEN":
                    raise
                status = ReconcileOps(self.session)._status_payload(writer)
                if not status["open"]:  # pragma: no cover - the SDK just persisted it before raising
                    raise RuntimeError("a halted reconciliation did not leave an open status") from error
                return {
                    "branch": name,
                    "into": current,
                    "outcome": "halted",
                    "snapshot": None,
                    "reconciliation": halted_result(str(chosen), status),
                }
        return {
            "branch": name,
            "into": current,
            "outcome": joined.outcome,
            "snapshot": str(joined.snapshot),
            "reconciliation": serialize_committed(joined.reconciliation) if joined.reconciliation else None,
        }


__all__ = ["BranchOps"]
