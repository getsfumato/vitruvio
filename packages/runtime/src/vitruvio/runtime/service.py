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
from contextlib import suppress
from functools import cached_property
from inspect import signature
from pathlib import Path
from typing import Any

from boltzmann.brain import Brain

from vitruvio.kernel import ResolvedConfig, VitruvioError
from vitruvio.runtime import wire
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.coerce import memory_type as _memory_type
from vitruvio.runtime.mapping import translate
from vitruvio.runtime.mapping import translated as _translated
from vitruvio.runtime.ops.benchmarking import BenchmarkOps
from vitruvio.runtime.ops.browsing import BrowsingOps
from vitruvio.runtime.ops.embedders import EmbedderOps
from vitruvio.runtime.ops.indices import IndexOps
from vitruvio.runtime.ops.inspection import InspectionOps
from vitruvio.runtime.ops.lifecycle import LifecycleOps
from vitruvio.runtime.ops.projects import ProjectOps
from vitruvio.runtime.ops.registration import RegistrationOps
from vitruvio.runtime.ops.retention import RetentionOps
from vitruvio.runtime.ops.retrieval import RetrievalOps
from vitruvio.runtime.ops.sources import SourceOps
from vitruvio.runtime.ops.tasks import TaskOps
from vitruvio.runtime.session import BrainSession


def _require_vector_index_ignore(method: Any) -> None:
    """Fail clearly when the CLI feature is used with an SDK that predates the supporting pull contract."""
    if "ignore_vector_indices" in signature(method).parameters:
        return
    raise VitruvioError(
        "the installed pyboltzmann does not support ignoring vector indices during a pull",
        hint="upgrade pyboltzmann to a release whose Brain.pull exposes `ignore_vector_indices`, then retry",
    )


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

    def export_content(self, digest: str, destination: Path) -> dict[str, Any]:
        """Write the bytes a block names to a file.

        See :meth:`vitruvio.runtime.ops.browsing.BrowsingOps.export_content`."""
        return self.browsing_ops.export_content(digest, destination)

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

    def reference_for(self, given: str | None = None) -> str:
        """
        Which repository this brain publishes to or pulls from.

        Four layers, and the lookups get more expensive as they go, so each is tried only when the ones before it
        came up empty:

        1. what the command was given;
        2. this brain's own ``reference``, or one derived from ``[registry].namespace``;
        3. one derived from whichever registry account is logged in -- the case that makes
           ``registry login --from-docker`` once enough for a whole project;
        4. nothing, and an error that names all three ways to fix it.

        Args:
            given (str | None): An explicit reference from the command line.

        Returns:
            str: The repository, without a tag.

        Raises:
            VitruvioError: If no layer names one.
        """
        from vitruvio.runtime.distribution import require_reference

        if given:
            return given

        configured = self.config.repository()
        if configured is None:
            # Only now: this reads the keyring and possibly runs a credential helper, which is not something to
            # do on a command that already knew its destination.
            from vitruvio.runtime.registry import account_for

            configured = self.config.repository(account_for())
        return require_reference(configured, None)

    def _client(
        self,
        reference: str,
        *,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        allow_docker: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> tuple[Any, str, list[str]]:
        """
        A registry client, the effective reference, and anything worth warning about.

        ``local`` selects a filesystem registry of OCI layouts. Not a mock: it goes through the same
        ``resolve``/``pull_blob``/``push`` contract the SDK defines, so the travelling-index path can be exercised
        end to end with no network, no credentials and no rate limits -- which is the right thing to prove before
        pointing anything at Docker Hub.
        """
        from vitruvio.runtime.registry import build_client, credential_for, normalize_reference

        if local is not None:
            from vitruvio.runtime.distribution import local_registry

            # A local layout has no host, so the reference is used verbatim as a repository name under `local`.
            return local_registry(local), reference, []

        _, effective = normalize_reference(reference)
        credential = credential_for(
            reference, username=username, token=token, anonymous=anonymous, allow_docker=allow_docker
        )
        client, warnings = build_client(
            reference,
            credential,
            insecure=self.config.project.registry.insecure if insecure is None else insecure,
        )
        return client, effective, warnings

    def pack(self, *, tag: str | None = None, modules: Iterable[str] | None = None) -> dict[str, Any]:
        """
        Build the OCI artifact locally, without pushing.

        Vouches for the vector index first: without that, ``pack`` silently omits the one layer a consumer cannot
        rebuild. See :mod:`vitruvio.runtime.vouch`.

        Args:
            tag (str | None): The tag to file it under.
            modules (Iterable[str] | None): Publish only these modules.

        Returns:
            dict[str, Any]: The manifest, with the digest a registry would file it under.
        """
        from vitruvio.runtime.vouch import vouch_travelling

        chosen = [_memory_type(item) for item in modules] if modules else None
        brain = self.brain(Capability.WRITE)
        vouched = vouch_travelling(brain, chosen)

        with _translated():
            manifest = brain.pack(tag=tag or self.config.project.registry.tag, modules=chosen)
        return {**wire.manifest(manifest), "vouched": vouched}

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
        """
        Test a registry with an artifact shaped exactly like a brain.

        Answers the question that a first push otherwise answers the hard way: does this registry accept a custom
        ``config.mediaType``? Checked rather than assumed, because the manifest's shape is fixed by the protocol.

        Returns:
            dict[str, Any]: Per-check outcomes, and a hint naming the real alternatives when it fails.
        """
        import asyncio

        from vitruvio.runtime.distribution import preflight

        target = self.reference_for(reference)
        client, _, warnings = self._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        brain = self.brain(Capability.INSPECT)
        with _translated():
            result = asyncio.run(preflight(target, client, brain.store))
        return {**result, "warnings": warnings}

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
        """
        Publish the brain.

        The SDK's own guards apply: a push that would narrow the module set is refused, and a push that is not a
        fast-forward is refused -- the latter failing *closed* on any error that is not a 404, so a refusal that looks
        like an absence cannot disable the check.

        Returns:
            dict[str, Any]: The digest the registry filed the manifest under.

        Raises:
            PublishForbiddenError: If the brain declares ``publish = false``. Checked first, before the reference is
                resolved and before a credential is read, because a refusal that happens after a credential lookup
                has already told a keyring what you were about to do.
        """
        import asyncio

        from vitruvio.runtime.vouch import vouch_travelling

        self._require_publishable()
        target = self.reference_for(reference)
        chosen = [_memory_type(item) for item in modules] if modules else None
        client, effective, warnings = self._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )

        brain = self.brain(Capability.WRITE)
        vouched = vouch_travelling(brain, chosen)
        with _translated():
            digest = asyncio.run(
                brain.push(
                    client,
                    reference=effective,
                    tag=tag or self.config.project.registry.tag,
                    force=force,
                    modules=chosen,
                )
            )
        return {
            "reference": target,
            "effective": effective,
            "tag": tag or self.config.project.registry.tag,
            "digest": str(digest),
            "vouched": vouched,
            "warnings": warnings,
        }

    def _require_publishable(self) -> None:
        """
        Refuse a push the project declared off-limits.

        The mistake this prevents is one command long and made by someone who does not expect to make it. A pulled
        brain is a working copy like any other -- nothing in the protocol distinguishes a brain you authored from one
        you installed -- so a stray ``dist push`` publishes a fork of somebody else's brain under whichever
        repository this project derives, and the two lineages diverge with nobody informed.

        Raises:
            PublishForbiddenError: If the selected brain declares ``publish = false``.
        """
        from vitruvio.kernel import PublishForbiddenError

        if self.config.publish_allowed:
            return
        name = self.config.brain_name or str(self.config.brain)
        raise PublishForbiddenError(
            f"brain {name!r} declares publish = false, so it is not published from here",
            hint=(
                "this is usually somebody else's upstream. If you really mean to publish a fork, set "
                f"publish = true under [brains.{name}] and give it its own `reference` first"
            ),
        )

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
        """
        Report what a pull would transfer, before transferring it.

        A canonical layer can be gigabytes, so "how much will this cost" has to be answerable without paying it.

        Reports ``local_work`` as well as the transfer, because cost is not the only thing worth knowing before a
        pull: an install adopts the remote composition, so anything committed here since the last pull stops being a
        member of it. Answered from the local head and nothing else, so it costs no extra round trip.

        Returns:
            dict[str, Any]: The plan, with the byte count taken from the resolved manifest.
        """
        import asyncio

        target = self.reference_for(reference)
        chosen = [_memory_type(item) for item in modules] if modules else None
        client, effective, warnings = self._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        wanted_tag = tag or self.config.project.registry.tag

        brain = self.brain(Capability.INSPECT)
        with _translated():
            manifest = asyncio.run(client.resolve(effective, wanted_tag))
            if ignore_vector_indices:
                _require_vector_index_ignore(brain.plan_pull)
                plan = asyncio.run(
                    brain.plan_pull(
                        client,
                        effective,
                        wanted_tag,
                        modules=chosen,
                        ignore_vector_indices=True,
                    )
                )
            else:
                # Keep the ordinary pull compatible with the previous SDK API. Only the new opt-in path requires
                # the SDK release that added `ignore_vector_indices`.
                plan = asyncio.run(brain.plan_pull(client, effective, wanted_tag, modules=chosen))
        return {
            "reference": target,
            "tag": wanted_tag,
            **wire.install_plan(plan, manifest),
            "local_work": self._local_work(brain),
            "warnings": warnings,
        }

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
        """
        Install a published brain.

        Returns:
            dict[str, Any]: The snapshot now installed.
        """
        import asyncio

        target = self.reference_for(reference)
        chosen = [_memory_type(item) for item in modules] if modules else None
        client, effective, warnings = self._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        wanted_tag = tag or self.config.project.registry.tag

        brain = self.brain(Capability.WRITE)
        # Captured before, because after the pull the composition is the remote's and there is nothing left to
        # compare against. This is the only place the count can be exact rather than estimated.
        before = self._composition_ids(brain)
        ignored: list[str] = []
        with _translated():
            if ignore_vector_indices:
                _require_vector_index_ignore(brain.pull)
                manifest = asyncio.run(client.resolve(effective, wanted_tag))
                wanted = chosen if chosen is not None else manifest.modules
                ignored = [
                    memory_type.value for memory_type in wanted if manifest.vector_index_for(memory_type) is not None
                ]
                snapshot = asyncio.run(
                    brain.pull(
                        client,
                        effective,
                        wanted_tag,
                        modules=chosen,
                        ignore_vector_indices=True,
                    )
                )
            else:
                snapshot = asyncio.run(brain.pull(client, effective, wanted_tag, modules=chosen))
        orphaned = sorted(before - self._composition_ids(brain))
        # `plan_pull` may already have memoized an INSPECT-capability brain at the old head. A pull advances the
        # pointer through the WRITE-capability instance, so every other cached view must be reopened before a caller
        # asks for state or verification on this same service object.
        self.session.invalidate()
        if ignored:
            named = ", ".join(ignored)
            warnings.append(
                f"ignored published vector indices for {named}; run `vitruvio index build --force` to build "
                "compatible local vectors before relying on semantic retrieval"
            )
        return {
            "reference": target,
            "tag": wanted_tag,
            "snapshot": wire.snapshot(snapshot),
            "partial": chosen is not None,
            "discarded": len(orphaned),
            "discarded_blocks": orphaned[:20],
            "ignored_vector_indices": ignored,
            "warnings": warnings,
        }

    # --- What a pull would replace ---------------------------------------------
    #
    # `pull` adopts the remote snapshot verbatim and moves the head to it, with no fast-forward check -- the
    # divergence guard lives on `push`, where overwriting means overwriting somebody *else's* work. That asymmetry
    # is right: an install installs the other side's version.
    #
    # What was missing is that the loss was silent. Blocks committed locally since the last pull stop being members
    # of any composition: they do not verify into a root, they do not appear in a search, and a pack does not carry
    # them. The blobs stay on disk and the previous snapshot stays in `retained`, so the state is recoverable by
    # hand -- but nothing said it happened, and the discovery came days later when a search returned nothing.

    def _local_work(self, brain: Brain) -> dict[str, Any]:
        """
        What is installed here that no pull put here.

        Answered from ``Origin``, which records the snapshot digest of the last pull, so the question "did I commit
        anything since?" is a local comparison and costs no round trip. The count is a delta between two snapshot
        documents rather than a set difference, because a plan must not download a composition to answer it.

        Args:
            brain (Brain): The opened brain.

        Returns:
            dict[str, Any]: ``diverged``, how many blocks are at stake, and which snapshot holds them.
        """
        snapshot = brain.snapshot()
        installed = sum(reference.block_count for reference in snapshot.modules.values())
        origin = brain.origin
        clean = {"diverged": False, "blocks": 0, "snapshot": None, "pulled": None}

        if installed == 0:
            return clean
        if origin is None:
            # Never pulled, and it holds blocks: everything in it is local, and a pull replaces the lot.
            return {"diverged": True, "blocks": installed, "snapshot": str(snapshot.digest), "pulled": None}
        if str(snapshot.digest) == str(origin.snapshot):
            return clean

        baseline = self._snapshot_at(brain, str(origin.snapshot))
        blocks = None if baseline is None else max(installed - baseline, 0)
        return {
            "diverged": True,
            "blocks": blocks,
            "snapshot": str(snapshot.digest),
            "pulled": str(origin.snapshot),
        }

    @staticmethod
    def _snapshot_at(brain: Brain, digest: str) -> int | None:
        """
        How many blocks one retained snapshot held, or ``None`` when it can no longer be read.

        ``None`` rather than zero: a missing baseline means the size of the local work is *unknown*, and reporting
        an unknown as "nothing" is the failure this whole report exists to prevent.
        """
        from boltzmann.brain import Snapshot
        from boltzmann.identity.digest import OciDigest

        try:
            document = brain.store.get_bytes(OciDigest.parse(digest))
        # Broad on purpose: a pruned or unreadable blob is not an error here, it is an unknown.
        except Exception:
            return None
        try:
            return sum(reference.block_count for reference in Snapshot.model_validate_json(document).modules.values())
        except ValueError:  # pragma: no cover - a blob that is not a snapshot document
            return None

    @staticmethod
    def _composition_ids(brain: Brain) -> set[str]:
        """Every block identity currently a member of some installed module."""
        found: set[str] = set()
        for kind in brain.snapshot().installed:
            with suppress(Exception):
                found.update(str(identity) for identity in brain.module(kind).block_ids)
        return found

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
        """
        Which tags a repository holds.

        Returns:
            dict[str, Any]: The tags, or an explanation when the registry does not offer a listing.
        """
        target = self.reference_for(reference)
        client, effective, warnings = self._client(
            target, username=username, token=token, anonymous=anonymous, insecure=insecure, local=local
        )
        from boltzmann.exceptions import DistributionError, ReferenceNotFoundError

        lister = getattr(client, "tags", None)
        try:
            if lister is None:
                found = sorted(client.registry.get_tags(effective))
            else:
                found = sorted(lister(effective))
        except (DistributionError, ReferenceNotFoundError):
            # A repository with nothing published is the ordinary state before a first push, and "no tags" is the
            # answer -- not an error, and certainly not an internal one, which is what an unwrapped raise produced.
            found = []
        except Exception as error:
            raise translate(error) from error

        return {"reference": target, "tags": found, "warnings": warnings, "published": bool(found)}

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
