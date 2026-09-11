"""The authoritative catalogue of runtime operations and what is true about each one.

An operation is declared here once. The generated facade, its documentation metadata, and the conformance tests
all read this catalogue, so adding an operation cannot leave one of those three views behind.

What is declared is not only that an operation exists. An interface that is not the CLI has to answer five more
questions before it can offer one safely -- may this caller run it, does it change anything, can its result be put
in an envelope, does it name a location on this host, and how long might it take -- and every one of those
answers used to be recoverable only by reading the implementation. Some of them were not recoverable at all:
``push`` and ``push_async`` are one operation and nothing said so, so a loop over this catalogue would have
offered twelve distribution tools where there are six.

The facts are declared rather than derived because deriving them is what does not work. Forty of the ninety-four
operations name no capability anywhere in their body; ``pull_source`` chooses between INSPECT and WRITE at
runtime from ``dry_run``; ``doctor`` reaches its brain through a private helper; ``add_brain``, ``bench`` and the
compound operations open a brain that is not this session's. A declaration can be checked against the code, and
``tests/test_operation_catalogue.py`` checks every one of them; an inference cannot be checked against anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum

from vitruvio.runtime.capability import Capability


class Exposure(StrEnum):
    """How a domain is reached from :class:`~vitruvio.runtime.BrainService`."""

    FACADE = "facade"
    PROPERTY = "property"


class ResultKind(StrEnum):
    """What an operation hands back, in the terms an interface has to serialize it in."""

    JSON = "json"
    """A plain dictionary of JSON-native values."""
    TYPED = "typed"
    """A declared result type, still JSON-native. See :mod:`vitruvio.runtime.reconcile_result`."""
    SCALAR = "scalar"
    """A single JSON-native value rather than a mapping."""
    BINARY = "binary"
    """Raw bytes. Not serializable into an envelope, and deliberately so."""


class Remote(StrEnum):
    """Whether an operation can be offered to a caller that is not on this machine."""

    EXPOSED = "exposed"
    LOCAL = "local"
    """Names a location on the host running vitruvio, so a remote caller cannot mean anything by it."""


class Cost(StrEnum):
    """Roughly what running it costs, for a caller choosing a timeout."""

    CHEAP = "cheap"
    """Opens no brain."""
    OPENS_BRAIN = "opens-brain"
    """Stands a brain up, which at RETRIEVE and above rebuilds the registered indices."""
    NETWORK = "network"
    """Contacts a registry or a declared source."""
    HEAVY = "heavy"
    """Rehashes, rebuilds or measures a whole brain."""


@dataclass(frozen=True, slots=True)
class Operation:
    """
    One protocol operation, and the facts an interface needs before offering it.

    Attributes:
        name (str): The method on the operations class and on the facade.
        capability (Capability | None): The most a caller must be allowed to do. ``None`` means the operation
            opens no brain at all. Over-declaring is safe and sometimes necessary -- ``compound_search`` opens
            RETRIEVE on each member's session rather than on this one -- so the checked property is that nothing
            in the body asks for *more* than this.
        mutates (bool): Whether it may change durable state: the brain, its derived indices, the configuration
            file, the plugins this installation loads, or any other file it puts on this host. Holding the WRITE
            brain counts, because that alone engages the retention policy and the validation gate and takes the
            session's writer slot. Declared conservatively -- ``doctor`` reaches a registry only under
            ``registry=True`` and still declares ``network``, because a caller choosing a timeout has to assume
            the branch it did not take.
        result (ResultKind): What it hands back.
        remote (Remote): Whether it may be offered to a caller elsewhere.
        network (bool): Whether it contacts a registry or a declared source.
        heavy (bool): Whether it rebuilds or measures the whole brain.
        async_name (str | None): The coroutine that *is* this operation, when one exists. The synchronous method
            is a bridge over ``asyncio.run``; an async interface should await the coroutine directly, and any
            interface should offer the operation once rather than twice.
    """

    name: str
    capability: Capability | None = None
    mutates: bool = False
    result: ResultKind = ResultKind.JSON
    remote: Remote = Remote.EXPOSED
    network: bool = False
    heavy: bool = False
    async_name: str | None = None

    @property
    def names(self) -> tuple[str, ...]:
        """Every method this operation is reachable by, synchronous bridge included."""
        return (self.name,) if self.async_name is None else (self.name, self.async_name)

    @property
    def cost(self) -> Cost:
        """Roughly what running it costs. Derived, so it cannot disagree with the facts it follows from."""
        if self.heavy:
            return Cost.HEAVY
        if self.network:
            return Cost.NETWORK
        return Cost.CHEAP if self.capability is None else Cost.OPENS_BRAIN


@dataclass(frozen=True, slots=True)
class OperationDomain:
    """One operations class and the protocol operations it owns."""

    module: str
    class_name: str
    property_name: str
    operations: tuple[Operation, ...]
    exposure: Exposure = Exposure.FACADE
    exports: tuple[str, ...] = field(default=())

    @property
    def qualified_name(self) -> str:
        """The importable name of the operations class."""
        return f"{self.module}.{self.class_name}"

    @property
    def method_names(self) -> tuple[str, ...]:
        """Every public method this class must expose, in declaration order."""
        return tuple(name for operation in self.operations for name in operation.names)


_INSPECT = Capability.INSPECT
_BROWSE = Capability.BROWSE
_RETRIEVE = Capability.RETRIEVE
_WRITE = Capability.WRITE


OPERATION_CATALOGUE: tuple[OperationDomain, ...] = (
    OperationDomain(
        "vitruvio.runtime.ops.lifecycle",
        "LifecycleOps",
        "lifecycle_ops",
        (
            Operation("init", capability=_INSPECT, mutates=True),
            Operation("state", capability=_INSPECT, result=ResultKind.TYPED),
            Operation("verify", capability=_INSPECT),
            Operation("history", capability=_INSPECT),
            Operation("info", capability=_INSPECT),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.inspection",
        "InspectionOps",
        "inspection_ops",
        (
            Operation("resolvability", capability=_INSPECT),
            Operation("resolve", capability=_INSPECT),
            Operation("prove", capability=_INSPECT),
            Operation("module", capability=_INSPECT),
            Operation("roots", capability=_INSPECT),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.browsing",
        "BrowsingOps",
        "browsing_ops",
        (
            Operation("blocks", capability=_BROWSE, result=ResultKind.TYPED),
            Operation("content", capability=_INSPECT, result=ResultKind.BINARY),
            Operation("content_range", capability=_INSPECT),
            Operation("export_content", capability=_INSPECT, mutates=True, remote=Remote.LOCAL),
            Operation("related", capability=_BROWSE),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.catalog",
        "CatalogOps",
        "catalog_ops",
        (
            Operation("catalog_show", capability=_INSPECT),
            Operation("catalog_tree", capability=_BROWSE),
            Operation("catalog_apply", capability=_WRITE, mutates=True),
            Operation("catalog_browse", capability=_BROWSE),
            Operation("catalog_path", capability=_BROWSE),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.authenticity",
        "AuthenticityOps",
        "authenticity_ops",
        (
            Operation("auth_keys"),
            Operation("auth_status", capability=_INSPECT),
            Operation("auth_trust_root", capability=_INSPECT),
            Operation("auth_sign", capability=_INSPECT, mutates=True),
            Operation("auth_pin", capability=_INSPECT, mutates=True),
            Operation("auth_attribution", capability=_INSPECT),
            Operation("auth_plan_rotation", capability=_INSPECT),
            Operation("auth_countersign", capability=_INSPECT, mutates=True),
            Operation("auth_rotate", capability=_WRITE, mutates=True),
            Operation("auth_revoke", capability=_WRITE, mutates=True),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.migration",
        "MigrationOps",
        "migration_ops",
        (
            Operation("plan_migration", capability=_INSPECT, remote=Remote.LOCAL),
            Operation("migrate", capability=_WRITE, mutates=True, remote=Remote.LOCAL, heavy=True),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.registration",
        "RegistrationOps",
        "registration_ops",
        (
            Operation("register", capability=_WRITE, mutates=True),
            Operation("replace", capability=_WRITE, mutates=True),
            Operation("put_content", capability=_WRITE, mutates=True),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.tasks",
        "TaskOps",
        "task_ops",
        (
            Operation("define_task", capability=_RETRIEVE),
            Operation("task_schema", capability=_RETRIEVE),
            Operation("validate_candidates", capability=_WRITE, mutates=True),
            Operation("commit_candidates", capability=_WRITE, mutates=True),
            Operation("ingest_run", capability=_WRITE, mutates=True, heavy=True),
            Operation("pipelines"),
        ),
        exports=("DUPLICATE",),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.sources",
        "SourceOps",
        "source_ops",
        (
            Operation("sources"),
            Operation("source_kinds"),
            Operation("scaffold_source", mutates=True, remote=Remote.LOCAL),
            Operation("add_source", mutates=True, remote=Remote.LOCAL),
            Operation("remove_source", mutates=True),
            Operation("pull_source", capability=_WRITE, mutates=True, network=True),
            Operation("pull_all", capability=_WRITE, mutates=True, network=True),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.retention",
        "RetentionOps",
        "retention_ops",
        (
            Operation("plan_drop", capability=_WRITE, mutates=True),
            Operation("drop", capability=_WRITE, mutates=True),
            Operation("drop_by_producer", capability=_WRITE, mutates=True),
            Operation("supersede", capability=_WRITE, mutates=True),
            Operation("demote", capability=_WRITE, mutates=True),
            Operation("prune", capability=_WRITE, mutates=True),
            Operation("redact", capability=_WRITE, mutates=True),
            Operation("policy"),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.indices",
        "IndexOps",
        "index_ops",
        (
            Operation("index_list", capability=_INSPECT),
            Operation("index_build", capability=_RETRIEVE, mutates=True, heavy=True),
            Operation("index_stats", capability=_INSPECT),
            Operation("index_verify", capability=_INSPECT),
            Operation("index_gc", mutates=True),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.benchmarking",
        "BenchmarkOps",
        "benchmark_ops",
        (Operation("bench", capability=_RETRIEVE, remote=Remote.LOCAL, heavy=True),),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.embedders",
        "EmbedderOps",
        "embedder_ops",
        (Operation("embedders"), Operation("test_embedder", heavy=True)),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.diagnosis",
        "DiagnosisOps",
        "diagnosis_ops",
        (Operation("doctor", capability=_INSPECT, network=True),),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.projects",
        "ProjectOps",
        "project_ops",
        (
            Operation("project"),
            Operation("add_brain", capability=_INSPECT, mutates=True, remote=Remote.LOCAL),
            Operation("remove_brain", mutates=True),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.remote",
        "RemoteOps",
        "remote_ops",
        (Operation("reference_for", result=ResultKind.SCALAR),),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.publish",
        "PublishOps",
        "publish_ops",
        (
            Operation("pack", capability=_WRITE, mutates=True),
            Operation("registry_check", capability=_INSPECT, network=True, async_name="registry_check_async"),
            Operation("push", capability=_WRITE, mutates=True, network=True, async_name="push_async"),
            Operation("push_all", capability=_WRITE, mutates=True, network=True),
            Operation("tags", network=True),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.install",
        "InstallOps",
        "install_ops",
        (
            Operation("plan_pull", capability=_INSPECT, network=True, async_name="plan_pull_async"),
            Operation("pull", capability=_WRITE, mutates=True, network=True, async_name="pull_async"),
            Operation("fetch", capability=_WRITE, mutates=True, network=True, async_name="fetch_async"),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.reconcile",
        "ReconcileOps",
        "reconcile_ops",
        (
            Operation("declared_strategy", result=ResultKind.SCALAR),
            Operation("contains", capability=_INSPECT, result=ResultKind.SCALAR),
            Operation("plan", capability=_INSPECT, result=ResultKind.TYPED),
            Operation("reconcile", capability=_WRITE, mutates=True, result=ResultKind.TYPED),
            Operation("status", capability=_INSPECT, result=ResultKind.TYPED),
            Operation("resolve", capability=_WRITE, mutates=True, result=ResultKind.TYPED),
            Operation("accept_removals", capability=_WRITE, mutates=True, result=ResultKind.TYPED),
            Operation("continue_", capability=_WRITE, mutates=True, result=ResultKind.TYPED),
            Operation("abort", capability=_WRITE, mutates=True, result=ResultKind.TYPED),
            Operation("tree", capability=_INSPECT),
        ),
        exposure=Exposure.PROPERTY,
    ),
    OperationDomain(
        "vitruvio.runtime.ops.retrieval",
        "RetrievalOps",
        "retrieval_ops",
        (
            Operation("search", capability=_RETRIEVE, result=ResultKind.TYPED),
            Operation("explain", capability=_RETRIEVE, result=ResultKind.TYPED),
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.compound",
        "CompoundOps",
        "compound_ops",
        (
            Operation("compound_search", capability=_RETRIEVE, result=ResultKind.TYPED),
            Operation("compound_explain", capability=_RETRIEVE, result=ResultKind.TYPED),
        ),
    ),
)


def operations() -> Iterator[tuple[OperationDomain, Operation]]:
    """Every declared operation, once, whichever way its domain is exposed."""
    for domain in OPERATION_CATALOGUE:
        yield from ((domain, operation) for operation in domain.operations)


def facade_operations() -> tuple[tuple[OperationDomain, str], ...]:
    """Every method forwarded directly by ``BrainService``, synchronous bridges included."""
    return tuple(
        (domain, name)
        for domain, operation in operations()
        if domain.exposure is Exposure.FACADE
        for name in operation.names
    )


def protocol_operations() -> tuple[tuple[OperationDomain, Operation], ...]:
    """
    The inventory an interface elsewhere would offer: one entry per operation, nothing host-local.

    Distinct from :func:`facade_operations`, which is about Python methods. This is about the protocol, so a
    sync/async pair counts once and an operation naming a path on this machine is absent rather than offered to
    somebody who cannot mean anything by it.
    """
    return tuple((domain, operation) for domain, operation in operations() if operation.remote is Remote.EXPOSED)


def documentation_metadata() -> tuple[dict[str, object], ...]:
    """Stable metadata for documentation and for interfaces built over the catalogue."""
    return tuple(
        {
            "domain": domain.class_name,
            "property": domain.property_name,
            "exposure": domain.exposure.value,
            "operations": tuple(
                {
                    "name": operation.name,
                    "async_name": operation.async_name,
                    "capability": operation.capability.name if operation.capability is not None else None,
                    "mutates": operation.mutates,
                    "result": operation.result.value,
                    "remote": operation.remote.value,
                    "cost": operation.cost.value,
                }
                for operation in domain.operations
            ),
        }
        for domain in OPERATION_CATALOGUE
    )


def operations_table() -> str:
    """
    The module-by-module summary in ``ARCHITECTURE.md``, rendered from the catalogue.

    That table was maintained by hand beside a catalogue that declares the same facts, which is the second owner
    this module exists to remove. ``generate_facade`` writes it in; ``test_operation_catalogue`` fails if the file
    has drifted.
    """
    lines = ["| module | operations | capability | writes |", "|---|---|---|---|"]
    for domain in OPERATION_CATALOGUE:
        module = domain.module.rsplit(".", 2)[-2] + "/" + domain.module.rsplit(".", 1)[-1] + ".py"
        names = ", ".join(operation.name.rstrip("_") for operation in domain.operations)
        levels = sorted({operation.capability for operation in domain.operations if operation.capability is not None})
        capability = ", ".join(level.name for level in levels) or "—"
        writes = "yes" if any(operation.mutates for operation in domain.operations) else "no"
        lines.append(f"| `{module}` | {names} | {capability} | {writes} |")
    return "\n".join(lines)


__all__ = [
    "OPERATION_CATALOGUE",
    "Cost",
    "Exposure",
    "Operation",
    "OperationDomain",
    "Remote",
    "ResultKind",
    "documentation_metadata",
    "facade_operations",
    "operations",
    "operations_table",
    "protocol_operations",
]
