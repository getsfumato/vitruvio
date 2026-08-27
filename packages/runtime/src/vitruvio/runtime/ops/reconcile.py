"""Joining two histories, and the loop somebody walks when it does not join cleanly.

Reconciliation inverts the intuition anyone brings from version control, and the operations here are shaped by
the inversion. In Git the three strategies differ in how the result is *computed*: a rebase replays patches and
can land on a tree a merge would not have produced. Here a snapshot is a complete statement of composition
rather than a patch, so there is nothing to replay sequentially and **all three produce the same set of
blocks**. What differs is only the lineage recorded, and therefore who stays on record as the author of the
incoming work.

That is why :meth:`ReconcileOps.reconcile` takes ``strategy`` as a required argument with no default. The SDK
refuses to supply one, and so does this: a default would be vitruvio deciding whose name comes off the work.
The brain's configuration may declare one, which is a person having stated it; nothing here invents it.

A conflict is a *validation* failure rather than a differencing failure. The structural reconciliation is set
arithmetic over immutable blocks and is automatic; its result goes through the ingestion gate, and what did not
apply comes back as verdicts. So there is no merged state with markers in it to hand-edit -- there is a list of
questions, and :meth:`ReconcileOps.resolve` answers them one at a time.

**A halt is not a failure.** :class:`~boltzmann.exceptions.ReconciliationHaltedError` means the operation is
asking something. It is caught and reported rather than translated, because a caller told "error" has nowhere
to put the answer.
"""

from __future__ import annotations

from typing import Any

from boltzmann.exceptions import ReconciliationError
from boltzmann.reconcile import ReconcileRequest, ResolutionKind

from vitruvio.kernel import ReconcileStrategy, ResolvedConfig, UsageError, VitruvioError
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import block_id, snapshot_digest
from vitruvio.runtime.coerce import strategy as coerce_strategy
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.reconcile_result import (
    ReconcileCommittedResult,
    ReconcileOperationResult,
    ReconcilePlanResult,
    ReconcileStatusEnvelope,
    ReconcileStatusResult,
    halted_result,
    open_status,
    serialize_committed,
    serialize_plan,
    serialize_status,
)
from vitruvio.runtime.session import BrainSession


class ReconcileOps:
    """Reconciliation, as operations."""

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

    def declared_strategy(self) -> ReconcileStrategy | None:
        """
        The strategy this brain declares, if it declares one.

        Returns:
            ReconcileStrategy | None: The declaration, or ``None`` when nobody has made one. ``None`` is a real
            answer: it means a fetch reconciles nothing, rather than reconciling under a guess.
        """
        return self.config.reconcile_strategy

    def contains(self, theirs: str) -> bool:
        """
        Whether this brain's history already contains another snapshot.

        Asked over ``reachable_history`` and not over the plan. ``ReconcilePlan.is_noop`` looks like the same
        question and is not: it reports that the arithmetic against the ancestor *the search found* changes
        nothing, and after a merge that search does not settle on the other side's head -- so a plan computed
        against an already-merged history still shows their blocks as additions and reads as not a no-op.

        Believing the plan on this cost a repeated ``dist fetch`` a fresh snapshot every time: each one a
        reconciliation of a history already held, moving the head and adding a version to ``brain history`` for
        nothing. Reachability is the question actually being asked -- the same one a fast-forward check asks --
        and it is a local walk over documents already here.

        Args:
            theirs (str): The other history's head.

        Returns:
            bool: Whether it is already in this brain's history, through any parent.
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            return snapshot_digest(theirs) in brain.reachable_history()

    def plan(self, theirs: str, *, ancestor: str | None = None) -> ReconcilePlanResult:
        """
        What joining another history would produce, without writing anything.

        Strategy-independent on purpose: the composition is identical under all three, so one plan can report
        what each of them would cost and let the choice be made with that in hand rather than afterwards.

        Args:
            theirs (str): The other history's head, already held locally -- which is what ``dist fetch`` is for.
            ancestor (str | None): The snapshot to reconcile against, when the caller knows it. Defaults to
                searching for the nearest one the two histories share.

        Returns:
            dict[str, Any]: The plan, every incoming block already judged, and the cost of each strategy.
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            found = brain.plan_reconcile(
                snapshot_digest(theirs),
                snapshot_digest(ancestor) if ancestor else None,
            )
        result = serialize_plan(found)
        result["theirs"] = theirs
        return result

    def reconcile(
        self,
        theirs: str,
        *,
        strategy: ReconcileStrategy | str,
        reason: str,
        ancestor: str | None = None,
    ) -> ReconcileOperationResult:
        """
        Join another history into this one, recording it the way the strategy asks.

        Args:
            theirs (str): The other history's head.
            strategy (ReconcileStrategy | str): How to record it. **Required.** The three land the same blocks
                and differ in attribution, so there is no defensible default.
            reason (str): Why. Required for the same reason a removal requires one: an unattributed
                reconciliation is not auditable.
            ancestor (str | None): The snapshot to reconcile against, when known.

        Returns:
            dict[str, Any]: What was committed when it committed, or the open questions when it halted --
            ``halted`` distinguishes the two, and a halt is the operation asking rather than failing.
        """
        chosen = coerce_strategy(strategy)
        with self.session.write() as brain:
            self._require_none_open(brain, theirs)
            request = ReconcileRequest(
                theirs=snapshot_digest(theirs),
                strategy=chosen,
                actor=self.config.actor(),
                reason=reason,
                ancestor=snapshot_digest(ancestor) if ancestor else None,
            )
            try:
                with translated():
                    result = brain.reconcile(request)
            except VitruvioError as error:
                if error.code != "RECONCILE_OPEN":
                    raise
                status = self._status_payload(brain)
                if not status["open"]:  # pragma: no cover - the SDK just persisted it before raising
                    raise RuntimeError("a halted reconciliation did not leave an open status") from error
                return halted_result(str(chosen), status)
            return serialize_committed(result)

    def status(self) -> ReconcileStatusEnvelope:
        """
        The reconciliation being resolved, if there is one.

        The plan inside it is recomputed rather than remembered. Storing verdicts would mean acting on a
        judgment that may no longer hold; what is persisted is what a person decided.

        Returns:
            dict[str, Any]: ``open`` is false when nothing is in progress, which is not an error -- asking is
            how a caller finds out.
        """
        brain = self.session.brain(Capability.INSPECT)
        return self._status_payload(brain)

    def resolve(self, block: str, *, kind: str, prefer: str | None = None) -> ReconcileStatusResult:
        """
        Decide one of the questions a halted reconciliation is holding.

        Three answers, and one of them is deliberately not always available. ``admit`` is offered for a
        contradiction -- holding two claims that disagree is a state the protocol permits, and which one is
        right is not a question it answers -- and refused for a rejection. A derived block whose evidence is
        absent from the composition breaks R1, and nothing downstream would catch it: verification recomputes
        hashes and compositions, not citations across modules. The fix for that is an ordinary commit that
        supplies the evidence, which belongs outside the reconciliation.

        Args:
            block (str): Which incoming block to decide.
            kind (str): ``admit``, ``reject`` or ``prefer``.
            prefer (str | None): The winning successor, for a precedence question.

        Returns:
            dict[str, Any]: The state after recording it.

        Raises:
            UsageError: If ``kind`` names no resolution, or a ``prefer`` arrives without a winner.
        """
        try:
            decision = ResolutionKind(kind)
        except ValueError as error:
            permitted = ", ".join(item.value for item in ResolutionKind)
            raise UsageError(f"{kind!r} is not a resolution; expected one of: {permitted}") from error
        if decision is ResolutionKind.PREFER and prefer is None:
            raise UsageError(
                "a precedence decision has to name the winner",
                hint="pass the successor that should take precedence",
            )

        with self.session.write() as brain, translated():
            status = brain.reconcile_resolve(
                block_id(block),
                decision,
                block_id(prefer) if prefer else None,
            )
        return serialize_status(status)

    def accept_removals(self) -> ReconcileStatusResult:
        """
        State that the work this reconciliation removes may go.

        One answer rather than one per block, because there is no per-block choice to offer: exclusion wins by
        construction in the set arithmetic, so a block the other history dropped cannot be kept by choosing to
        keep it. Deliberately re-admitting it is an ordinary commit, outside this.

        Returns:
            dict[str, Any]: The state after recording it.
        """
        with self.session.write() as brain, translated():
            status = brain.reconcile_accept_removals()
        return serialize_status(status)

    def continue_(self) -> ReconcileCommittedResult:
        """
        Conclude the reconciliation now that its questions are answered.

        Refuses while any candidate is undecided: the protocol declined to decide it, and committing would
        decide it on the operator's behalf.

        Returns:
            dict[str, Any]: What was committed.
        """
        with self.session.write() as brain, translated():
            result = brain.reconcile_continue()
        return serialize_committed(result)

    def abort(self) -> dict[str, Any]:
        """
        Abandon the reconciliation being resolved.

        Nothing is undone, because a halted reconciliation never wrote a composition or moved the pointer. What
        goes is the record of the decisions taken so far.

        Returns:
            dict[str, Any]: What was abandoned, so the report can name it rather than say "done".
        """
        with self.session.write() as brain, translated():
            try:
                status = brain.reconcile_status()
            except ReconciliationError:
                brain.reconcile_abort()
                return {"aborted": True, "theirs": None, "strategy": None, "decisions": None, "stale": True}

            if status is None:
                raise UsageError(
                    "no reconciliation is in progress, so there is nothing to abandon",
                    hint="`vitruvio reconcile status` reports whether one is open",
                )
            brain.reconcile_abort()
        return {
            "aborted": True,
            "theirs": str(status.state.theirs),
            "strategy": str(status.state.strategy),
            "decisions": len(status.state.resolutions),
            "stale": False,
        }

    def tree(self, theirs: str | None = None, *, ancestor: str | None = None) -> dict[str, Any]:
        """
        Where two histories parted, and what each has added since.

        Reads the local side from the brain and the other side from the plan, so it answers the question a
        reconciliation is about -- what diverged -- rather than the one ``brain history`` answers, which is what
        this brain is on its own.

        Args:
            theirs (str | None): The other history's head. Defaults to the one an open reconciliation names.
            ancestor (str | None): The shared snapshot, when known.

        Returns:
            dict[str, Any]: The two heads, the ancestor, and the local first-parent chain to reach it.

        Raises:
            UsageError: If no history was named and none is open to take one from.
        """
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            open_state = brain.reconcile_status()
        target = theirs or (str(open_state.state.theirs) if open_state else None)
        if target is None:
            raise UsageError(
                "no history to compare against, and no reconciliation is open to take one from",
                hint="pass the other head, or run `vitruvio dist fetch` to bring one",
            )

        plan = self.plan(target, ancestor=ancestor)
        with translated():
            chain = [str(digest) for digest in brain.ancestry()]
        return {
            "ours": chain[0] if chain else None,
            "theirs": target,
            "ancestor": plan["ancestor"],
            "ancestry": chain,
            "reconciling": open_state is not None,
            "collapsed": plan["collapsed"],
            "replayable": plan["replayable"],
            "modules": plan["modules"],
            "is_noop": plan["is_noop"],
            "is_clean": plan["is_clean"],
        }

    @staticmethod
    def _require_none_open(brain: Any, theirs: str) -> None:
        """
        Refuse to start a reconciliation while another is unresolved, before the SDK has to.

        `Brain.reconcile` already refuses, but it refuses by raising the class a *halt* raises, so the caller
        cannot tell the two apart afterwards -- and the difference is total: one means the history you named is
        waiting on your decisions, the other means somebody else's is, and yours never started.

        Asked here, where the answer is unambiguous. A stale state -- one whose head moved underneath it --
        counts as open too, and is named as such, because the way out of it is `abort` rather than a retry.

        Args:
            brain (Any): The opened brain.
            theirs (str): The history the caller asked to reconcile, for the message.

        Raises:
            VitruvioError: If a reconciliation is already open.
        """
        # Inside `translated()` for the reason `abort` documents: everything `reconcile_status` can raise that
        # is not a `ReconciliationError` -- it reads blocks -- would otherwise escape the mapping table.
        with translated():
            try:
                status = brain.reconcile_status()
            except ReconciliationError as error:
                raise UsageError(
                    f"cannot reconcile {theirs}: a reconciliation is already open, and its recorded state no "
                    "longer matches this brain",
                    hint="`vitruvio reconcile abort` abandons it; nothing it recorded was ever written",
                ) from error
        if status is None:
            return
        raise UsageError(
            f"cannot reconcile {theirs}: the reconciliation of {status.state.theirs} is still unresolved",
            hint=(
                "`vitruvio reconcile status` lists what is open, `continue` concludes it, and `abort` "
                "abandons it -- one reconciliation at a time"
            ),
        )

    def _status_payload(self, brain: Any) -> ReconcileStatusEnvelope:
        """
        One shape for "where does the reconciliation stand", used by three callers.

        Shared so that ``status``, a halted ``reconcile`` and the interactive resolver cannot come to describe
        the same state differently -- the phrasing is the contract here, and three copies of it drift.

        Args:
            brain (Any): The opened brain.

        Returns:
            dict[str, Any]: ``open``, and the status when there is one.
        """
        with translated():
            status = brain.reconcile_status()
        if status is None:
            return {"open": False}
        return open_status(serialize_status(status))


__all__ = ["ReconcileOps"]
