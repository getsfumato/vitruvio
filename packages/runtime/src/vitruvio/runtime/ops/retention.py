"""Removing and demoting knowledge, and the asymmetry that shapes all five mechanisms.

Every operation here is *shaped* by one asymmetry: a drop is cheap to state and expensive to undo, and its cost
is not local -- excluding one block excludes everything derived from it. So `plan_drop` exists as its own
operation, and `drop` runs the same plan again rather than trusting one it was handed. Two calls, on purpose:
between the plan a caller saw and the drop it authorised, the composition may have moved.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from boltzmann.blocks.memory_type import MemoryType

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime import wire
from vitruvio.runtime.coerce import block_id
from vitruvio.runtime.coerce import memory_type as coerce_memory_type
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession


class RetentionOps:
    """Retention, as operations."""

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

    def plan_drop(
        self,
        blocks: Iterable[str],
        *,
        memory_type: str,
        reason: str = "requested",
        rederive_against: str | None = None,
    ) -> dict[str, Any]:
        """
        What a drop would take with it, without writing anything.

        Args:
            blocks (Iterable[str]): The blocks to exclude.
            memory_type (str): Which module they belong to.
            reason (str): Why. Recorded in provenance by the drop, and required by the protocol -- an unexplained
                removal is a removal nobody can audit.
            rederive_against (str | None): Newer evidence the dependents could be re-derived from instead of dropped.

        Returns:
            dict[str, Any]: The cascade, its size, and whether it needs review.
        """
        with self.session.write() as brain, translated():
            return wire.cascade(brain.plan_drop(self._drop_request(blocks, memory_type, reason, rederive_against)))

    def drop(
        self,
        blocks: Iterable[str],
        *,
        memory_type: str,
        reason: str = "requested",
        rederive_against: str | None = None,
    ) -> dict[str, Any]:
        """
        Exclude blocks from a module, cascading through provenance.

        The cascade is returned alongside the result, not because a caller needs it to interpret the outcome, but
        because it is the record of what this drop actually took -- and the plan a caller saw beforehand was computed
        against a composition that may since have moved.

        Args:
            blocks (Iterable[str]): The blocks to exclude.
            memory_type (str): Which module.
            reason (str): Why.
            rederive_against (str | None): Newer evidence to re-derive dependents from.

        Returns:
            dict[str, Any]: What was dropped, the new roots, and the cascade it followed.
        """
        request = self._drop_request(blocks, memory_type, reason, rederive_against)
        with self.session.write() as brain, translated():
            plan = wire.cascade(brain.plan_drop(request))
            return {**wire.dropped(brain.drop(request)), "cascade": plan}

    def drop_by_producer(
        self,
        producer_id: str,
        *,
        kind: str = "model",
        version: str | None = None,
        memory_types: Iterable[str] | None = None,
        reason: str = "producer invalidated",
    ) -> dict[str, Any]:
        """
        Drop everything one producer derived.

        The operation a bad model version needs. It works only because the producer was recorded at commit time,
        which is why a proposer names itself and why ``vitruvio`` records the producer rather than trusting the
        candidate set's claim about it.

        Args:
            producer_id (str): The model name, pipeline name or batch id.
            kind (str): ``model``, ``pipeline``, ``batch`` or ``actor``.
            version (str | None): A specific version, so one bad release can be dropped without the others.
            memory_types (Iterable[str] | None): Which modules to sweep. Defaults to every derived module.
            reason (str): Why.

        Returns:
            dict[str, Any]: What was dropped, and the new roots.
        """
        from boltzmann.blocks.provenance import Producer, ProducerKind
        from boltzmann.retention.requests import ProducerDropRequest

        types = (
            [coerce_memory_type(item) for item in memory_types]
            if memory_types
            else [MemoryType.SEMANTIC, MemoryType.PROCEDURAL, MemoryType.EPISODIC]
        )
        with self.session.write() as brain, translated():
            request = ProducerDropRequest(
                producer=Producer(kind=ProducerKind(kind), id=producer_id, version=version),
                memory_types=types,
                actor=self.config.actor(),
                reason=reason,
                policy_name=self.config.project.policy.profile.value,
            )
            return wire.dropped(brain.drop_by_producer(request))

    def supersede(self, block: str, *, superseded: str, memory_type: str, reason: str | None = None) -> dict[str, Any]:
        """
        Record that one block takes precedence over another, without changing membership.

        The superseded block stays in the composition and keeps proving into the root; only accessibility changes.
        It is the *only* removal path the episodic module has, because episodic memory is append-only by protocol --
        what happened cannot stop having happened.

        Args:
            block (str): The block that takes precedence.
            superseded (str): The block it replaces.
            memory_type (str): Which module both belong to.
            reason (str | None): Why.

        Returns:
            dict[str, Any]: The new version and the record written.
        """
        with self.session.write() as brain, translated():
            result = brain.supersede(
                block_id(block), block_id(superseded), coerce_memory_type(memory_type), reason=reason
            )
            return {**wire.supersession(result), "block": block, "superseded": superseded}

    def demote(self, block: str, *, memory_type: str, reason: str | None = None) -> dict[str, Any]:
        """
        Lower a block's retrieval priority without removing it.

        Recorded in the ledger rather than on the block: a block is immutable, so accessibility as a *field* would
        change the block id and make a demoted block a different block.

        Args:
            block (str): The block to demote.
            memory_type (str): Which module.
            reason (str | None): Why.

        Returns:
            dict[str, Any]: The new version and the record written.
        """
        with self.session.write() as brain, translated():
            return {
                **wire.supersession(brain.demote(block_id(block), coerce_memory_type(memory_type), reason=reason)),
                "block": block,
            }

    def prune(self, *, apply: bool = False) -> dict[str, Any]:
        """
        Reclaim blobs unreachable from every retained root.

        Pruning decides nothing about what to forget -- a drop already did that. It reclaims what no retained
        composition still needs, which is what makes it irreversible and yet harmless.

        Args:
            apply (bool): Actually delete. Defaults to reporting, matching the SDK, because the safe direction is the
                one you can repeat.

        Returns:
            dict[str, Any]: What would be, or was, reclaimed.
        """
        with self.session.write() as brain, translated():
            return {**wire.prune(brain.prune(dry_run=not apply)), "applied": apply}

    def redact(self, block: str, *, memory_type: str, reason: str) -> dict[str, Any]:
        """
        Destroy a block's bytes while a retained root still names it.

        Not the cleanup path. Wrong or obsolete knowledge is *dropped*; redaction is for personal data, credentials
        or licensed material that has to disappear even from retained history. It punches a hole in a composition
        that still names the block: membership still verifies, and reconstruction of that one block is forfeited --
        which ``inspect resolvability`` reports as tombstoned rather than missing, so a lawful erasure is never
        mistaken for a corrupt store.

        Args:
            block (str): The block to redact.
            memory_type (str): Which module.
            reason (str): Why. Not optional here, and the protocol agrees: an unexplained destruction of evidence is
                indistinguishable from an attack on the record.

        Returns:
            dict[str, Any]: What was destroyed, and what was held back because another block still names it.
        """
        with self.session.write() as brain, translated():
            return {
                **wire.redaction(brain.redact(block_id(block), coerce_memory_type(memory_type), reason)),
                "block": block,
            }

    def policy(self) -> dict[str, Any]:
        """
        The retention policy in force, and what it permits.

        Returns:
            dict[str, Any]: The profile, the policy document, and which mechanisms it allows.
        """
        policy = self.config.policy()
        return {
            "profile": self.config.project.policy.profile.value,
            "policy": policy.model_dump(mode="json"),
            "config_file": str(self.config.config_file) if self.config.config_file else None,
        }

    def _drop_request(self, blocks: Iterable[str], memory_type: str, reason: str, rederive_against: str | None) -> Any:
        """
        Build a drop request. One place, so ``plan_drop`` and ``drop`` cannot disagree about what was asked.

        Args:
            blocks (Iterable[str]): The blocks.
            memory_type (str): Which module.
            reason (str): Why.
            rederive_against (str | None): Newer evidence.

        Returns:
            Any: The ``DropRequest``.
        """
        from boltzmann.retention.requests import DropRequest

        return DropRequest(
            blocks=[block_id(item) for item in blocks],
            memory_type=coerce_memory_type(memory_type),
            actor=self.config.actor(),
            reason=reason,
            policy_name=self.config.project.policy.profile.value,
            rederive_against=block_id(rederive_against) if rederive_against else None,
        )
