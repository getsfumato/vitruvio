"""Stateless HTTP reads over caller-selected OCI brains."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from vitruvio.http.brain import InstalledBrain, RemoteManifest, install, make_service, portable_config, resolve_remote
from vitruvio.http.models import BrainRequest, ResolveRequest, SearchRequest
from vitruvio.http.response import envelope, install_errors
from vitruvio.kernel import ExitCode, VitruvioError


class InstallInterruptedError(VitruvioError):
    """The request that was installing a brain went away before the install finished; the others waited on it."""

    code = "INSTALL_INTERRUPTED"
    exit_code = ExitCode.BUSY
    retryable = True
    http_status = 503


@dataclass(slots=True)
class CacheEntry:
    key: str
    directory: tempfile.TemporaryDirectory[str]
    ready: asyncio.Future[InstalledBrain]
    users: int = 0
    signatures: list[str] = field(default_factory=list)
    """The manifest's referrers when the entry was installed; a governed head is refreshed when they change."""

    @property
    def installed(self) -> InstalledBrain:
        return self.ready.result()


class QueryCache:
    """
    A bounded, disposable cache keyed by remote manifest and exact request configuration.

    The lock covers bookkeeping only. Resolution and installation happen outside it, in worker threads, because the
    registry client blocks: a lock held across one brain's download would stall every request for the brains that
    are already cached. Two requests that miss on the same key share one installation through the entry's future.
    """

    def __init__(self, capacity: int = 4) -> None:
        self.capacity = capacity
        self._entries: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _resolve(request: BrainRequest) -> RemoteManifest:
        # Resolving the manifest on every call detects a moved tag without treating the tag as immutable.
        with tempfile.TemporaryDirectory(prefix="vitruvio-resolve-") as work:
            probe = make_service(Path(work), request.brain, request.config)
            return resolve_remote(probe, request.brain)

    @staticmethod
    def _key(request: BrainRequest, digest: str) -> str:
        identity = json.dumps(
            {"repository": request.brain.repository, "digest": digest, "config": portable_config(request.config)},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(identity.encode()).hexdigest()

    async def acquire(self, request: BrainRequest) -> CacheEntry:
        remote = await asyncio.to_thread(self._resolve, request)
        key = self._key(request, remote.digest)
        while True:
            async with self._lock:
                entry = self._entries.get(key)
                installing = entry is None
                if entry is None:
                    entry = CacheEntry(
                        key,
                        tempfile.TemporaryDirectory(prefix="vitruvio-query-"),
                        asyncio.get_running_loop().create_future(),
                    )
                    self._entries[key] = entry
                else:
                    # Most recently used last, which is the order eviction walks.
                    self._entries[key] = self._entries.pop(key)
                entry.users += 1
            if installing:
                await self._install(entry, request, remote)
                return entry
            try:
                # Shielded: a waiter that is cancelled must not cancel the future the installer still has to fill.
                installed = await asyncio.shield(entry.ready)
            except BaseException:
                await self.release(entry)
                raise
            if not installed.governed or await asyncio.to_thread(remote.signatures) == entry.signatures:
                return entry
            # Signatures are referrers, so they land without changing the manifest digest this entry is keyed by. A
            # governed head whose referrers changed is retired; the next pass installs it afresh and sees them.
            async with self._lock:
                if self._entries.get(key) is entry:
                    del self._entries[key]
            await self.release(entry)

    async def _install(self, entry: CacheEntry, request: BrainRequest, remote: RemoteManifest) -> None:
        try:
            # Listed before the pull rather than after: a signature that lands in between is then in the pull and
            # absent from the listing, which costs one spurious refresh instead of a stale authenticity.
            signatures = await asyncio.to_thread(remote.signatures)
            service = make_service(Path(entry.directory.name), request.brain, request.config)
            installed = await asyncio.to_thread(install, service, request.brain, remote.digest)
        except BaseException as error:
            async with self._lock:
                if self._entries.get(entry.key) is entry:
                    del self._entries[entry.key]
            failure = (
                InstallInterruptedError("the brain installation was interrupted")
                if isinstance(error, asyncio.CancelledError)
                else error
            )
            entry.ready.set_exception(failure)
            await self.release(entry)
            raise
        entry.signatures = signatures
        entry.ready.set_result(installed)
        async with self._lock:
            self._evict()

    async def release(self, entry: CacheEntry) -> None:
        async with self._lock:
            entry.users -= 1
            if entry.users == 0 and self._entries.get(entry.key) is not entry:
                # Retired or failed: nothing can find it again, so the last user disposes of it.
                entry.directory.cleanup()
            self._evict()

    def _evict(self) -> None:
        for key, entry in list(self._entries.items()):
            if len(self._entries) <= self.capacity:
                break
            if entry.users == 0 and entry.ready.done():
                self._entries.pop(key)
                entry.directory.cleanup()

    @asynccontextmanager
    async def use(self, request: BrainRequest) -> AsyncIterator[InstalledBrain]:
        entry = await self.acquire(request)
        try:
            yield entry.installed
        finally:
            await self.release(entry)

    def close(self) -> None:
        for entry in self._entries.values():
            entry.directory.cleanup()
        self._entries.clear()


cache = QueryCache()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        cache.close()


app = FastAPI(title="Vitruvio Query API", version="1", lifespan=lifespan)
install_errors(app)


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/search")
async def search(request: SearchRequest) -> dict[str, Any]:
    async with cache.use(request) as installed:
        data = await asyncio.to_thread(installed.service.search, **request.search_args())
        return envelope("query.search", {**installed.metadata(), "evidence": data}, installed.warnings)


@app.post("/v1/explain")
async def explain(request: SearchRequest) -> dict[str, Any]:
    async with cache.use(request) as installed:
        args = request.search_args()
        args.pop("diagnostics")
        data = await asyncio.to_thread(installed.service.explain, **args)
        return envelope("query.explain", {**installed.metadata(), "explanation": data}, installed.warnings)


@app.post("/v1/brain/state")
async def state(request: BrainRequest) -> dict[str, Any]:
    async with cache.use(request) as installed:
        data = await asyncio.to_thread(installed.service.state)
        return envelope("brain.state", {**installed.metadata(), "state": data}, installed.warnings)


@app.post("/v1/blocks/resolve")
async def resolve_block(request: ResolveRequest) -> dict[str, Any]:
    async with cache.use(request) as installed:
        data = await asyncio.to_thread(installed.service.resolve, request.block_id)
        return envelope("inspect.resolve", {**installed.metadata(), "block": data}, installed.warnings)
