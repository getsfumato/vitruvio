"""Governed genesis, explicit signing and consumer-side pinning."""

from __future__ import annotations

from pathlib import Path

import pytest
from boltzmann.authenticity import SshPublicKey, rfc4253_signature

from vitruvio.kernel import UsageError
from vitruvio.runtime import BrainService

ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")


class Party:
    """A deterministic test key implementing the signing seam."""

    def __init__(self) -> None:
        self._private = ed25519.Ed25519PrivateKey.from_private_bytes(bytes([0x42]) * 32)
        line = self._private.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        self.public_key = SshPublicKey.parse(line.decode("ascii"))

    def sign_blob(self, data: bytes) -> bytes:
        return rfc4253_signature("ssh-ed25519", self._private.sign(data))


def test_governed_writes_are_signed_only_when_requested(
    config: object, source_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    party = Party()
    monkeypatch.setattr("boltzmann.authenticity.AgentSigner", lambda _key: party)
    monkeypatch.setattr("vitruvio.runtime.ops.authenticity.AgentSigner", lambda _key: party)
    service = BrainService(config)  # type: ignore[arg-type]

    created = service.init(governed=True, sign_with=[party.public_key.fingerprint])
    assert created["governed"] is True
    assert service.auth_status()["state"] == "authorized"

    service.register(source_file, media_type="text/markdown")
    assert service.auth_status()["state"] == "unsigned"
    record = service.auth_sign(party.public_key.fingerprint)
    assert record["key"] == party.public_key.fingerprint
    assert service.auth_status()["state"] == "authorized"
    attribution = service.auth_attribution()
    assert attribution["snapshot"] == record["snapshot"]
    assert attribution["complete"] is True
    assert "tester@example.com" in attribution["verified"]

    pin = service.auth_pin()
    assert pin["source"] == "first_use"
    assert service.auth_status()["pinned"] is True


def test_an_ungoverned_brain_reports_integrity_separately(service: BrainService) -> None:
    status = service.auth_status()
    assert status["integrity"] is True
    assert status["state"] == "unsigned"
    assert status["trust_root"] is None


def test_invalid_auth_enums_are_usage_errors(service: BrainService) -> None:
    with pytest.raises(UsageError, match="signing scope"):
        service.auth_sign("SHA256:not-needed", scopes=["not-a-scope"])
    with pytest.raises(UsageError, match="pin source"):
        service.auth_pin(source="not-a-source")


def test_sign_with_is_refused_for_an_ungoverned_genesis(config: object) -> None:
    with pytest.raises(UsageError, match="ungoverned"):
        BrainService(config).init(sign_with=["SHA256:not-needed"])  # type: ignore[arg-type]


def test_historical_auth_status_verifies_the_requested_snapshot(
    config: object, source_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    party = Party()
    monkeypatch.setattr("boltzmann.authenticity.AgentSigner", lambda _key: party)
    service = BrainService(config)  # type: ignore[arg-type]
    genesis = service.init(governed=True, sign_with=[party.public_key.fingerprint])["snapshot"]["digest"]
    service.register(source_file, media_type="text/markdown")

    from boltzmann.brain import Brain

    monkeypatch.setattr(Brain, "verify", lambda brain: str(brain.snapshot().digest) == genesis)
    assert service.auth_status(snapshot=genesis)["integrity"] is True
    assert service.auth_status()["integrity"] is False
