"""The HTTP adapters round-trip through the real OCI layout registry."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import fakeredis
import fakeredis.aioredis
import httpx
import pytest

from vitruvio.http import brain, ingest, processing, query, worker
from vitruvio.http.models import BrainConfig, BrainRef, IngestionRequest, SearchRequest
from vitruvio.ingest.evidence import Evidence
from vitruvio.kernel import ConfigError, SourceError, UsageError, VitruvioError, resolve
from vitruvio.runtime import BrainService

ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")


@pytest.fixture
def published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, str, BrainService]]:
    registry = tmp_path / "registry"
    registry.mkdir()
    monkeypatch.setenv("VITRUVIO_HTTP_LOCAL_REGISTRY", str(registry))
    source = BrainService(resolve(brain=tmp_path / "source", actor_id="source@example.org", require_layout=False))
    source.init(write_config=False)
    source.register(
        Evidence.from_bytes(b"# Original\nFirst section.\n", origin="test:original", media_type="text/markdown")
    )
    reference = "example/brain"
    source.push(reference, tag="v1", local=registry)
    yield registry, reference, source
    query.cache.close()


class Party:
    """A deterministic test key implementing the signing seam."""

    def __init__(self, seed: int = 0x42) -> None:
        from boltzmann.authenticity import SshPublicKey

        self._private = ed25519.Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32)
        line = self._private.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        self.public_key = SshPublicKey.parse(line.decode("ascii"))

    def sign_blob(self, data: bytes) -> bytes:
        from boltzmann.authenticity import rfc4253_signature

        return rfc4253_signature("ssh-ed25519", self._private.sign(data))


class Agent:
    """Stands in for the SSH agent: signs through the seam `--sign-with` reaches, and lists what it holds."""

    def __init__(self, *parties: Party) -> None:
        self.parties = {party.public_key.fingerprint: party for party in parties}

    def __call__(self, key: str) -> Party:
        return self.parties[key]

    def identities(self) -> list[Any]:
        return [party.public_key for party in self.parties.values()]


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, str, BrainService, Party]]:
    """A governed brain whose signed head is published; the source stays open for signing and republishing."""
    registry = tmp_path / "registry"
    registry.mkdir()
    monkeypatch.setenv("VITRUVIO_HTTP_LOCAL_REGISTRY", str(registry))
    party = Party()
    agent = Agent(party)
    monkeypatch.setattr("boltzmann.authenticity.AgentSigner", agent)
    monkeypatch.setattr("vitruvio.runtime.ops.authenticity.AgentSigner", agent)
    source = BrainService(resolve(brain=tmp_path / "source", actor_id="source@example.org", require_layout=False))
    source.init(governed=True, sign_with=[party.public_key.fingerprint], write_config=False)
    source.register(
        Evidence.from_bytes(b"# Original\nFirst section.\n", origin="test:original", media_type="text/markdown")
    )
    yield registry, "example/governed", source, party
    query.cache.close()


def _consumer(tmp_path: Path, name: str) -> BrainService:
    consumer = BrainService(resolve(brain=tmp_path / name, actor_id="reader@example.org", require_layout=False))
    consumer.init(write_config=False)
    return consumer


def _document_request(reference: str, *, actor: str = "writer@example.org", **extra: Any) -> IngestionRequest:
    return IngestionRequest.model_validate(
        {
            "brain": {"repository": reference, "tag": "v1"},
            "config": {"actor": {"id": actor}},
            "assisted_by": [],
            "items": [
                {
                    "kind": "document",
                    "uri": "https://example.org/document.md",
                    "origin": "test:new",
                    "media_type": "text/markdown",
                }
            ],
            **extra,
        }
    )


def _queue(client: Any, request: IngestionRequest, job_id: str) -> tuple[str, str, dict[str, str]]:
    """Queue one job the way the ingest API does, and read its delivery the way a worker does."""
    key = f"{ingest.JOB_PREFIX}{job_id}"
    now = datetime.now(UTC).isoformat()
    client.hset(
        key,
        mapping={
            "id": job_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "request": request.model_dump_json(),
        },
    )
    client.xadd(ingest.STREAM, {"job_id": job_id})
    consumer = worker.Worker(client)
    batches: Any = client.xreadgroup(worker.GROUP, consumer.name, {ingest.STREAM: ">"})
    message_id, fields = batches[0][1][0]
    return key, message_id, fields


@pytest.mark.asyncio
async def test_query_loads_caller_selected_oci(published: tuple[Path, str, BrainService]) -> None:
    _, reference, _ = published
    request = SearchRequest.model_validate(
        {"brain": {"repository": reference, "tag": "v1"}, "text": "Original", "limit": 5}
    )
    result = await query.search(request)
    assert result["ok"] is True
    assert result["data"]["manifest_digest"].startswith("sha256:")
    assert result["data"]["evidence"]["matches"]
    assert (await query.search(request))["data"]["manifest_digest"] == result["data"]["manifest_digest"]

    transport = httpx.ASGITransport(app=query.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/search", json=request.model_dump())
    assert response.status_code == 200
    assert response.json()["data"]["evidence"]["matches"]


@pytest.mark.asyncio
async def test_query_refreshes_a_moved_tag(published: tuple[Path, str, BrainService], tmp_path: Path) -> None:
    registry, reference, _ = published
    request = SearchRequest.model_validate({"brain": {"repository": reference, "tag": "v1"}, "text": "Second"})
    first = await query.search(request)

    updater = BrainService(resolve(brain=tmp_path / "updater", actor_id="updater@example.org", require_layout=False))
    updater.init(write_config=False)
    await updater.pull_async(reference, tag="v1", local=registry)
    updater.register(
        Evidence.from_bytes(b"# Second\nAnother section.\n", origin="test:second", media_type="text/markdown")
    )
    await updater.push_async(reference, tag="v1", local=registry)

    second = await query.search(request)
    assert second["data"]["manifest_digest"] != first["data"]["manifest_digest"]
    assert second["data"]["evidence"]["matches"]


@pytest.mark.asyncio
async def test_a_registry_outside_the_allowlist_is_refused_before_any_credential_is_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VITRUVIO_HTTP_LOCAL_REGISTRY", raising=False)
    request = SearchRequest.model_validate({"brain": {"repository": "ghcr.io/team/brain", "tag": "v1"}, "text": "x"})
    with pytest.raises(brain.DestinationRefusedError):
        brain.make_service(tmp_path / "unlisted", request.brain, request.config)

    monkeypatch.setenv("VITRUVIO_HTTP_ALLOWED_REGISTRIES", "GHCR.io, registry.example:5000")
    brain.make_service(tmp_path / "listed", request.brain, request.config)
    # Docker Hub's shorthand is judged by the host it actually goes to.
    with pytest.raises(brain.DestinationRefusedError):
        brain.make_service(tmp_path / "hub", BrainRef(repository="team/brain", tag="v1"), request.config)
    with pytest.raises(UsageError):
        brain.make_service(tmp_path / "tagged", BrainRef(repository="ghcr.io/team/brain:v1", tag="v1"), request.config)

    transport = httpx.ASGITransport(app=query.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        refused = await client.post("/v1/search", json={"brain": {"repository": "evil.example/x/y", "tag": "v1"}})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "DESTINATION_REFUSED"


def test_an_embedding_endpoint_outside_the_allowlist_is_refused(
    published: tuple[Path, str, BrainService], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, reference, _ = published
    ref = BrainRef(repository=reference, tag="v1")

    def config(base_url: str | None) -> BrainConfig:
        spec: dict[str, Any] = {"provider": "openai", "model": "text-embedding-3-small"}
        if base_url is not None:
            spec["base_url"] = base_url
        return BrainConfig.model_validate({"embedding": {"text": spec}})

    with pytest.raises(brain.DestinationRefusedError):
        brain.make_service(tmp_path / "redirected", ref, config("https://attacker.example/v1"))
    # No endpoint named means the provider's own, which is where the server's key for it belongs.
    brain.make_service(tmp_path / "default", ref, config(None))

    monkeypatch.setenv("VITRUVIO_HTTP_ALLOWED_EMBEDDING_URLS", "https://Gateway.example/v1/")
    brain.make_service(tmp_path / "listed", ref, config("https://gateway.example/v1"))
    with pytest.raises(brain.DestinationRefusedError):
        brain.make_service(tmp_path / "sibling", ref, config("https://gateway.example/v2"))
    with pytest.raises(UsageError):
        brain.make_service(tmp_path / "userinfo", ref, config("https://user:secret@gateway.example/v1"))


@pytest.mark.asyncio
async def test_nullable_configuration_is_accepted_and_malformed_configuration_is_the_callers_error(
    published: tuple[Path, str, BrainService], tmp_path: Path
) -> None:
    _, reference, _ = published
    ref = BrainRef(repository=reference, tag="v1")
    service = brain.make_service(
        tmp_path / "nullable", ref, BrainConfig.model_validate({"actor": {"id": "writer@example.org", "name": None}})
    )
    assert service.config.project.actor.id == "writer@example.org"
    assert service.config.project.actor.name is None

    with pytest.raises(ConfigError):
        brain.make_service(
            tmp_path / "unknown", ref, BrainConfig.model_validate({"actor": {"id": "w@x.org", "bogus": 1}})
        )
    with pytest.raises(ConfigError):
        brain.make_service(
            tmp_path / "null-item", ref, BrainConfig.model_validate({"policy": {"redactable_media_types": [None]}})
        )

    transport = httpx.ASGITransport(app=query.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/search",
            json={"brain": {"repository": reference, "tag": "v1"}, "config": {"actor": {"id": "w@x.org", "bogus": 1}}},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CONFIG_INVALID"


def test_install_refuses_an_artifact_other_than_the_resolved_manifest(
    published: tuple[Path, str, BrainService], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tag moves A -> B -> A around the pull: B is what arrived, and A is what a second resolve would bless."""
    registry, reference, source = published
    ref = BrainRef(repository=reference, tag="v1")
    config = BrainConfig()
    resolved = brain.resolve_remote(brain.make_service(tmp_path / "probe", ref, config), ref)

    updater = BrainService(resolve(brain=tmp_path / "updater", actor_id="updater@example.org", require_layout=False))
    updater.init(write_config=False)
    updater.pull(reference, tag="v1", local=registry)
    updater.register(Evidence.from_bytes(b"# B\nMoved.\n", origin="test:b", media_type="text/markdown"))
    updater.push(reference, tag="v1", local=registry)

    real = BrainService.pull

    def pull_then_restore(self: BrainService, *args: Any, **kwargs: Any) -> dict[str, Any]:
        pulled = real(self, *args, **kwargs)
        source.push(reference, tag="v1", local=registry, force=True)
        return pulled

    monkeypatch.setattr(BrainService, "pull", pull_then_restore)
    with pytest.raises(brain.TagMovedError):
        brain.install(brain.make_service(tmp_path / "install", ref, config), ref, resolved.digest)


@pytest.mark.asyncio
async def test_registry_io_does_not_stall_the_event_loop(
    published: tuple[Path, str, BrainService], monkeypatch: pytest.MonkeyPatch
) -> None:
    from boltzmann.distribution.local import LocalLayoutRegistry

    _, reference, _ = published
    real = LocalLayoutRegistry.resolve

    async def blocking(self: Any, reference: str, tag: str) -> Any:
        time.sleep(0.25)  # The SDK's transport blocks behind its async signature; this makes that visible.
        return await real(self, reference, tag)

    monkeypatch.setattr(LocalLayoutRegistry, "resolve", blocking)
    request = SearchRequest.model_validate({"brain": {"repository": reference, "tag": "v1"}, "text": "Original"})
    ticks: list[float] = []

    async def ticker() -> None:
        while len(ticks) < 20:
            await asyncio.sleep(0.01)
            ticks.append(time.perf_counter())

    _, result = await asyncio.gather(ticker(), query.search(request))
    assert result["data"]["evidence"]["matches"]
    assert max(b - a for a, b in pairwise(ticks)) < 0.15


@pytest.mark.asyncio
async def test_a_slow_install_does_not_hold_up_a_brain_already_cached(
    published: tuple[Path, str, BrainService], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, reference, _ = published
    other = BrainService(resolve(brain=tmp_path / "other", actor_id="other@example.org", require_layout=False))
    other.init(write_config=False)
    other.register(Evidence.from_bytes(b"# Other\nElsewhere.\n", origin="test:other", media_type="text/markdown"))
    other.push("example/other", tag="v1", local=registry)

    cached = SearchRequest.model_validate({"brain": {"repository": reference, "tag": "v1"}, "text": "Original"})
    await query.search(cached)

    gate = threading.Event()
    real = query.install

    def slow(service: BrainService, ref: BrainRef, digest: str) -> brain.InstalledBrain:
        if ref.repository == "example/other":
            assert gate.wait(10)
        return real(service, ref, digest)

    monkeypatch.setattr(query, "install", slow)
    slow_request = SearchRequest.model_validate(
        {"brain": {"repository": "example/other", "tag": "v1"}, "text": "Other"}
    )
    slow_task = asyncio.create_task(query.search(slow_request))
    await asyncio.sleep(0.1)
    hit = await asyncio.wait_for(query.search(cached), timeout=5)
    assert hit["data"]["evidence"]["matches"]
    assert not slow_task.done()
    gate.set()
    assert (await slow_task)["data"]["evidence"]["matches"]


@pytest.mark.asyncio
async def test_a_cached_governed_head_sees_signatures_published_after_it(
    governed: tuple[Path, str, BrainService, Party],
) -> None:
    registry, reference, source, party = governed
    source.push(reference, tag="v1", local=registry)  # The head is unsigned: the register happened after genesis.
    request = SearchRequest.model_validate({"brain": {"repository": reference, "tag": "v1"}, "text": "Original"})
    first = await query.search(request)
    assert first["data"]["authenticity"]["state"] == "unsigned"
    assert any("unsigned" in warning for warning in first["warnings"])

    source.auth_sign(party.public_key.fingerprint)
    source.push(reference, tag="v1", local=registry)  # Same manifest; the signature travels as a referrer.

    second = await query.search(request)
    assert second["data"]["manifest_digest"] == first["data"]["manifest_digest"]
    assert second["data"]["authenticity"]["state"] == "authorized"
    assert second["data"]["attribution"]["fully_vouched"] is True
    assert (await query.search(request))["data"]["authenticity"]["state"] == "authorized"


@pytest.mark.asyncio
async def test_ingest_job_publishes_a_new_tag(
    published: tuple[Path, str, BrainService], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry, reference, _ = published

    async def document(_uri: str, _limit: int) -> bytes:
        return b"# New knowledge\nA distinct section.\n"

    monkeypatch.setattr(processing, "read_document", document)
    request = _document_request(reference)
    prepared: list[tuple[dict[str, Any], str]] = []
    progress: list[dict[str, Any]] = []
    result = await processing.process_job(
        request,
        "testjob",
        lambda value, digest: prepared.append((json.loads(json.dumps(value)), digest)),
        lambda _index, item: progress.append(item),
    )
    assert result["status"] == "completed"
    assert result["output"]["tag"] == "ingest-testjob"
    assert prepared[0][1] == result["output"]["digest"]
    assert progress[0]["ok"] is True

    recovery = worker.Worker(fakeredis.FakeRedis(decode_responses=True))
    recovered = await recovery._recover(
        {"prepared": json.dumps(prepared[0][0]), "prepared_digest": prepared[0][1]}, request
    )
    assert recovered is not None
    assert recovered["status"] == "completed"

    consumer = _consumer(tmp_path, "consumer")
    consumer.pull(reference, tag="ingest-testjob", local=registry)
    assert consumer.verify()["verified"] is True
    assert consumer.search("New knowledge")["matches"]


@pytest.mark.asyncio
async def test_ingest_continues_after_a_rejected_item(
    published: tuple[Path, str, BrainService], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, reference, _ = published

    async def document(_uri: str, _limit: int) -> bytes:
        return b"# Good\nAccepted despite an earlier failure.\n"

    monkeypatch.setattr(processing, "read_document", document)
    request = IngestionRequest.model_validate(
        {
            "brain": {"repository": reference, "tag": "v1"},
            "config": {"actor": {"id": "writer@example.org"}},
            "assisted_by": [],
            "items": [
                {"kind": "candidates", "task": {}, "candidates": {}},
                {
                    "kind": "document",
                    "uri": "https://example.org/good.md",
                    "origin": "test:good",
                    "media_type": "text/markdown",
                },
            ],
        }
    )
    result = await processing.process_job(request, "partialjob", lambda _result, _digest: None, lambda _i, _r: None)
    assert result["status"] == "partial"
    assert result["failed"] == 1
    assert result["succeeded"] == 1
    assert result["output"]["tag"] == "ingest-partialjob"


@pytest.mark.asyncio
async def test_recovery_completes_a_governed_publication_interrupted_before_its_signatures(
    governed: tuple[Path, str, BrainService, Party], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``push`` uploads the manifest, then the signature referrers. A worker that dies in between leaves a head a
    consumer sees as unsigned, and recovery used to report it as fully vouched because the manifest matched."""
    from boltzmann.distribution.local import LocalLayoutRegistry

    registry, reference, source, party = governed
    source.auth_sign(party.public_key.fingerprint)
    source.push(reference, tag="v1", local=registry)

    async def document(_uri: str, _limit: int) -> bytes:
        return b"# New knowledge\nA distinct section.\n"

    monkeypatch.setattr(processing, "read_document", document)
    # The governing key vouches for its own subject, so the writer is the subject the trust root names.
    request = _document_request(reference, actor="source@example.org", sign_with=[party.public_key.fingerprint])

    dying = True
    real = LocalLayoutRegistry.push_referrer

    async def interrupted(self: Any, *args: Any, **kwargs: Any) -> Any:
        if dying:
            raise OSError("the worker died after the manifest was written")
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(LocalLayoutRegistry, "push_referrer", interrupted)
    prepared: list[tuple[dict[str, Any], str]] = []
    with pytest.raises(VitruvioError):
        await processing.process_job(
            request,
            "sigjob",
            lambda value, digest: prepared.append((json.loads(json.dumps(value)), digest)),
            lambda _i, _r: None,
        )
    assert prepared, "the artifact identity is recorded before the push"
    before = _consumer(tmp_path, "before")
    before.pull(reference, tag="ingest-sigjob", local=registry)
    assert before.auth_status()["state"] == "unsigned"

    dying = False
    values = {"prepared": json.dumps(prepared[0][0]), "prepared_digest": prepared[0][1]}
    recovered = await worker.Worker(fakeredis.FakeRedis(decode_responses=True))._recover(values, request)
    assert recovered is not None
    assert recovered["status"] == "completed"
    assert recovered["attribution"]["fully_vouched"] is True
    assert not any("unsigned" in warning for warning in recovered["warnings"])

    after = _consumer(tmp_path, "after")
    after.pull(reference, tag="ingest-sigjob", local=registry)
    assert after.auth_status()["state"] == "authorized"
    assert after.auth_attribution()["verified"] == recovered["attribution"]["verified"]
    assert after.auth_attribution()["fully_vouched"] is True


@pytest.mark.asyncio
async def test_recovery_refuses_a_governed_publication_it_cannot_complete(
    governed: tuple[Path, str, BrainService, Party], monkeypatch: pytest.MonkeyPatch
) -> None:
    from boltzmann.distribution.local import LocalLayoutRegistry

    registry, reference, source, party = governed
    source.auth_sign(party.public_key.fingerprint)
    source.push(reference, tag="v1", local=registry)

    async def document(_uri: str, _limit: int) -> bytes:
        return b"# New knowledge\nA distinct section.\n"

    monkeypatch.setattr(processing, "read_document", document)
    # The governing key vouches for its own subject, so the writer is the subject the trust root names.
    request = _document_request(reference, actor="source@example.org", sign_with=[party.public_key.fingerprint])

    async def interrupted(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise OSError("the registry never takes the referrer")

    monkeypatch.setattr(LocalLayoutRegistry, "push_referrer", interrupted)
    prepared: list[tuple[dict[str, Any], str]] = []
    with pytest.raises(VitruvioError):
        await processing.process_job(
            request,
            "stuckjob",
            lambda value, digest: prepared.append((json.loads(json.dumps(value)), digest)),
            lambda _i, _r: None,
        )
    values = {"prepared": json.dumps(prepared[0][0]), "prepared_digest": prepared[0][1]}
    with pytest.raises(VitruvioError):
        await worker.Worker(fakeredis.FakeRedis(decode_responses=True))._recover(values, request)


@pytest.mark.asyncio
async def test_a_push_that_landed_before_its_failure_surfaced_is_recovered_rather_than_failed(
    published: tuple[Path, str, BrainService], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry, reference, _ = published

    async def document(_uri: str, _limit: int) -> bytes:
        return b"# New knowledge\nA distinct section.\n"

    monkeypatch.setattr(processing, "read_document", document)
    real = BrainService.push

    def lost_response(self: BrainService, *args: Any, **kwargs: Any) -> dict[str, Any]:
        real(self, *args, **kwargs)
        raise SourceError("the registry's answer was lost")

    monkeypatch.setattr(BrainService, "push", lost_response)
    client = fakeredis.FakeRedis(decode_responses=True)
    key, message_id, fields = _queue(client, _document_request(reference), "lostjob")
    consumer = worker.Worker(client)
    await asyncio.to_thread(consumer._deliver, message_id, fields)

    values = client.hgetall(key)
    assert values["status"] == "completed"
    assert "request" not in values
    assert json.loads(values["result"])["output"]["tag"] == "ingest-lostjob"
    assert client.xpending(ingest.STREAM, worker.GROUP)["pending"] == 0
    reader = _consumer(tmp_path, "reader")
    reader.pull(reference, tag="ingest-lostjob", local=registry)
    assert reader.search("New knowledge")["matches"]


@pytest.mark.asyncio
async def test_a_push_whose_outcome_cannot_be_checked_leaves_the_job_recoverable(
    published: tuple[Path, str, BrainService], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, reference, _ = published

    async def document(_uri: str, _limit: int) -> bytes:
        return b"# New knowledge\nA distinct section.\n"

    monkeypatch.setattr(processing, "read_document", document)
    real = BrainService.push

    def lost_response(self: BrainService, *args: Any, **kwargs: Any) -> dict[str, Any]:
        real(self, *args, **kwargs)
        raise SourceError("the registry's answer was lost")

    monkeypatch.setattr(BrainService, "push", lost_response)

    def unreachable(*_args: Any, **_kwargs: Any) -> Any:
        raise SourceError("the registry is unreachable")

    monkeypatch.setattr(worker, "resolve_remote", unreachable)
    client = fakeredis.FakeRedis(decode_responses=True)
    key, message_id, fields = _queue(client, _document_request(reference), "uncertainjob")
    consumer = worker.Worker(client)
    await asyncio.to_thread(consumer._deliver, message_id, fields)

    values = client.hgetall(key)
    assert values["status"] == "running"
    assert "request" in values
    assert "prepared" in values
    assert client.xpending(ingest.STREAM, worker.GROUP)["pending"] == 1
    assert client.get(f"{worker.LEASE_PREFIX}uncertainjob") is None


def test_an_interrupted_attempt_keeps_the_request_for_the_next_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VITRUVIO_HTTP_ALLOWED_REGISTRIES", "docker.io")
    client = fakeredis.FakeRedis(decode_responses=True)
    key, message_id, fields = _queue(client, _document_request("example/brain"), "interrupted")
    consumer = worker.Worker(client)

    async def interrupted(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise KeyboardInterrupt

    monkeypatch.setattr(worker, "process_job", interrupted)
    with pytest.raises(KeyboardInterrupt):
        consumer._deliver(message_id, fields)
    values = client.hgetall(key)
    assert values["status"] == "running"
    assert "request" in values
    assert client.get(f"{worker.LEASE_PREFIX}interrupted") is None, "the lease is released with the attempt"
    assert client.xpending(ingest.STREAM, worker.GROUP)["pending"] == 1

    async def finished(_request: IngestionRequest, job_id: str, *_args: Any) -> dict[str, Any]:
        return {"job_id": job_id, "status": "completed", "items": [], "output": None}

    monkeypatch.setattr(worker, "process_job", finished)
    consumer._deliver(message_id, fields)
    values = client.hgetall(key)
    assert values["status"] == "completed"
    assert "request" not in values
    assert client.xpending(ingest.STREAM, worker.GROUP)["pending"] == 0


def test_a_stale_attempt_cannot_write_over_its_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VITRUVIO_HTTP_ALLOWED_REGISTRIES", "docker.io")
    client = fakeredis.FakeRedis(decode_responses=True)
    key, message_id, fields = _queue(client, _document_request("example/brain"), "fenced")
    consumer = worker.Worker(client)

    stale = worker.Lease(client, "fenced")
    assert stale.acquire()
    client.delete(stale.key)  # Its heartbeats stopped and the lease expired.
    fresh = worker.Lease(client, "fenced")
    assert fresh.acquire()

    with pytest.raises(worker.OwnershipLostError):
        consumer._finish(key, stale, status="failed")
    assert client.hget(key, "status") == "queued"
    assert stale.renew() is False
    assert stale.lost.is_set()
    assert stale.guarded(lambda pipe: pipe.xack(ingest.STREAM, worker.GROUP, message_id)) is False
    assert client.xpending(ingest.STREAM, worker.GROUP)["pending"] == 1

    # A delivery for a job another attempt holds is left pending and untouched.
    consumer._deliver(message_id, fields)
    assert client.hget(key, "status") == "queued"
    assert client.xpending(ingest.STREAM, worker.GROUP)["pending"] == 1

    consumer._finish(key, fresh, status="completed", result="{}")
    assert client.hget(key, "status") == "completed"
    fresh.release()
    assert client.get(fresh.key) is None


def test_abandoned_deliveries_are_scanned_from_the_returned_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    consumer = worker.Worker(client)
    ids = [client.xadd(ingest.STREAM, {"job_id": str(index)}) for index in range(12)]
    client.xreadgroup(worker.GROUP, "dead-worker", {ingest.STREAM: ">"}, count=12)
    monkeypatch.setattr(worker, "LEASE_TTL_MS", 0)

    cursor = "0-0"
    claimed: list[str] = []
    for _ in ids:
        cursor, deliveries = consumer._claim_abandoned(cursor)
        claimed.extend(message_id for message_id, _ in deliveries)
    # A scan that restarted at 0-0 each time would have claimed the first entry twelve times over.
    assert claimed == ids


@pytest.mark.asyncio
async def test_redis_job_submission_and_worker_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VITRUVIO_HTTP_ALLOWED_REGISTRIES", "docker.io")
    server = fakeredis.FakeServer()
    sync_client = fakeredis.FakeRedis(server=server, decode_responses=True)
    async_client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    ingest.app.state.redis = async_client

    async def fake_process(
        _request: IngestionRequest,
        job_id: str,
        _prepared: Any,
        progress: Any,
    ) -> dict[str, Any]:
        progress(0, {"index": 0, "kind": "candidates", "ok": True, "changed": False})
        return {"job_id": job_id, "status": "completed", "items": [], "output": None}

    monkeypatch.setattr(worker, "process_job", fake_process)
    body = {
        "brain": {"repository": "example/brain", "tag": "v1"},
        "config": {"actor": {"id": "writer@example.org"}},
        "assisted_by": [],
        "items": [{"kind": "candidates", "task": {}, "candidates": {}}],
    }
    transport = httpx.ASGITransport(app=ingest.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        submitted = await client.post("/v1/ingestions", json=body)
        assert submitted.status_code == 202
        job_id = submitted.json()["data"]["job_id"]
        waiting = await client.get(f"/v1/ingestions/{job_id}")
        assert waiting.json()["data"]["status"] == "queued"

        consumer = worker.Worker(sync_client)
        batches: Any = sync_client.xreadgroup(worker.GROUP, consumer.name, {ingest.STREAM: ">"})
        deliveries: Any = batches[0][1]
        await asyncio.to_thread(consumer._deliver, *deliveries[0])

        finished = await client.get(f"/v1/ingestions/{job_id}")
        assert finished.json()["data"]["status"] == "completed"
        assert finished.json()["data"]["items"][0]["ok"] is True
        assert sync_client.xpending(ingest.STREAM, worker.GROUP)["pending"] == 0
    await async_client.aclose()


def test_governed_publish_requires_an_explicit_authorized_ssh_key() -> None:
    class SignerService:
        def __init__(self) -> None:
            self.signed: list[str] = []
            self.authorized = False

        def auth_keys(self) -> dict[str, Any]:
            return {"keys": [{"fingerprint": "SHA256:good"}]}

        def auth_trust_root(self) -> dict[str, Any]:
            return {"keys": [{"fingerprint": "SHA256:good", "active": True}]}

        def auth_sign(self, fingerprint: str) -> None:
            self.signed.append(fingerprint)

        def auth_status(self) -> dict[str, str]:
            return {"state": "authorized" if self.authorized else "unsigned"}

        def auth_attribution(self) -> dict[str, bool]:
            return {"complete": True, "fully_vouched": True}

    service = SignerService()
    result: dict[str, Any] = {"warnings": []}
    with pytest.raises(ConfigError):
        processing._sign_governed(service, [], result)  # type: ignore[arg-type]
    with pytest.raises(ConfigError):
        processing._sign_governed(service, ["SHA256:other"], result)  # type: ignore[arg-type]
    assert service.signed == []
    with pytest.raises(VitruvioError):
        processing._sign_governed(service, ["SHA256:good"], result)  # type: ignore[arg-type]
    service.authorized = True
    processing._sign_governed(service, ["SHA256:good"], result)  # type: ignore[arg-type]
    assert result["attribution"]["fully_vouched"] is True
