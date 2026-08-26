"""``BrainService`` -- one method per protocol operation, each returning JSON-able data.

This is the layer that makes the CLI thin, and the MCP server and HTTP API that follow it thin as well. Three
properties are load-bearing:

**Every method returns a plain dictionary.** Built by :mod:`vitruvio.runtime.wire`, which is the only place SDK
models become JSON. The CLI's ``--json``, an MCP tool result and an API response body are then the same bytes
by construction rather than by discipline.

**Every method declares its capability.** Opening a brain at ``INSPECT`` registers no index, which is what
keeps ``brain state`` from loading a model. See :mod:`vitruvio.runtime.assembly`.

**Every SDK exception is translated on the way out.** A caller gets a code, an exit status and a hint, from the
one table in :mod:`vitruvio.runtime.mapping`, rather than a raw ``BoltzmannError`` whose type it would have to
know how to interpret.

Nothing here decides how a result is displayed, and nothing here writes prose. The brain returns evidence; the
caller writes the answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import cached_property
from pathlib import Path
from typing import Any

from boltzmann.brain import Brain

from vitruvio.kernel import ResolvedConfig
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.ops.benchmarking import BenchmarkOps
from vitruvio.runtime.ops.browsing import BrowsingOps
from vitruvio.runtime.ops.embedders import EmbedderOps
from vitruvio.runtime.ops.indices import IndexOps
from vitruvio.runtime.ops.inspection import InspectionOps
from vitruvio.runtime.ops.install import InstallOps
from vitruvio.runtime.ops.lifecycle import LifecycleOps
from vitruvio.runtime.ops.projects import ProjectOps
from vitruvio.runtime.ops.publish import PublishOps
from vitruvio.runtime.ops.reconcile import ReconcileOps
from vitruvio.runtime.ops.registration import RegistrationOps
from vitruvio.runtime.ops.remote import RemoteOps
from vitruvio.runtime.ops.retention import RetentionOps
from vitruvio.runtime.ops.retrieval import RetrievalOps
from vitruvio.runtime.ops.sources import SourceOps
from vitruvio.runtime.ops.tasks import TaskOps
from vitruvio.runtime.session import BrainSession


class BrainService:
    """
    The protocol, as operations a caller can drive without knowing the SDK.

    Attributes:
        config (ResolvedConfig): Which brain, who as, under what policy.
    """

    def __init__(self, config: ResolvedConfig) -> None:
        """
        Build a service over a resolved configuration.

        No brain is opened here. Each operation opens at its own capability, so constructing a service is free
        and a read never pays for a write's machinery.

        Args:
            config (ResolvedConfig): The resolved configuration.
        """
        self.config = config
        self.session = BrainSession(config)

    def brain(self, capability: Capability = Capability.INSPECT) -> Brain:
        """
        The opened brain, memoized per capability.

        Args:
            capability (Capability): How much to stand up.

        Returns:
            Brain: The brain.
        """
        return self.session.brain(capability)

    # --- Lifecycle ------------------------------------------------------------

    @cached_property
    def lifecycle_ops(self) -> LifecycleOps:
        """The lifecycle operations."""
        return LifecycleOps(self.session)

    def init(self, *, force: bool = False) -> dict[str, Any]:
        """Create a brain, and a ``vitruvio.toml`` beside it.

        See :meth:`vitruvio.runtime.ops.lifecycle.LifecycleOps.init`."""
        return self.lifecycle_ops.init(force=force)

    def state(self) -> dict[str, Any]:
        """The brain's head pointer, snapshot and installed modules.

        See :meth:`vitruvio.runtime.ops.lifecycle.LifecycleOps.state`."""
        return self.lifecycle_ops.state()

    def verify(self) -> dict[str, Any]:
        """Recompute every module's Merkle root from its blocks and compare.

        See :meth:`vitruvio.runtime.ops.lifecycle.LifecycleOps.verify`."""
        return self.lifecycle_ops.verify()

    def history(self, *, limit: int | None = None) -> dict[str, Any]:
        """The retained snapshots, most recent first.

        See :meth:`vitruvio.runtime.ops.lifecycle.LifecycleOps.history`."""
        return self.lifecycle_ops.history(limit=limit)

    def info(self) -> dict[str, Any]:
        """Per-module shape: roots, block counts, and which indices are registered.

        See :meth:`vitruvio.runtime.ops.lifecycle.LifecycleOps.info`."""
        return self.lifecycle_ops.info()

    # --- Inspection -----------------------------------------------------------

    @cached_property
    def inspection_ops(self) -> InspectionOps:
        """The composition-reading operations."""
        return InspectionOps(self.session)

    def resolvability(self) -> dict[str, Any]:
        """Which blocks are readable, which are tombstoned, and which are simply absent.

        See :meth:`vitruvio.runtime.ops.inspection.InspectionOps.resolvability`."""
        return self.inspection_ops.resolvability()

    def resolve(self, block_id: str) -> dict[str, Any]:
        """Read one block by identity, verified by hash on the way out of the store.

        See :meth:`vitruvio.runtime.ops.inspection.InspectionOps.resolve`."""
        return self.inspection_ops.resolve(block_id)

    def prove(self, block_id: str, memory_type: str) -> dict[str, Any]:
        """A Merkle inclusion proof for one block, already checked against the module's root.

        See :meth:`vitruvio.runtime.ops.inspection.InspectionOps.prove`."""
        return self.inspection_ops.prove(block_id, memory_type)

    def module(self, memory_type: str, *, limit: int = 20) -> dict[str, Any]:
        """One module's shape and a sample of its block identities.

        See :meth:`vitruvio.runtime.ops.inspection.InspectionOps.module`."""
        return self.inspection_ops.module(memory_type, limit=limit)

    def roots(self) -> dict[str, Any]:
        """Every installed module's Merkle root.

        See :meth:`vitruvio.runtime.ops.inspection.InspectionOps.roots`."""
        return self.inspection_ops.roots()

    # --- Browsing -------------------------------------------------------------

    @cached_property
    def browsing_ops(self) -> BrowsingOps:
        """The browsing operations."""
        return BrowsingOps(self.session)

    def blocks(
        self, memory_type: str, *, limit: int = 100, offset: int = 0, contains: str | None = None
    ) -> dict[str, Any]:
        """One module's blocks, as rows, in the module's own order.

        See :meth:`vitruvio.runtime.ops.browsing.BrowsingOps.blocks`."""
        return self.browsing_ops.blocks(memory_type, limit=limit, offset=offset, contains=contains)

    def content(self, digest: str) -> bytes:
        """The bytes a block names, verified against the digest on the way out of the store.

        See :meth:`vitruvio.runtime.ops.browsing.BrowsingOps.content`."""
        return self.browsing_ops.content(digest)

    def export_content(self, digest: str, destination: Path, *, overwrite: bool = True) -> dict[str, Any]:
        """Write the bytes a block names to a file.

        See :meth:`vitruvio.runtime.ops.browsing.BrowsingOps.export_content`."""
        return self.browsing_ops.export_content(digest, destination, overwrite=overwrite)

    def related(self, block_id: str, *, limit: int = 50) -> dict[str, Any]:
        """The provenance records that name a block: how it got here, and what was done to it since.

        See :meth:`vitruvio.runtime.ops.browsing.BrowsingOps.related`."""
        return self.browsing_ops.related(block_id, limit=limit)

    # --- Canonical registration ----------------------------------------------

    @cached_property
    def registration_ops(self) -> RegistrationOps:
        """The registration operations."""
        return RegistrationOps(self.session)

    def register(
        self,
        path: Path,
        *,
        media_type: str,
        origin: str | None = None,
        license_id: str | None = None,
        retention_policy: str | None = None,
        normalize_with: str | None = None,
    ) -> dict[str, Any]:
        """Register a source as canonical evidence.

        See :meth:`vitruvio.runtime.ops.registration.RegistrationOps.register`."""
        return self.registration_ops.register(
            path,
            media_type=media_type,
            origin=origin,
            license_id=license_id,
            retention_policy=retention_policy,
            normalize_with=normalize_with,
        )

    def replace(
        self,
        path: Path,
        *,
        supersedes: str,
        media_type: str,
        origin: str | None = None,
        license_id: str | None = None,
        normalize_with: str | None = None,
    ) -> dict[str, Any]:
        """Register a newer edition of a source, and record that it supersedes the old one.

        See :meth:`vitruvio.runtime.ops.registration.RegistrationOps.replace`."""
        return self.registration_ops.replace(
            path,
            supersedes=supersedes,
            media_type=media_type,
            origin=origin,
            license_id=license_id,
            normalize_with=normalize_with,
        )

    def put_content(self, path: Path, *, media_type: str) -> dict[str, Any]:
        """Store bytes addressably without registering a canonical block.

        See :meth:`vitruvio.runtime.ops.registration.RegistrationOps.put_content`."""
        return self.registration_ops.put_content(path, media_type=media_type)

    # --- The task lifecycle ---------------------------------------------------

    DUPLICATE = TaskOps.DUPLICATE
    """The one rejection code that is not a defect in the proposal.

    Re-exported from :class:`~vitruvio.runtime.ops.tasks.TaskOps` rather than left behind: it is public, and a
    caller reading a validation report branches on it."""

    @cached_property
    def task_ops(self) -> TaskOps:
        """The tasks operations."""
        return TaskOps(self.session)

    def define_task(
        self,
        source: str,
        *,
        allowed: Iterable[str] | None = None,
        requirements: Iterable[str] | None = None,
        instructions: str | None = None,
        task_id: str | None = None,
        replacing: str | None = None,
    ) -> dict[str, Any]:
        """Define what an external model is being asked to do with one canonical block.

        See :meth:`vitruvio.runtime.ops.tasks.TaskOps.define_task`."""
        return self.task_ops.define_task(
            source,
            allowed=allowed,
            requirements=requirements,
            instructions=instructions,
            task_id=task_id,
            replacing=replacing,
        )

    def task_schema(self, task: dict[str, Any]) -> dict[str, Any]:
        """The JSON Schema a proposal for this task must satisfy.

        See :meth:`vitruvio.runtime.ops.tasks.TaskOps.task_schema`."""
        return self.task_ops.task_schema(task)

    def validate_candidates(self, candidates: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
        """Run the validation gate over a candidate set, without committing anything.

        See :meth:`vitruvio.runtime.ops.tasks.TaskOps.validate_candidates`."""
        return self.task_ops.validate_candidates(candidates, task)

    def commit_candidates(self, candidates: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
        """Validate and commit in one step, refusing if anything was rejected for a reason worth fixing.

        See :meth:`vitruvio.runtime.ops.tasks.TaskOps.commit_candidates`."""
        return self.task_ops.commit_candidates(candidates, task)

    def ingest_run(
        self,
        path: Path,
        *,
        media_type: str,
        proposer: str = "structure",
        allowed: Iterable[str] | None = None,
        normalize_with: str | None = None,
        subject: str | None = None,
        origin: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """The whole path in one call: register, define, propose, validate, commit.

        See :meth:`vitruvio.runtime.ops.tasks.TaskOps.ingest_run`."""
        return self.task_ops.ingest_run(
            path,
            media_type=media_type,
            proposer=proposer,
            allowed=allowed,
            normalize_with=normalize_with,
            subject=subject,
            origin=origin,
            dry_run=dry_run,
        )

    def pipelines(self) -> dict[str, Any]:
        """Every normalization pipeline this build can run.

        See :meth:`vitruvio.runtime.ops.tasks.TaskOps.pipelines`."""
        return self.task_ops.pipelines()

    # --- Declared sources -----------------------------------------------------

    @cached_property
    def source_ops(self) -> SourceOps:
        """The sources operations."""
        return SourceOps(self.session)

    def sources(self) -> dict[str, Any]:
        """Every declared source, whether it can be used, and where its kind came from.

        See :meth:`vitruvio.runtime.ops.sources.SourceOps.sources`."""
        return self.source_ops.sources()

    def source_kinds(self) -> dict[str, Any]:
        """Every source kind this installation can construct.

        See :meth:`vitruvio.runtime.ops.sources.SourceOps.source_kinds`."""
        return self.source_ops.source_kinds()

    def scaffold_source(self, kind: str, *, force: bool = False) -> dict[str, Any]:
        """Write a starter plugin for one kind into the user's plugin directory.

        See :meth:`vitruvio.runtime.ops.sources.SourceOps.scaffold_source`."""
        return self.source_ops.scaffold_source(kind, force=force)

    def add_source(
        self,
        name: str,
        *,
        kind: str,
        path: str | None = None,
        media_type: str | None = None,
        normalize_with: str | None = None,
        license_id: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Declare a source in ``vitruvio.toml``.

        See :meth:`vitruvio.runtime.ops.sources.SourceOps.add_source`."""
        return self.source_ops.add_source(
            name,
            kind=kind,
            path=path,
            media_type=media_type,
            normalize_with=normalize_with,
            license_id=license_id,
            options=options,
        )

    def remove_source(self, name: str) -> dict[str, Any]:
        """Undeclare a source. Nothing it ever registered is touched.

        See :meth:`vitruvio.runtime.ops.sources.SourceOps.remove_source`."""
        return self.source_ops.remove_source(name)

    def pull_source(
        self,
        name: str,
        *,
        dry_run: bool = False,
        limit: int | None = None,
        refetch: bool = False,
        option_overrides: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Acquire from one declared source and register what is new as canonical evidence.

        See :meth:`vitruvio.runtime.ops.sources.SourceOps.pull_source`."""
        return self.source_ops.pull_source(
            name, dry_run=dry_run, limit=limit, refetch=refetch, option_overrides=option_overrides
        )

    def pull_all(self, *, dry_run: bool = False, limit: int | None = None, refetch: bool = False) -> dict[str, Any]:
        """Pull every source declared by the selected brain.

        See :meth:`vitruvio.runtime.ops.sources.SourceOps.pull_all`."""
        return self.source_ops.pull_all(dry_run=dry_run, limit=limit, refetch=refetch)

    # --- Retention ------------------------------------------------------------

    @cached_property
    def retention_ops(self) -> RetentionOps:
        """The retention operations."""
        return RetentionOps(self.session)

    def plan_drop(
        self, blocks: Iterable[str], *, memory_type: str, reason: str = "requested", rederive_against: str | None = None
    ) -> dict[str, Any]:
        """What a drop would take with it, without writing anything.

        See :meth:`vitruvio.runtime.ops.retention.RetentionOps.plan_drop`."""
        return self.retention_ops.plan_drop(
            blocks, memory_type=memory_type, reason=reason, rederive_against=rederive_against
        )

    def drop(
        self, blocks: Iterable[str], *, memory_type: str, reason: str = "requested", rederive_against: str | None = None
    ) -> dict[str, Any]:
        """Exclude blocks from a module, cascading through provenance.

        See :meth:`vitruvio.runtime.ops.retention.RetentionOps.drop`."""
        return self.retention_ops.drop(
            blocks, memory_type=memory_type, reason=reason, rederive_against=rederive_against
        )

    def drop_by_producer(
        self,
        producer_id: str,
        *,
        kind: str = "model",
        version: str | None = None,
        memory_types: Iterable[str] | None = None,
        reason: str = "producer invalidated",
    ) -> dict[str, Any]:
        """Drop everything one producer derived.

        See :meth:`vitruvio.runtime.ops.retention.RetentionOps.drop_by_producer`."""
        return self.retention_ops.drop_by_producer(
            producer_id, kind=kind, version=version, memory_types=memory_types, reason=reason
        )

    def supersede(self, block: str, *, superseded: str, memory_type: str, reason: str | None = None) -> dict[str, Any]:
        """Record that one block takes precedence over another, without changing membership.

        See :meth:`vitruvio.runtime.ops.retention.RetentionOps.supersede`."""
        return self.retention_ops.supersede(block, superseded=superseded, memory_type=memory_type, reason=reason)

    def demote(self, block: str, *, memory_type: str, reason: str | None = None) -> dict[str, Any]:
        """Lower a block's retrieval priority without removing it.

        See :meth:`vitruvio.runtime.ops.retention.RetentionOps.demote`."""
        return self.retention_ops.demote(block, memory_type=memory_type, reason=reason)

    def prune(self, *, apply: bool = False) -> dict[str, Any]:
        """Reclaim blobs unreachable from every retained root.

        See :meth:`vitruvio.runtime.ops.retention.RetentionOps.prune`."""
        return self.retention_ops.prune(apply=apply)

    def redact(self, block: str, *, memory_type: str, reason: str) -> dict[str, Any]:
        """Destroy a block's bytes while a retained root still names it.

        See :meth:`vitruvio.runtime.ops.retention.RetentionOps.redact`."""
        return self.retention_ops.redact(block, memory_type=memory_type, reason=reason)

    def policy(self) -> dict[str, Any]:
        """The retention policy in force, and what it permits.

        See :meth:`vitruvio.runtime.ops.retention.RetentionOps.policy`."""
        return self.retention_ops.policy()

    # --- Indices --------------------------------------------------------------

    @cached_property
    def index_ops(self) -> IndexOps:
        """The indices operations."""
        return IndexOps(self.session)

    def index_list(self) -> dict[str, Any]:
        """Every registered index, with what it holds and where it lives.

        See :meth:`vitruvio.runtime.ops.indices.IndexOps.index_list`."""
        return self.index_ops.index_list()

    def index_build(self, *, memory_types: Iterable[str] | None = None, force: bool = False) -> dict[str, Any]:
        """Build or refresh the indices, and persist the statistics they measured.

        See :meth:`vitruvio.runtime.ops.indices.IndexOps.index_build`."""
        return self.index_ops.index_build(memory_types=memory_types, force=force)

    def index_stats(self, *, memory_type: str | None = None) -> dict[str, Any]:
        """The statistics catalogue, as the planner sees it.

        See :meth:`vitruvio.runtime.ops.indices.IndexOps.index_stats`."""
        return self.index_ops.index_stats(memory_type=memory_type)

    def index_verify(self) -> dict[str, Any]:
        """Check each index against the composition it claims to describe.

        See :meth:`vitruvio.runtime.ops.indices.IndexOps.index_verify`."""
        return self.index_ops.index_verify()

    def index_gc(self, *, apply: bool = False) -> dict[str, Any]:
        """Remove index files no declared index owns.

        See :meth:`vitruvio.runtime.ops.indices.IndexOps.index_gc`."""
        return self.index_ops.index_gc(apply=apply)

    # --- Benchmarking ---------------------------------------------------------

    @cached_property
    def benchmark_ops(self) -> BenchmarkOps:
        """The benchmarking operations."""
        return BenchmarkOps(self.session)

    def bench(self, *, tier: int = 1000, seed: int = 1234, queries: int = 24, limit: int = 10) -> dict[str, Any]:
        """Generate a corpus with known answers, and measure four retrieval strategies over it.

        See :meth:`vitruvio.runtime.ops.benchmarking.BenchmarkOps.bench`."""
        return self.benchmark_ops.bench(tier=tier, seed=seed, queries=queries, limit=limit)

    # --- Embedders ------------------------------------------------------------

    @cached_property
    def embedder_ops(self) -> EmbedderOps:
        """The embedding operations."""
        return EmbedderOps(self.session)

    def embedders(self) -> dict[str, Any]:
        """Every embedding provider this build knows, whether it can run, and what is configured.

        See :meth:`vitruvio.runtime.ops.embedders.EmbedderOps.embedders`."""
        return self.embedder_ops.embedders()

    def test_embedder(self, *, which: str = "text", text: str | None = None) -> dict[str, Any]:
        """Actually embed something, and report what came back.

        See :meth:`vitruvio.runtime.ops.embedders.EmbedderOps.test_embedder`."""
        return self.embedder_ops.test_embedder(which=which, text=text)

    # --- The project ----------------------------------------------------------

    @cached_property
    def project_ops(self) -> ProjectOps:
        """The projects operations."""
        return ProjectOps(self.session)

    def project(self) -> dict[str, Any]:
        """Every brain this project holds, where each one lives, and where each one publishes.

        See :meth:`vitruvio.runtime.ops.projects.ProjectOps.project`."""
        return self.project_ops.project()

    def add_brain(
        self,
        name: str,
        *,
        path: str | None = None,
        description: str | None = None,
        reference: str | None = None,
        create: bool = True,
        publish: bool = True,
    ) -> dict[str, Any]:
        """Register a brain in the project, creating its layout when it does not exist yet.

        See :meth:`vitruvio.runtime.ops.projects.ProjectOps.add_brain`."""
        return self.project_ops.add_brain(
            name, path=path, description=description, reference=reference, create=create, publish=publish
        )

    def remove_brain(self, name: str) -> dict[str, Any]:
        """Unregister a brain from the project. The layout on disk is left alone.

        See :meth:`vitruvio.runtime.ops.projects.ProjectOps.remove_brain`."""
        return self.project_ops.remove_brain(name)

    # --- Distribution ---------------------------------------------------------

    @cached_property
    def remote_ops(self) -> RemoteOps:
        """The remote operations."""
        return RemoteOps(self.session)

    def reference_for(self, given: str | None = None) -> str:
        """Which repository this brain publishes to or pulls from.

        See :meth:`vitruvio.runtime.ops.remote.RemoteOps.reference_for`."""
        return self.remote_ops.reference_for(given)

    @cached_property
    def publish_ops(self) -> PublishOps:
        """The publish operations."""
        return PublishOps(self.session)

    def pack(self, *, tag: str | None = None, modules: Iterable[str] | None = None) -> dict[str, Any]:
        """Build the OCI artifact locally, without pushing.

        See :meth:`vitruvio.runtime.ops.publish.PublishOps.pack`."""
        return self.publish_ops.pack(tag=tag, modules=modules)

    def registry_check(
        self,
        reference: str | None = None,
        *,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """Test a registry with an artifact shaped exactly like a brain.

        See :meth:`vitruvio.runtime.ops.publish.PublishOps.registry_check`."""
        return self.publish_ops.registry_check(
            reference, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )

    def push(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        force: bool = False,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """Publish the brain.

        See :meth:`vitruvio.runtime.ops.publish.PublishOps.push`."""
        return self.publish_ops.push(
            reference,
            tag=tag,
            modules=modules,
            force=force,
            username=username,
            token=token,
            anonymous=anonymous,
            insecure=insecure,
            local=local,
        )

    def tags(
        self,
        reference: str | None = None,
        *,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """Which tags a repository holds.

        See :meth:`vitruvio.runtime.ops.publish.PublishOps.tags`."""
        return self.publish_ops.tags(
            reference, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )

    @cached_property
    def install_ops(self) -> InstallOps:
        """The install operations."""
        return InstallOps(self.session)

    def plan_pull(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        ignore_vector_indices: bool = False,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """Report what a pull would transfer, before transferring it.

        See :meth:`vitruvio.runtime.ops.install.InstallOps.plan_pull`."""
        return self.install_ops.plan_pull(
            reference,
            tag=tag,
            modules=modules,
            ignore_vector_indices=ignore_vector_indices,
            username=username,
            token=token,
            anonymous=anonymous,
            insecure=insecure,
            local=local,
        )

    def pull(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        ignore_vector_indices: bool = False,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """Install a published brain.

        See :meth:`vitruvio.runtime.ops.install.InstallOps.pull`."""
        return self.install_ops.pull(
            reference,
            tag=tag,
            modules=modules,
            ignore_vector_indices=ignore_vector_indices,
            username=username,
            token=token,
            anonymous=anonymous,
            insecure=insecure,
            local=local,
        )

    def fetch(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        modules: Iterable[str] | None = None,
        reconcile: bool = True,
        reason: str | None = None,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> dict[str, Any]:
        """Retrieve a remote history without moving the pointer, and reconcile it when that is safe.

        See :meth:`vitruvio.runtime.ops.install.InstallOps.fetch`."""
        return self.install_ops.fetch(
            reference,
            tag=tag,
            modules=modules,
            reconcile=reconcile,
            reason=reason,
            username=username,
            token=token,
            anonymous=anonymous,
            insecure=insecure,
            local=local,
        )

    # --- Reconciliation -------------------------------------------------------

    @cached_property
    def reconcile_ops(self) -> ReconcileOps:
        """The reconciliation operations.

        Reached as a property rather than forwarded method by method, which is the one place this facade
        departs from "one method per protocol operation" -- and it departs because the ratchet asked it to.
        `PLR0904` exists so that a domain's worth of logic landing back here trips a lint instead of being
        noticed at 3000 lines, and eight more delegations tripped it. Raising the threshold would have spent
        the mechanism to avoid the refactor it was installed to force.

        It costs little, because reconciliation is the one domain whose operations are never called alone:
        plan, start, decide, conclude is a loop, and every caller of it -- the command group and the
        interactive resolver alike -- holds one object for the whole walk rather than reaching for eight."""
        return ReconcileOps(self.session)

    # --- Retrieval ------------------------------------------------------------

    @cached_property
    def retrieval_ops(self) -> RetrievalOps:
        """The retrieval operations."""
        return RetrievalOps(self.session)

    def search(
        self,
        text: str = "",
        *,
        memory_types: Iterable[str] | None = None,
        subject: str | None = None,
        since: str | None = None,
        until: str | None = None,
        tags: Iterable[str] | None = None,
        evidence: Iterable[str] | None = None,
        include_superseded: bool = False,
        mode: str | None = None,
        limit: int = 10,
        expand_depth: int = 0,
        diagnostics: bool = False,
    ) -> dict[str, Any]:
        """Retrieve evidence.

        See :meth:`vitruvio.runtime.ops.retrieval.RetrievalOps.search`."""
        return self.retrieval_ops.search(
            text,
            memory_types=memory_types,
            subject=subject,
            since=since,
            until=until,
            tags=tags,
            evidence=evidence,
            include_superseded=include_superseded,
            mode=mode,
            limit=limit,
            expand_depth=expand_depth,
            diagnostics=diagnostics,
        )

    def explain(
        self,
        text: str = "",
        *,
        memory_types: Iterable[str] | None = None,
        subject: str | None = None,
        since: str | None = None,
        until: str | None = None,
        tags: Iterable[str] | None = None,
        evidence: Iterable[str] | None = None,
        include_superseded: bool = False,
        mode: str | None = None,
        limit: int = 10,
        expand_depth: int = 0,
        analyze: bool = False,
    ) -> dict[str, Any]:
        """Report how a query would be answered, or was.

        See :meth:`vitruvio.runtime.ops.retrieval.RetrievalOps.explain`."""
        return self.retrieval_ops.explain(
            text,
            memory_types=memory_types,
            subject=subject,
            since=since,
            until=until,
            tags=tags,
            evidence=evidence,
            include_superseded=include_superseded,
            mode=mode,
            limit=limit,
            expand_depth=expand_depth,
            analyze=analyze,
        )
