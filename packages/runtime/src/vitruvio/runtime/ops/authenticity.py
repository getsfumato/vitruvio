"""SSH authenticity and trust-root governance as explicit operations."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from typing import Any

from boltzmann.authenticity import (
    AgentSigner,
    PinSource,
    RotationPlan,
    Scope,
    SignatureRecord,
    SnapshotStance,
    TrustRoot,
)
from boltzmann.identity.digest import OciDigest

from vitruvio.kernel import ResolvedConfig, UsageError
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession


class AuthenticityOps:
    """Authenticate, sign, pin and govern a brain without handling private keys."""

    def __init__(self, session: BrainSession) -> None:
        self.session = session

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration."""
        return self.session.config

    @staticmethod
    def _signers(keys: Sequence[str]) -> list[AgentSigner]:
        return [AgentSigner(key) for key in keys]

    @staticmethod
    def _records(values: Sequence[Mapping[str, Any]]) -> list[SignatureRecord]:
        return [SignatureRecord.model_validate(value) for value in values]

    @staticmethod
    def _plan(value: Mapping[str, Any]) -> RotationPlan:
        try:
            document = base64.b64decode(str(value["document_b64"]), validate=True)
            return RotationPlan(
                document=document,
                digest=OciDigest.parse(value["digest"]),
                quorum_required=int(value["quorum_required"]),
                eligible=tuple(str(item) for item in value["eligible"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise UsageError("rotation plan is malformed or its document is not valid base64") from error

    def auth_keys(self) -> dict[str, Any]:
        """List Ed25519 public keys currently offered by the SSH agent."""
        with translated():
            keys = AgentSigner.identities()
        return {
            "keys": [
                {"fingerprint": key.fingerprint, "key_type": key.key_type, "public_key": key.authorized_key}
                for key in keys
            ]
        }

    def auth_status(self, *, snapshot: str | None = None, offered: bool = False) -> dict[str, Any]:
        """Report integrity and authenticity separately for one snapshot."""
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            requested = OciDigest.parse(snapshot) if snapshot else None
            report = brain.authenticate(
                requested,
                policy=self.config.project.authenticity.build(),
                stance=SnapshotStance.OFFERED if offered else SnapshotStance.HEAD,
            )
            payload = report.model_dump(mode="json")
            payload["state"] = report.state.value
            if requested is None or requested == brain.snapshot().digest:
                payload["integrity"] = brain.verify()
            else:
                from boltzmann.brain import Brain
                from boltzmann.module.snapshot import Snapshot

                historical = Snapshot.from_document(brain.store.get_bytes(requested))
                payload["integrity"] = Brain(
                    brain.store,
                    actor=brain.actor,
                    snapshot=historical,
                    assisted_by=brain.assisted_by,
                    policy=brain.policy,
                ).verify()
        return payload

    def auth_sign(
        self,
        key: str,
        *,
        snapshot: str | None = None,
        scopes: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Explicitly sign a snapshot with an Ed25519 key held by the SSH agent."""
        brain = self.session.brain(Capability.INSPECT)
        try:
            selected_scopes = [Scope(item) for item in scopes] if scopes else None
        except ValueError as error:
            permitted = ", ".join(item.value for item in Scope)
            raise UsageError(f"unknown signing scope {error.args[0]!r}; expected one of: {permitted}") from error
        with translated():
            record = brain.sign(
                AgentSigner(key),
                snapshot=OciDigest.parse(snapshot) if snapshot else None,
                scopes=selected_scopes,
            )
        return record.model_dump(mode="json")

    def auth_pin(self, *, trust_root: str | None = None, source: str | None = None) -> dict[str, Any]:
        """Anchor a trust-root digest in consumer-side state."""
        brain = self.session.brain(Capability.INSPECT)
        try:
            selected_source = PinSource(source) if source else None
        except ValueError as error:
            permitted = ", ".join(item.value for item in PinSource)
            raise UsageError(f"unknown pin source {source!r}; expected one of: {permitted}") from error
        with translated():
            pin = brain.pin(
                OciDigest.parse(trust_root) if trust_root else None,
                selected_source,
            )
        return pin.model_dump(mode="json")

    def auth_attribution(self) -> dict[str, Any]:
        """Compare actors introduced by the head with subjects vouched by valid signatures."""
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            report = brain.audit_attribution()
        return {
            "snapshot": str(report.snapshot),
            "verified": list(report.verified),
            "asserted": list(report.asserted),
            "legacy": list(report.legacy),
            "evidence_gaps": list(report.evidence_gaps),
            "complete": report.is_complete,
            "fully_vouched": report.is_fully_vouched,
            "detail": report.detail,
        }

    def auth_plan_rotation(self, trust_root: Mapping[str, Any]) -> dict[str, Any]:
        """Build the exact trust-root revision document a distributed quorum must sign."""
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            plan = brain.plan_rotate(TrustRoot.model_validate(trust_root))
        return {
            "document_b64": base64.b64encode(plan.document).decode("ascii"),
            "digest": str(plan.digest),
            "quorum_required": plan.quorum_required,
            "eligible": list(plan.eligible),
        }

    def auth_countersign(self, plan: Mapping[str, Any], key: str) -> dict[str, Any]:
        """Inspect and countersign the exact document carried by a rotation plan."""
        brain = self.session.brain(Capability.INSPECT)
        typed = self._plan(plan)
        with translated():
            record = brain.countersign(typed.document, AgentSigner(key))
        return record.model_dump(mode="json")

    def auth_rotate(
        self,
        *,
        trust_root: Mapping[str, Any] | None = None,
        plan: Mapping[str, Any] | None = None,
        sign_with: Sequence[str] = (),
        records: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Commit a local or distributed trust-root rotation after its quorum is met."""
        if (trust_root is None) == (plan is None):
            raise UsageError("auth rotate needs exactly one of trust_root or plan")
        with self.session.write() as brain, translated():
            result = brain.rotate(
                trust_root=TrustRoot.model_validate(trust_root) if trust_root is not None else None,
                plan=self._plan(plan) if plan is not None else None,
                signers=self._signers(sign_with),
                records=self._records(records),
            )
        return result.model_dump(mode="json")

    def auth_revoke(
        self,
        key: str,
        *,
        sign_with: Sequence[str] = (),
        records: Sequence[Mapping[str, Any]] = (),
        retired_from: int | None = None,
        compromised_from: str | None = None,
    ) -> dict[str, Any]:
        """Retire a key prospectively or withdraw it from a compromised snapshot onward."""
        if retired_from is not None and compromised_from is not None:
            raise UsageError("retirement and compromise are opposite operations; choose one")
        with self.session.write() as brain, translated():
            result = brain.revoke(
                key,
                signers=self._signers(sign_with),
                records=self._records(records),
                retired_from=retired_from,
                compromised_from=OciDigest.parse(compromised_from) if compromised_from else None,
            )
        return result.model_dump(mode="json")


__all__ = ["AuthenticityOps"]
