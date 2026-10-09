"""The HTTP adapters round-trip through the real OCI layout registry."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import fakeredis
import fakeredis.aioredis
import httpx
import pytest

from vitruvio.http import ingest, processing, query, worker
from vitruvio.http.models import IngestionRequest, SearchRequest
from vitruvio.ingest.evidence import Evidence
from vitruvio.kernel import ConfigError, VitruvioError, resolve
from vitruvio.runtime import BrainService


@pytest.fixture
def published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, str]]:
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
    yield registry, reference
    query.cache.close()


@pytest.mark.asyncio
async def test_query_loads_caller_selected_oci(published: tuple[Path, str]) -> None:
    _, reference = published
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
async def test_query_refreshes_a_moved_tag(published: tuple[Path, str], tmp_path: Path) -> None:
    registry, reference = published
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
async def test_ingest_job_publishes_a_new_tag(
    published: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry, reference = published

    async def document(_uri: str, _limit: int) -> bytes:
        return b"# New knowledge\nA distinct section.\n"

    monkeypatch.setattr(processing, "read_document", document)
    request = IngestionRequest.model_validate(
        {
            "brain": {"repository": reference, "tag": "v1"},
            "config": {"actor": {"id": "writer@example.org"}},
            "assisted_by": [],
            "items": [
                {
                    "kind": "document",
                    "uri": "https://example.org/document.md",
                    "origin": "test:new",
                    "media_type": "text/markdown",
                }
            ],
        }
    )
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

    consumer = BrainService(resolve(brain=tmp_path / "consumer", actor_id="reader@example.org", require_layout=False))
    consumer.init(write_config=False)
    consumer.pull(reference, tag="ingest-testjob", local=registry)
    assert consumer.verify()["verified"] is True
    assert consumer.search("New knowledge")["matches"]


@pytest.mark.asyncio
async def test_ingest_continues_after_a_rejected_item(
    published: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, reference = published

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
async def test_redis_job_submission_and_worker_result(monkeypatch: pytest.MonkeyPatch) -> None:
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
