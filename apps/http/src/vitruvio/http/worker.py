"""A Redis Streams worker; each delivery owns a disposable local OCI layout."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import redis
from redis.exceptions import ResponseError

from vitruvio.http.brain import make_service, resolve_manifest
from vitruvio.http.ingest import JOB_PREFIX, RESULT_TTL, STREAM
from vitruvio.http.models import BrainRef, IngestionRequest
from vitruvio.http.processing import process_job
from vitruvio.kernel import VitruvioError
from vitruvio.runtime import translate

GROUP = "vitruvio-ingest-workers"
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _failure(error: Exception) -> dict[str, Any]:
    if not isinstance(error, VitruvioError):
        logger.exception("unexpected ingest job error", exc_info=error)
        return {"code": "INTERNAL", "message": "internal processing error", "hint": None, "retryable": False}
    return {
        "code": error.code,
        "message": error.message,
        "hint": error.hint,
        "retryable": error.retryable,
    }


class Worker:
    def __init__(self, client: redis.Redis) -> None:
        self.client = client
        self.name = f"worker-{uuid.uuid4().hex[:12]}"
        try:
            self.client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    def _set(self, key: str, **fields: str) -> None:
        fields["updated_at"] = _now()
        self.client.hset(key, mapping=cast(Any, fields))
        self.client.expire(key, RESULT_TTL)

    async def _recover(self, values: dict[str, str], request: IngestionRequest) -> dict[str, Any] | None:
        if "prepared" not in values:
            return None
        result: dict[str, Any] = json.loads(values["prepared"])
        output = result["output"]
        target = BrainRef(repository=output["repository"], tag=output["tag"])
        with tempfile.TemporaryDirectory(prefix="vitruvio-recover-") as work:
            probe = make_service(Path(work), target, request.config, assisted_by=request.assisted_by)
            try:
                actual = await resolve_manifest(probe, target)
            except Exception as error:
                if translate(error).code == "REFERENCE_NOT_FOUND":
                    return None
                raise
        if actual != values["prepared_digest"]:
            raise RuntimeError("the job output tag contains a different artifact")
        result["status"] = "partial" if result["failed"] else "completed"
        return result

    def _run_job(self, job_id: str) -> None:
        key = f"{JOB_PREFIX}{job_id}"
        values = cast(dict[str, str], self.client.hgetall(key))
        if not values:
            return
        if values["status"] in {"completed", "partial", "failed"}:
            return

        def prepared(result: dict[str, Any], digest: str) -> None:
            self._set(key, prepared=json.dumps(result), prepared_digest=digest)

        def progress(index: int, result: dict[str, Any]) -> None:
            self._set(key, **{f"item:{index}": json.dumps(result)})

        try:
            request = IngestionRequest.model_validate_json(values["request"])
            self._set(key, status="running")
            recovered = asyncio.run(self._recover(values, request))
            result = recovered or asyncio.run(process_job(request, job_id, prepared, progress))
            self._set(key, status=result["status"], result=json.dumps(result))
        except Exception as error:
            self._set(key, status="failed", error=json.dumps(_failure(error)))
        finally:
            self.client.hdel(key, "request")

    def _heartbeat(self, message_id: str, done: threading.Event) -> None:
        while not done.wait(10):
            self.client.xclaim(STREAM, GROUP, self.name, min_idle_time=0, message_ids=[message_id], justid=True)

    def _deliver(self, message_id: str, fields: dict[str, str]) -> None:
        done = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(message_id, done), daemon=True)
        beat.start()
        try:
            self._run_job(fields["job_id"])
            self.client.xack(STREAM, GROUP, message_id)
            self.client.xdel(STREAM, message_id)
        finally:
            done.set()
            beat.join(timeout=2)

    def run_forever(self) -> None:
        while True:
            claimed = self.client.xautoclaim(STREAM, GROUP, self.name, min_idle_time=120_000, start_id="0-0", count=1)
            deliveries = claimed[1]
            if not deliveries:
                batches = cast(Any, self.client.xreadgroup(GROUP, self.name, {STREAM: ">"}, count=1, block=5000))
                deliveries = batches[0][1] if batches else []
            for message_id, fields in deliveries:
                self._deliver(message_id, fields)


def main() -> None:
    client: redis.Redis = redis.from_url(
        os.environ.get("VITRUVIO_HTTP_REDIS_URL", "redis://localhost:6379/0"), decode_responses=True
    )
    Worker(client).run_forever()


if __name__ == "__main__":
    main()
