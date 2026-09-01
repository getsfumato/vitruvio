"""One-way recreation of legacy brains under the current protocol."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from boltzmann.blocks.canonical import CanonicalBlock
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.blocks.provenance import (
    NormalizationRecord,
    Producer,
    ProducerKind,
    ProvenanceBlock,
    ProvenanceBlockV2,
    RegistrationRecord,
)
from boltzmann.catalog import declaration_from_block
from boltzmann.catalog_models import (
    ClassDeclaration,
    HierarchyDeclaration,
    PlacementDeclaration,
    SchemeDeclaration,
)
from boltzmann.identity.digest import BlockId
from boltzmann.identity.principal import actor_id_form
from boltzmann.ingest.proposer import Candidate
from boltzmann.ingest.register import RegistrationRequest
from boltzmann.ingest.validation import ValidatedCandidate, ValidationReport, ValidationStatus
from boltzmann.module.ledger import Ledger

from vitruvio.kernel import Origin, ResolvedConfig, UsageError, VitruvioError
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.ops.lifecycle import LifecycleOps
from vitruvio.runtime.session import BrainSession

DERIVED_TYPES = (MemoryType.SEMANTIC, MemoryType.EPISODIC, MemoryType.PROCEDURAL)


class MigrationOps:
    """Plan and recreate a legacy brain without mutating its history."""

    def __init__(self, session: BrainSession) -> None:
        self.session = session

    @property
    def config(self) -> ResolvedConfig:
        """The source brain's resolved configuration."""
        return self.session.config

    def _inventory(self) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
        source = self.session.brain(Capability.INSPECT)
        modules = source.modules()
        ledger = Ledger.of(modules)
        problems: list[dict[str, str]] = []
        excluded: list[dict[str, str]] = []
        counts: dict[str, int] = {}
        catalog = 0
        legacy_actors: set[str] = set()
        reproducible: set[BlockId] = set()
        derived: dict[BlockId, tuple[MemoryType, list[BlockId]]] = {}

        provenance = modules.get(MemoryType.PROVENANCE)
        if provenance is not None:
            for identity in provenance.block_ids:
                if not provenance.store.is_resolvable(identity):
                    continue
                block = provenance.get(identity)
                record = block.record if isinstance(block, ProvenanceBlock | ProvenanceBlockV2) else None
                actor = getattr(record, "actor", None)
                if actor is not None and actor_id_form(actor.id) is None:
                    legacy_actors.add(actor.id)

        for memory_type, module in modules.items():
            if memory_type is MemoryType.PROVENANCE:
                continue
            counts[memory_type.value] = 0
            for identity in module.block_ids:
                if not ledger.is_accessible(identity):
                    excluded.append({"block": str(identity), "reason": "not accessible in the source head"})
                    continue
                if not module.store.is_resolvable(identity):
                    problems.append({"block": str(identity), "reason": "content is not resolvable"})
                    continue
                block = module.get(identity)
                if any(not source.store.is_resolvable(digest) for digest in block.content_digests):
                    problems.append({"block": str(identity), "reason": "one or more content blobs are not resolvable"})
                    continue
                if memory_type is MemoryType.SEMANTIC and declaration_from_block(block) is not None:
                    catalog += 1
                    continue
                if memory_type is MemoryType.CANONICAL:
                    if not isinstance(block, CanonicalBlock):
                        problems.append({"block": str(identity), "reason": "canonical block has an unknown schema"})
                        continue
                    reproducible.add(identity)
                    counts[memory_type.value] += 1
                    continue
                if memory_type not in DERIVED_TYPES:
                    problems.append({"block": str(identity), "reason": f"unsupported memory type {memory_type.value}"})
                    continue
                evidence = list(ledger.evidence.get(identity) or getattr(block, "evidence", None) or ())
                if not evidence:
                    problems.append(
                        {"block": str(identity), "reason": "derived block has no reproducible evidence edge"}
                    )
                    continue
                derived[identity] = (memory_type, evidence)

        pending = dict(derived)
        while pending:
            ready = [identity for identity, (_, evidence) in pending.items() if set(evidence).issubset(reproducible)]
            if not ready:
                break
            for identity in ready:
                memory_type, _evidence = pending.pop(identity)
                reproducible.add(identity)
                counts[memory_type.value] += 1
        for identity, (_memory_type, evidence) in pending.items():
            missing = ", ".join(str(item) for item in evidence if item not in reproducible)
            problems.append(
                {
                    "block": str(identity),
                    "reason": f"evidence dependencies are not reproducible: {missing}",
                }
            )
        return {
            "source_snapshot": str(source.snapshot().digest),
            "verified": source.verify(),
            "counts": counts,
            "catalog_declarations": catalog,
            "legacy_actors": sorted(legacy_actors),
            "problems": problems,
            "excluded": excluded,
        }

    def plan_migration(self, destination: Path) -> dict[str, Any]:
        """Inspect the current accessible state and report what recreation would copy."""
        destination = destination.expanduser().resolve()
        if destination.exists():
            raise UsageError(
                f"migration destination {destination} already exists",
                hint="choose a new path; migration never writes in place or reuses a directory",
            )
        inventory = self._inventory()
        return {
            "schema": "vitruvio.migration-report/v1",
            "source": str(self.config.brain),
            "destination": str(destination),
            "source_preserved": True,
            "history_preserved": False,
            "provenance_preserved": False,
            **inventory,
        }

    @staticmethod
    def _provenance_metadata(
        source: Any,
    ) -> tuple[dict[BlockId, RegistrationRecord], dict[BlockId, NormalizationRecord]]:
        registrations: dict[BlockId, RegistrationRecord] = {}
        normalizations: dict[BlockId, NormalizationRecord] = {}
        module = source.modules().get(MemoryType.PROVENANCE)
        if module is None:
            return registrations, normalizations
        for identity in module.block_ids:
            if not module.store.is_resolvable(identity):
                continue
            block = module.get(identity)
            if not isinstance(block, ProvenanceBlock | ProvenanceBlockV2):
                continue
            record = block.record
            if isinstance(record, RegistrationRecord):
                registrations[record.block] = record
            elif isinstance(record, NormalizationRecord):
                normalizations[record.block] = record
        return registrations, normalizations

    @staticmethod
    def _copy_content(source: Any, destination: Any, block: Any) -> None:
        for digest in block.content_digests:
            destination.store.put_bytes(source.store.get_bytes(digest))

    def migrate(  # noqa: PLR0912, PLR0915
        self,
        destination: Path,
        *,
        governed: bool = True,
        trust_root: Mapping[str, Any] | None = None,
        sign_with: Sequence[str] = (),
        govern_quorum: int = 1,
        allow_partial: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Recreate current accessible knowledge in a new brain and leave the source untouched."""
        plan = self.plan_migration(destination)
        if not plan["verified"]:
            raise VitruvioError("the source brain fails integrity verification; migration is refused")
        if dry_run:
            return {**plan, "dry_run": True, "completed": False}
        if plan["problems"] and not allow_partial:
            raise UsageError(
                f"migration found {len(plan['problems'])} non-reproducible block(s)",
                hint="inspect the dry-run report; repair the source or pass --allow-partial deliberately",
            )
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self.session.brain(Capability.INSPECT)
        source_modules = source.modules()
        ledger = Ledger.of(source_modules)
        registrations, normalizations = self._provenance_metadata(source)
        skipped = [*plan["problems"], *plan["excluded"]]
        problem_ids = {item["block"] for item in plan["problems"]}
        preserved: list[str] = []
        migrated: set[BlockId] = set()

        with (
            tempfile.TemporaryDirectory(prefix=".vitruvio-migrate-", dir=destination.parent) as temporary,
            ExitStack() as writes,
        ):
            work = Path(temporary) / "brain"
            destination_config = self.config.model_copy(
                update={"brain": work, "brain_origin": Origin.FLAG, "brain_name": None, "config_file": None}
            )
            destination_session = BrainSession(destination_config)
            LifecycleOps(destination_session).init(
                governed=governed,
                trust_root=trust_root,
                sign_with=sign_with,
                govern_quorum=govern_quorum,
                labels={"vitruvio.migrated-from": plan["source_snapshot"]},
                write_config=False,
            )
            target = writes.enter_context(destination_session.write())

            canonical = source_modules.get(MemoryType.CANONICAL)
            if canonical is not None:
                for identity in canonical.block_ids:
                    if not ledger.is_accessible(identity) or not canonical.store.is_resolvable(identity):
                        continue
                    if str(identity) in problem_ids:
                        continue
                    block = canonical.get(identity)
                    if not isinstance(block, CanonicalBlock):
                        skipped.append({"block": str(identity), "reason": "canonical block has an unknown schema"})
                        continue
                    metadata = registrations.get(identity)
                    normalization = normalizations.get(identity)
                    try:
                        result = target.register(
                            source.store.get_bytes(block.blob),
                            RegistrationRequest(
                                media_type=block.media_type,
                                actor=destination_config.actor(),
                                origin=metadata.origin if metadata else f"migration:{plan['source_snapshot']}",
                                license=metadata.license if metadata else None,
                                retention_policy=metadata.retention_policy if metadata else None,
                                normalize_with=normalization.pipeline if normalization else None,
                            ),
                        )
                    except Exception as error:
                        skipped.append({"block": str(identity), "reason": f"registration failed: {error}"})
                        if not allow_partial:
                            raise UsageError(f"canonical block {identity} could not be recreated: {error}") from error
                        continue
                    if result.block_id != identity:
                        reason = (
                            f"recreated as {result.block_id}; normalization no longer reproduces the original identity"
                        )
                        skipped.append({"block": str(identity), "reason": reason})
                        if not allow_partial:
                            raise UsageError(f"canonical block {identity} {reason}")
                        continue
                    migrated.add(identity)
                    preserved.append(str(identity))

            pending: dict[BlockId, tuple[MemoryType, Any, list[BlockId]]] = {}
            catalog_declarations: list[Any] = []
            for memory_type in DERIVED_TYPES:
                module = source_modules.get(memory_type)
                if module is None:
                    continue
                for identity in module.block_ids:
                    if not ledger.is_accessible(identity) or not module.store.is_resolvable(identity):
                        continue
                    if str(identity) in problem_ids:
                        continue
                    block = module.get(identity)
                    declaration = declaration_from_block(block) if memory_type is MemoryType.SEMANTIC else None
                    if declaration is not None:
                        catalog_declarations.append(declaration)
                        continue
                    evidence = list(ledger.evidence.get(identity) or getattr(block, "evidence", None) or ())
                    if not evidence:
                        continue
                    try:
                        self._copy_content(source, target, block)
                    except Exception as error:
                        skipped.append({"block": str(identity), "reason": f"content copy failed: {error}"})
                        if not allow_partial:
                            raise UsageError(f"content for {identity} could not be copied: {error}") from error
                        continue
                    pending[identity] = (memory_type, block, evidence)

            while pending:
                ready = [item for item in pending.items() if set(item[1][2]).issubset(migrated)]
                if not ready:
                    unresolved = [
                        {"block": str(identity), "reason": "one or more evidence dependencies were not migrated"}
                        for identity in pending
                    ]
                    skipped.extend(unresolved)
                    if not allow_partial:
                        raise UsageError(
                            f"{len(unresolved)} derived block(s) depend on evidence that could not be migrated"
                        )
                    break
                results = []
                for identity, (memory_type, block, evidence) in ready:
                    candidate = Candidate(memory_type=memory_type, payload=block.payload(), evidence=evidence)
                    results.append(
                        ValidatedCandidate(candidate=candidate, status=ValidationStatus.VALIDATED, block=block)
                    )
                    del pending[identity]
                report = ValidationReport(
                    results=results,
                    producer=Producer(kind=ProducerKind.ACTOR, id=destination_config.actor().id),
                    task_id=f"migration:{plan['source_snapshot']}",
                    checks=["vitruvio:migration/current-state"],
                )
                committed = target.commit(report)
                expected = {identity for identity, _detail in ready}
                actual = set(committed.committed)
                if actual != expected:
                    missing = ", ".join(str(item) for item in sorted(expected - actual, key=str)) or "none"
                    unexpected = ", ".join(str(item) for item in sorted(actual - expected, key=str)) or "none"
                    raise VitruvioError(
                        f"derived recreation changed protocol identities (missing: {missing}; unexpected: {unexpected})"
                    )
                for identity in actual:
                    migrated.add(identity)
                    preserved.append(str(identity))

            eligible_catalog: list[Any] = []
            for declaration in catalog_declarations:
                if isinstance(declaration, PlacementDeclaration) and declaration.source not in migrated:
                    skipped.append(
                        {
                            "block": str(declaration.block_id),
                            "reason": f"catalog placement source {declaration.source} was not migrated",
                        }
                    )
                    continue
                eligible_catalog.append(declaration)
            ordered_catalog = [
                declaration
                for kind in (SchemeDeclaration, ClassDeclaration, HierarchyDeclaration, PlacementDeclaration)
                for declaration in eligible_catalog
                if isinstance(declaration, kind)
            ]
            if ordered_catalog:
                classified = target.classify(ordered_catalog)
                rejected = [item for item in classified.verdicts if item.status is not ValidationStatus.VALIDATED]
                if rejected and not allow_partial:
                    raise UsageError(f"{len(rejected)} catalog declaration(s) could not be recreated")
                for verdict in classified.verdicts:
                    if verdict.status is ValidationStatus.VALIDATED:
                        migrated.add(verdict.block_id)
                        preserved.append(str(verdict.block_id))
                    else:
                        detail = "; ".join(issue.detail for issue in verdict.issues) or verdict.status.value
                        skipped.append({"block": str(verdict.block_id), "reason": f"catalog rejected: {detail}"})

            final_signatures = []
            if sign_with:
                from boltzmann.authenticity import AgentSigner

                for key in sign_with:
                    final_signatures.append(target.sign(AgentSigner(key)).model_dump(mode="json"))

            final_snapshot = str(target.snapshot().digest)
            authenticity = target.authenticate().state.value
            writes.close()
            work.replace(destination)

        return {
            **plan,
            "dry_run": False,
            "completed": True,
            "destination_snapshot": final_snapshot,
            "preserved_ids": sorted(preserved),
            "preserved_id_count": len(preserved),
            "skipped": skipped,
            "partial": bool(skipped),
            "authenticity": authenticity,
            "final_signatures": final_signatures,
        }


__all__ = ["MigrationOps"]
