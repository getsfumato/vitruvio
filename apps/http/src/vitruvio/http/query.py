"""Stateless HTTP reads over caller-selected OCI brains."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from vitruvio.http.brain import InstalledBrain, install, make_service, resolve_manifest
from vitruvio.http.models import BrainRequest, ResolveRequest, SearchRequest
from vitruvio.http.response import envelope, install_errors


@dataclass(slots=True)
class CacheEntry:
    directory: tempfile.TemporaryDirectory[str]
    installed: InstalledBrain
    users: int = 0


class QueryCache:
    """A bounded, disposable cache keyed by remote manifest and exact request configuration."""

    def __init__(self, capacity: int = 4) -> None:
        self.capacity = capacity
        self._entries: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def _get(self, request: BrainRequest) -> CacheEntry:
        async with self._lock:
            # Resolving the manifest on every call detects a moved tag without treating the tag as immutable.
            with tempfile.TemporaryDirectory(prefix="vitruvio-resolve-") as work:
                probe = make_service(Path(work), request.brain, request.config)
                digest = await resolve_manifest(probe, request.brain)
            identity = json.dumps(
                {"repository": request.brain.repository, "digest": digest, "config": request.config.model_dump()},
                sort_keys=True,
                separators=(",", ":"),
            )
            key = hashlib.sha256(identity.encode()).hexdigest()
            cached = self._entries.pop(key, None)
            if cached is not None:
                cached.users += 1
                self._entries[key] = cached
                return cached

            directory = tempfile.TemporaryDirectory(prefix="vitruvio-query-")
            try:
                service = make_service(Path(directory.name), request.brain, request.config)
                installed = await install(service, request.brain, digest)
            except BaseException:
                directory.cleanup()
                raise
            entry = CacheEntry(directory, installed, users=1)
            self._entries[key] = entry
            self._evict()
            return entry

    def _evict(self) -> None:
        for key, entry in list(self._entries.items()):
            if len(self._entries) <= self.capacity:
                break
            if entry.users == 0:
                self._entries.pop(key)
                entry.directory.cleanup()

    @asynccontextmanager
    async def use(self, request: BrainRequest) -> AsyncIterator[InstalledBrain]:
        entry = await self._get(request)
        try:
            yield entry.installed
        finally:
            entry.users -= 1
            self._evict()

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
