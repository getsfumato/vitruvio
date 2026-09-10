"""Governed genesis, explicit signing and consumer-side pinning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from boltzmann.authenticity import SshPublicKey, rfc4253_signature

from vitruvio.ingest.evidence import Evidence
from vitruvio.kernel import UsageError, VitruvioError
from vitruvio.runtime import BrainService

ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")


class Party:
    """A deterministic test key implementing the signing seam."""

    def __init__(self, seed: int = 0x42) -> None:
        self._private = ed25519.Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32)
        line = self._private.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        self.public_key = SshPublicKey.parse(line.decode("ascii"))

    def sign_blob(self, data: bytes) -> bytes:
        return rfc4253_signature("ssh-ed25519", self._private.sign(data))


def governed(config: object, monkeypatch: pytest.MonkeyPatch, *others: Party) -> tuple[BrainService, Party]:
    """Open a genuinely governed brain while replacing only the process-external SSH agent."""
    party = Party()
    # Keyed by fingerprint, so a second authority signs through the same seam `--sign-with` reaches.
    parties = {each.public_key.fingerprint: each for each in (party, *others)}
    monkeypatch.setattr("boltzmann.authenticity.AgentSigner", parties.__getitem__)
    monkeypatch.setattr("vitruvio.runtime.ops.authenticity.AgentSigner", parties.__getitem__)
    service = BrainService(config)  # type: ignore[arg-type]
    created = service.init(governed=True, sign_with=[party.public_key.fingerprint])
    assert created["governed"] is True
    return service, party


def test_a_governed_write_remains_unsigned_until_explicitly_signed(
    config: object, source_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, party = governed(config, monkeypatch)
    assert service.auth_status()["state"] == "authorized"

    service.register(Evidence.from_path(source_file, media_type="text/markdown"))
    assert service.auth_status()["state"] == "unsigned"
    record = service.auth_sign(party.public_key.fingerprint)
    assert record["key"] == party.public_key.fingerprint
    assert service.auth_status()["state"] == "authorized"


def test_signed_creation_provenance_verifies_the_configured_actor(
    config: object, source_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, party = governed(config, monkeypatch)
    service.register(Evidence.from_path(source_file, media_type="text/markdown"))
    record = service.auth_sign(party.public_key.fingerprint)
    attribution = service.auth_attribution()
    assert attribution["snapshot"] == record["snapshot"]
    assert attribution["complete"] is True
    assert "tester@example.com" in attribution["verified"]
    row = service.blocks("canonical")["rows"][0]
    assert row["authorship"]["claims"][0]["actor_verified"] is True
    assert row["authorship"]["claims"][0]["snapshot_authenticity"] == "authorized"


def test_a_consumer_pin_is_reported_beside_the_governance_inventory(
    config: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _party = governed(config, monkeypatch)
    pin = service.auth_pin()
    assert pin["source"] == "first_use"
    assert service.auth_status()["pinned"] is True

    root = service.auth_trust_root()
    assert root["governed"] is True
    assert root["pinned"] is True
    assert root["trust_root"]["revision"] == 1
    assert root["keys"][0]["subject"] == "tester@example.com"
    assert set(root["keys"][0]["scopes"]) == {
        "commit",
        "ingest",
        "drop:canonical",
        "redact",
        "govern",
        "propose",
    }


def test_an_ungoverned_brain_reports_integrity_separately(service: BrainService) -> None:
    status = service.auth_status()
    assert status["integrity"] is True
    assert status["state"] == "unsigned"
    assert status["trust_root"] is None
    root = service.auth_trust_root()
    assert root["governed"] is False
    assert root["keys"] == []
    assert root["pin"] is None


def test_invalid_auth_enums_are_usage_errors(service: BrainService) -> None:
    with pytest.raises(UsageError, match="signing scope"):
        service.auth_sign("SHA256:not-needed", scopes=["not-a-scope"])
    with pytest.raises(UsageError, match="pin source"):
        service.auth_pin(source="not-a-source")


def test_sign_with_is_refused_for_an_ungoverned_genesis(config: object) -> None:
    with pytest.raises(UsageError, match="ungoverned"):
        BrainService(config).init(sign_with=["SHA256:not-needed"])  # type: ignore[arg-type]


def test_malformed_genesis_trust_root_is_a_stable_caller_error(config: object) -> None:
    with pytest.raises(VitruvioError) as genesis:
        BrainService(config).init(governed=True, trust_root={"revision": 1})  # type: ignore[arg-type]
    assert genesis.value.code == "USAGE"


def test_malformed_rotation_trust_root_is_a_stable_caller_error(service: BrainService) -> None:
    with pytest.raises(VitruvioError) as rotation:
        service.auth_plan_rotation({"revision": 2})
    assert rotation.value.code == "USAGE"


def test_historical_auth_status_verifies_the_requested_snapshot(
    config: object, source_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    party = Party()
    monkeypatch.setattr("boltzmann.authenticity.AgentSigner", lambda _key: party)
    service = BrainService(config)  # type: ignore[arg-type]
    genesis = service.init(governed=True, sign_with=[party.public_key.fingerprint])["snapshot"]["digest"]
    service.register(Evidence.from_path(source_file, media_type="text/markdown"))

    from boltzmann.brain import Brain

    monkeypatch.setattr(Brain, "verify", lambda brain: str(brain.snapshot().digest) == genesis)
    assert service.auth_status(snapshot=genesis)["integrity"] is True
    assert service.auth_status()["integrity"] is False


def _admitting(root: dict[str, Any], newcomer: Party) -> dict[str, Any]:
    """Revision 2 over the root in force: the same keys, the newcomer beside them, and a quorum that needs both."""
    keys = [
        {"key": entry["public_key"], "subject": entry["subject"], "scopes": entry["scopes"], "since": entry["since"]}
        for entry in root["keys"]
    ]
    keys.append(
        {
            "key": newcomer.public_key.authorized_key,
            "subject": "second@example.com",
            "scopes": ["govern", "commit"],
            "since": 2,
        }
    )
    return {"revision": 2, "govern_quorum": 2, "keys": keys}


def test_a_second_authority_is_admitted_by_plan_countersign_and_rotate(
    config: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The distributed flow, with the countersignature standing in for one that arrived from another machine."""
    second = Party(0x43)
    service, party = governed(config, monkeypatch, second)

    plan = service.auth_plan_rotation(_admitting(service.auth_trust_root(), second))
    assert plan["quorum_required"] == 1
    assert plan["eligible"] == [party.public_key.fingerprint]

    record = service.auth_countersign(plan, party.public_key.fingerprint)
    assert record["snapshot"] == plan["digest"]

    rotated = service.auth_rotate(plan=plan, records=[record])
    assert (rotated["revision"], rotated["quorum_met"]) == (2, 1)
    root = service.auth_trust_root()
    assert root["trust_root"]["govern_quorum"] == 2
    assert [entry["active"] for entry in root["keys"]] == [True, True]
    assert service.auth_status()["state"] == "authorized"


def test_retiring_a_key_needs_the_quorum_the_revised_root_set(config: object, monkeypatch: pytest.MonkeyPatch) -> None:
    second = Party(0x43)
    service, party = governed(config, monkeypatch, second)
    plan = service.auth_plan_rotation(_admitting(service.auth_trust_root(), second))
    service.auth_rotate(plan=plan, records=[service.auth_countersign(plan, party.public_key.fingerprint)])

    with pytest.raises(VitruvioError) as alone:
        service.auth_revoke(second.public_key.fingerprint, sign_with=[party.public_key.fingerprint])
    assert alone.value.code == "AUTHENTICITY_FAILED"

    revoked = service.auth_revoke(
        second.public_key.fingerprint, sign_with=[party.public_key.fingerprint, second.public_key.fingerprint]
    )
    assert (revoked["revision"], revoked["quorum_required"], revoked["quorum_met"]) == (3, 2, 2)
    keys = service.auth_trust_root()["keys"]
    retired = next(entry for entry in keys if entry["fingerprint"] == second.public_key.fingerprint)
    assert (retired["active"], retired["retired_from"]) == (False, 3)
    assert service.auth_status()["state"] == "authorized"
