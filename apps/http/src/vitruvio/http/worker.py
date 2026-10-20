"""A Redis Streams worker; each delivery owns a disposable local OCI layout and a fenced lease on its job.

Two things a worker can get wrong are guarded structurally rather than by convention:

* **A stale attempt writing over a live one.** A worker that stops heartbeating for longer than the lease -- paused,
  partitioned, swapped out -- loses the job, and another worker takes it. The first may still be running. Every
  state change, the publication included, is conditioned on a per-attempt token that Redis compares in the same
  transaction, so the stale attempt's writes are refused server-side rather than by its own belief about ownership.
* **Calling a publication failed because the response was lost.** ``push`` can land and then raise. The prepared
  artifact identity is recorded before the push, and a failure after that point checks the registry before the job
  is finalized; an answer it cannot get leaves the job recoverable rather than terminal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import redis
from redis.exceptions import ResponseError, WatchError

from vitruvio.http.brain import InstalledBrain, install, local_registry, make_service, resolve_remote
from vitruvio.http.ingest import JOB_PREFIX, RESULT_TTL, STREAM
from vitruvio.http.models import BrainRef, IngestionRequest
from vitruvio.http.processing import _sign_governed, process_job
from vitruvio.kernel import ExitCode, VitruvioError

GROUP = "vitruvio-ingest-workers"
LEASE_PREFIX = "vitruvio:ingest:lease:"
LEASE_TTL_MS = 120_000
"""How long a job stays owned without a heartbeat; also how idle a delivery must be before another worker claims it."""
HEARTBEAT_SECONDS = 10
TERMINAL = frozenset({"completed", "partial", "failed"})
logger = logging.getLogger(__name__)


class OwnershipLostError(VitruvioError):
    """This attempt's lease lapsed or passed to another worker, so it must stop writing."""

    code = "INGEST_OWNERSHIP_LOST"
    exit_code = ExitCode.BUSY
    retryable = True
    http_status = 409


class PublicationMismatchError(VitruvioError):
    """The job's output tag names an artifact other than the one this job prepared."""

    code = "INGEST_OUTPUT_MISMATCH"
    exit_code = ExitCode.DIVERGED
    http_status = 409


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


class Lease:
    """
    One attempt's ownership of a job, fenced by a token every state change is conditioned on.

    Redis holds the token under a TTL. ``WATCH``/``MULTI`` rather than a Lua script, so the same code runs against
    fakeredis in the tests; the comparison and the write are one transaction either way.
    """

    def __init__(self, client: redis.Redis, job_id: str) -> None:
        self.client = client
        self.key = f"{LEASE_PREFIX}{job_id}"
        self.token = uuid.uuid4().hex
        self.lost = threading.Event()

    def acquire(self) -> bool:
        return bool(self.client.set(self.key, self.token, nx=True, px=LEASE_TTL_MS))

    def guarded(self, *commands: Callable[[Any], Any]) -> bool:
        """Run commands atomically, only while this attempt still holds the lease. False when it does not."""
        with self.client.pipeline(transaction=True) as pipe:
            while True:
                try:
                    pipe.watch(self.key)
                    if pipe.get(self.key) != self.token:
                        pipe.unwatch()
                        self.lost.set()
                        return False
                    pipe.multi()
                    for command in commands:
                        command(pipe)
                    pipe.execute()
                    return True
                except WatchError:
                    continue

    def renew(self) -> bool:
        return self.guarded(lambda pipe: pipe.pexpire(self.key, LEASE_TTL_MS))

    def release(self) -> None:
        self.guarded(lambda pipe: pipe.delete(self.key))


class Worker:
    def __init__(self, client: redis.Redis) -> None:
        self.client = client
        self.name = f"worker-{uuid.uuid4().hex[:12]}"
        try:
            self.client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    def _write(self, key: str, lease: Lease, **fields: str) -> None:
        fields["updated_at"] = _now()
        if not lease.guarded(
            lambda pipe: pipe.hset(key, mapping=cast(Any, fields)),
            lambda pipe: pipe.expire(key, RESULT_TTL),
        ):
            raise OwnershipLostError("this attempt no longer owns the job")

    def _finish(self, key: str, lease: Lease, **fields: str) -> None:
        """Record a terminal state and, in the same transaction, discard the request it no longer needs."""
        fields["updated_at"] = _now()
        if not lease.guarded(
            lambda pipe: pipe.hset(key, mapping=cast(Any, fields)),
            lambda pipe: pipe.hdel(key, "request"),
            lambda pipe: pipe.expire(key, RESULT_TTL),
        ):
            raise OwnershipLostError("this attempt no longer owns the job")

    @staticmethod
    def _verify_output(request: IngestionRequest, target: BrainRef, expected: str, work: Path) -> InstalledBrain:
        probe = make_service(work, target, request.config, assisted_by=request.assisted_by)
        return install(probe, target, expected)

    async def _recover(self, values: dict[str, str], request: IngestionRequest) -> dict[str, Any] | None:
        """
        What an interrupted publication actually left in the registry, verified rather than assumed.

        ``None`` when the output tag is absent, which means the push never landed. Otherwise the tag must name the
        prepared manifest, the artifact must verify, and a governed head must be authorized: ``push`` uploads the
        manifest before the signature referrers, so a worker that died in between left a head a consumer sees as
        unsigned. That case is completed here, with the keys the request named, and then verified from the registry
        again before it is reported as published.
        """
        result: dict[str, Any] = json.loads(values["prepared"])
        output = result["output"]
        target = BrainRef(repository=output["repository"], tag=output["tag"])
        expected = values["prepared_digest"]
        with tempfile.TemporaryDirectory(prefix="vitruvio-recover-") as work:
            probe = make_service(Path(work), target, request.config, assisted_by=request.assisted_by)
            try:
                remote = await asyncio.to_thread(resolve_remote, probe, target)
            except VitruvioError as error:
                if error.code == "REFERENCE_NOT_FOUND":
                    return None
                raise
            if remote.digest != expected:
                raise PublicationMismatchError("the job output tag names a different artifact than this job prepared")
            installed = await asyncio.to_thread(install, probe, target, expected)
            if installed.governed and installed.authenticity.get("state") != "authorized":
                _sign_governed(probe, request.sign_with, result)
                await asyncio.to_thread(probe.push, target.repository, tag=target.tag, local=local_registry())
                with tempfile.TemporaryDirectory(prefix="vitruvio-recover-verify-") as check:
                    installed = await asyncio.to_thread(self._verify_output, request, target, expected, Path(check))
                if installed.authenticity.get("state") != "authorized":
                    raise VitruvioError(
                        "the published output is not authorized even after its signatures were completed"
                    )
        if installed.governed:
            result["attribution"] = installed.attribution
        result["warnings"] = [
            *result.get("warnings", []),
            *[w for w in installed.warnings if w not in result.get("warnings", [])],
        ]
        result["status"] = "partial" if result["failed"] else "completed"
        return result

    def _run_job(self, job_id: str, lease: Lease) -> bool:
        """Drive one job to a terminal state. True when the delivery can be acknowledged."""
        key = f"{JOB_PREFIX}{job_id}"
        values = cast(dict[str, str], self.client.hgetall(key))
        if not values or values["status"] in TERMINAL:
            return True
        try:
            request = IngestionRequest.model_validate_json(values["request"])
        except Exception as error:
            # The request survives until a terminal state is recorded, so this is a hash somebody edited by hand.
            # It must not wedge the stream.
            self._finish(key, lease, status="failed", error=json.dumps(_failure(error)))
            return True

        def prepared(result: dict[str, Any], digest: str) -> None:
            self._write(key, lease, prepared=json.dumps(result), prepared_digest=digest)

        def progress(index: int, result: dict[str, Any]) -> None:
            self._write(key, lease, **{f"item:{index}": json.dumps(result)})

        try:
            self._write(key, lease, status="running", attempt=lease.token, worker=self.name)
            recovered = asyncio.run(self._recover(values, request)) if "prepared" in values else None
            result = recovered or asyncio.run(process_job(request, job_id, prepared, progress))
            self._finish(key, lease, status=result["status"], result=json.dumps(result))
            return True
        except OwnershipLostError:
            logger.warning(
                "job %s: attempt %s lost its lease; its state belongs to the current owner", job_id, lease.token
            )
            return False
        except Exception as error:
            return self._fail(key, lease, request, error)

    def _fail(self, key: str, lease: Lease, request: IngestionRequest, error: Exception) -> bool:
        """
        Finalize a failed attempt -- unless its push may have landed, in which case the registry decides.

        Returns False, leaving the job ``running`` with its request and prepared record intact, when the registry
        cannot answer: the delivery stays pending and the check runs again on the next claim.
        """
        latest = cast(dict[str, str], self.client.hgetall(key))
        if "prepared" in latest:
            try:
                recovered = asyncio.run(self._recover(latest, request))
            except VitruvioError as check:
                if check.retryable:
                    logger.warning("job %s: publication outcome is uncertain (%s); left pending", key, check.message)
                    return False
                error = check
            except Exception:
                logger.exception("job %s: could not check the prepared output; left pending", key)
                return False
            else:
                if recovered is not None:
                    self._finish(key, lease, status=recovered["status"], result=json.dumps(recovered))
                    return True
        try:
            self._finish(key, lease, status="failed", error=json.dumps(_failure(error)))
        except OwnershipLostError:
            logger.warning("job %s: attempt %s lost its lease while failing; not recorded", key, lease.token)
            return False
        return True

    def _heartbeat(self, message_id: str, lease: Lease, done: threading.Event) -> None:
        while not done.wait(HEARTBEAT_SECONDS):
            try:
                if not lease.renew():
                    logger.warning("job lease %s lapsed; this attempt stops renewing it", lease.key)
                    return
                # Keep the delivery visibly active, but only while this attempt still owns the job: claiming it
                # back unconditionally is how a stale worker used to steal a delivery from its replacement.
                self.client.xclaim(STREAM, GROUP, self.name, min_idle_time=0, message_ids=[message_id], justid=True)
            except redis.RedisError as error:
                logger.warning("job heartbeat failed: %s", error)

    def _deliver(self, message_id: str, fields: dict[str, str]) -> None:
        job_id = fields["job_id"]
        lease = Lease(self.client, job_id)
        if not lease.acquire():
            # Held by a live attempt. Left pending rather than acknowledged: once that attempt finishes, the next
            # claim finds the job terminal and acknowledges it.
            logger.info("job %s is held by another attempt; its delivery stays pending", job_id)
            return
        done = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(message_id, lease, done), daemon=True)
        beat.start()
        try:
            terminal = self._run_job(job_id, lease)
            if terminal and not lease.guarded(
                lambda pipe: pipe.xack(STREAM, GROUP, message_id),
                lambda pipe: pipe.xdel(STREAM, message_id),
            ):
                logger.warning("job %s: finished after its lease lapsed; the delivery is left to its owner", job_id)
        finally:
            done.set()
            beat.join(timeout=2)
            lease.release()

    def _claim_abandoned(self, cursor: str) -> tuple[str, list[tuple[str, dict[str, str]]]]:
        """
        Take over one delivery whose worker stopped heartbeating, scanning onward from where the last scan ended.

        ``XAUTOCLAIM`` examines a bounded window of the pending list per call and returns where to continue; a
        scan that restarted at ``0-0`` every time could never get past entries kept active by healthy workers to an
        abandoned one behind them. The cursor comes back as ``0-0`` once the end is reached, which restarts it.
        """
        claimed = cast(
            Any,
            self.client.xautoclaim(STREAM, GROUP, self.name, min_idle_time=LEASE_TTL_MS, start_id=cursor, count=1),
        )
        return str(claimed[0]), list(claimed[1])

    def run_forever(self) -> None:
        cursor = "0-0"
        while True:
            cursor, deliveries = self._claim_abandoned(cursor)
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
