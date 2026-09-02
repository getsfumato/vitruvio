"""The authoritative catalogue of runtime operations and their facade exposure.

An operation is declared here once.  The generated facade, its documentation metadata, and the conformance tests
all read this catalogue, so adding an operation cannot leave one of those three views behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Exposure(StrEnum):
    """How a domain is reached from :class:`~vitruvio.runtime.BrainService`."""

    FACADE = "facade"
    PROPERTY = "property"


@dataclass(frozen=True, slots=True)
class OperationDomain:
    """One operations class and the protocol names it owns."""

    module: str
    class_name: str
    property_name: str
    operations: tuple[str, ...]
    exposure: Exposure = Exposure.FACADE
    exports: tuple[str, ...] = ()

    @property
    def qualified_name(self) -> str:
        """The importable name of the operations class."""
        return f"{self.module}.{self.class_name}"


OPERATION_CATALOGUE: tuple[OperationDomain, ...] = (
    OperationDomain(
        "vitruvio.runtime.ops.lifecycle",
        "LifecycleOps",
        "lifecycle_ops",
        ("init", "state", "verify", "history", "info"),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.inspection",
        "InspectionOps",
        "inspection_ops",
        ("resolvability", "resolve", "prove", "module", "roots"),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.browsing",
        "BrowsingOps",
        "browsing_ops",
        ("blocks", "content", "export_content", "related"),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.catalog",
        "CatalogOps",
        "catalog_ops",
        ("catalog_show", "catalog_tree", "catalog_apply", "catalog_browse", "catalog_path"),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.authenticity",
        "AuthenticityOps",
        "authenticity_ops",
        (
            "auth_keys",
            "auth_status",
            "auth_trust_root",
            "auth_sign",
            "auth_pin",
            "auth_attribution",
            "auth_plan_rotation",
            "auth_countersign",
            "auth_rotate",
            "auth_revoke",
        ),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.migration",
        "MigrationOps",
        "migration_ops",
        ("plan_migration", "migrate"),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.registration",
        "RegistrationOps",
        "registration_ops",
        ("register", "replace", "put_content"),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.tasks",
        "TaskOps",
        "task_ops",
        ("define_task", "task_schema", "validate_candidates", "commit_candidates", "ingest_run", "pipelines"),
        exports=("DUPLICATE",),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.sources",
        "SourceOps",
        "source_ops",
        ("sources", "source_kinds", "scaffold_source", "add_source", "remove_source", "pull_source", "pull_all"),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.retention",
        "RetentionOps",
        "retention_ops",
        ("plan_drop", "drop", "drop_by_producer", "supersede", "demote", "prune", "redact", "policy"),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.indices",
        "IndexOps",
        "index_ops",
        ("index_list", "index_build", "index_stats", "index_verify", "index_gc"),
    ),
    OperationDomain("vitruvio.runtime.ops.benchmarking", "BenchmarkOps", "benchmark_ops", ("bench",)),
    OperationDomain("vitruvio.runtime.ops.embedders", "EmbedderOps", "embedder_ops", ("embedders", "test_embedder")),
    OperationDomain(
        "vitruvio.runtime.ops.projects", "ProjectOps", "project_ops", ("project", "add_brain", "remove_brain")
    ),
    OperationDomain("vitruvio.runtime.ops.remote", "RemoteOps", "remote_ops", ("reference_for",)),
    OperationDomain(
        "vitruvio.runtime.ops.publish",
        "PublishOps",
        "publish_ops",
        ("pack", "registry_check", "registry_check_async", "push", "push_async", "tags"),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.install",
        "InstallOps",
        "install_ops",
        ("plan_pull", "plan_pull_async", "pull", "pull_async", "fetch", "fetch_async"),
    ),
    OperationDomain(
        "vitruvio.runtime.ops.reconcile",
        "ReconcileOps",
        "reconcile_ops",
        (
            "declared_strategy",
            "contains",
            "plan",
            "reconcile",
            "status",
            "resolve",
            "accept_removals",
            "continue_",
            "abort",
            "tree",
        ),
        exposure=Exposure.PROPERTY,
    ),
    OperationDomain("vitruvio.runtime.ops.retrieval", "RetrievalOps", "retrieval_ops", ("search", "explain")),
    OperationDomain(
        "vitruvio.runtime.ops.compound", "CompoundOps", "compound_ops", ("compound_search", "compound_explain")
    ),
)


def facade_operations() -> tuple[tuple[OperationDomain, str], ...]:
    """Every operation forwarded directly by ``BrainService``."""
    return tuple(
        (domain, operation)
        for domain in OPERATION_CATALOGUE
        if domain.exposure is Exposure.FACADE
        for operation in domain.operations
    )


def documentation_metadata() -> tuple[dict[str, object], ...]:
    """Stable metadata for documentation and future protocol adapters."""
    return tuple(
        {
            "domain": domain.class_name,
            "property": domain.property_name,
            "exposure": domain.exposure.value,
            "operations": domain.operations,
        }
        for domain in OPERATION_CATALOGUE
    )


__all__ = [
    "OPERATION_CATALOGUE",
    "Exposure",
    "OperationDomain",
    "documentation_metadata",
    "facade_operations",
]
