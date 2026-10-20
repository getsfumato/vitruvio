"""HTTP submission and observation of Redis-backed ingest jobs."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException

from vitruvio.http.brain import make_service
from vitruvio.http.models import IngestionRequest
from vitruvio.http.response import envelope, install_errors
from vitruvio.kernel import ConfigError

STREAM = "vitruvio:ingest:jobs"
JOB_PREFIX = "vitruvio:ingest:job:"
RESULT_TTL = 7 * 24 * 60 * 60


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = redis.from_url(
        os.environ.get("VITRUVIO_HTTP_REDIS_URL", "redis://localhost:6379/0"), decode_responses=True
    )
    app.state.redis = client
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(title="Vitruvio Ingest API", version="1", lifespan=lifespan)
install_errors(app)


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready() -> dict[str, str]:
    await app.state.redis.ping()
    return {"status": "ok"}


@app.post("/v1/ingestions", status_code=202)
async def submit(request: IngestionRequest) -> dict[str, Any]:
    if request.config.actor is None:
        raise ConfigError("ingestion requires a declared actor in config.actor")
    payload = request.model_dump_json()
    ceiling = int(os.environ.get("VITRUVIO_HTTP_MAX_JOB_BYTES", str(8 * 1024 * 1024)))
    if len(payload.encode("utf-8")) > ceiling:
        raise HTTPException(status_code=413, detail=f"job request exceeds the {ceiling} byte limit")
    with tempfile.TemporaryDirectory(prefix="vitruvio-validate-") as work:
        make_service(Path(work), request.brain, request.config, assisted_by=request.assisted_by)

    job_id = uuid.uuid4().hex
    key = f"{JOB_PREFIX}{job_id}"
    now = datetime.now(UTC).isoformat()
    async with app.state.redis.pipeline(transaction=True) as pipe:
        pipe.hset(
            key,
            mapping={
                "id": job_id,
                "status": "queued",
                "created_at": now,
                "updated_at": now,
                "request": payload,
            },
        )
        pipe.expire(key, RESULT_TTL)
        pipe.xadd(STREAM, {"job_id": job_id})
        await pipe.execute()
    return envelope("ingest.submit", {"job_id": job_id, "status": "queued", "status_url": f"/v1/ingestions/{job_id}"})


@app.get("/v1/ingestions/{job_id}")
async def status(job_id: str) -> dict[str, Any]:
    values: dict[str, str] = await app.state.redis.hgetall(f"{JOB_PREFIX}{job_id}")
    if not values:
        raise HTTPException(status_code=404, detail="job not found")
    items = [json.loads(value) for key, value in values.items() if key.startswith("item:")]
    items.sort(key=lambda item: item["index"])
    data: dict[str, Any] = {
        "job_id": job_id,
        "status": values["status"],
        "created_at": values["created_at"],
        "updated_at": values["updated_at"],
        "items": items,
    }
    if "result" in values:
        data["result"] = json.loads(values["result"])
    if "error" in values:
        data["error"] = json.loads(values["error"])
    return envelope("ingest.status", data)
