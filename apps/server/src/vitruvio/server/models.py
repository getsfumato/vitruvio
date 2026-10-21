"""The deliberately small HTTP contract shared by the two services."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrainRef(StrictModel):
    repository: str = Field(min_length=1)
    tag: str = Field(min_length=1)


class BrainConfig(StrictModel):
    """Only portable Vitruvio settings; paths, sources and credentials are server-owned."""

    actor: dict[str, Any] | None = None
    assisted_by: list[dict[str, Any]] = Field(default_factory=list)
    authenticity: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    embedding: dict[str, Any] = Field(default_factory=dict)
    index: list[dict[str, Any]] = Field(default_factory=list)
    planner: dict[str, Any] = Field(default_factory=dict)
    ingest: dict[str, Any] = Field(default_factory=dict)


class BrainRequest(StrictModel):
    brain: BrainRef
    config: BrainConfig = Field(default_factory=BrainConfig)


class SearchRequest(BrainRequest):
    text: str = ""
    memory_types: list[str] | None = None
    subject: str | None = None
    since: str | None = None
    until: str | None = None
    tags: list[str] | None = None
    classes: list[str] | None = None
    evidence: list[str] | None = None
    include_superseded: bool = False
    mode: str | None = None
    limit: int = Field(default=10, ge=1, le=100)
    expand_depth: int = Field(default=0, ge=0, le=10)
    diagnostics: bool = False

    def search_args(self) -> dict[str, Any]:
        return self.model_dump(exclude={"brain", "config"})


class ResolveRequest(BrainRequest):
    block_id: str


class DocumentItem(StrictModel):
    kind: Literal["document"]
    uri: str
    origin: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    proposer: str = "structure"
    allowed: list[str] | None = None
    subject: str | None = None
    license: str | None = None
    retention_policy: str | None = None
    normalize_with: str | None = None


class CandidateItem(StrictModel):
    kind: Literal["candidates"]
    task: dict[str, Any]
    candidates: dict[str, Any]


class IngestionRequest(BrainRequest):
    assisted_by: list[str]
    sign_with: list[str] = Field(default_factory=list)
    items: list[DocumentItem | CandidateItem] = Field(min_length=1, max_length=100)
