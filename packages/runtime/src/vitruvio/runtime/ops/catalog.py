"""Portable catalog declarations behind one manifest-shaped interface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from boltzmann.catalog import Catalog
from boltzmann.catalog_core import CATALOG_DUPLICATE
from boltzmann.catalog_models import (
    ClassDeclaration,
    ClassificationRequest,
    HierarchyDeclaration,
    PlacementDeclaration,
    SchemeDeclaration,
)
from boltzmann.catalog_validation import validate_declarations
from boltzmann.identity.digest import BlockId
from boltzmann.ingest.validation import ValidationStatus
from pydantic import BaseModel, ConfigDict, Field

from vitruvio.kernel import ResolvedConfig, UsageError
from vitruvio.runtime.assembly import Capability
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession


class CatalogSchemeSpec(BaseModel):
    """One independent catalog dimension in ``vitruvio.catalog/v1``."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1)
    exclusive: bool = False


class CatalogClassSpec(BaseModel):
    """One class and its optional broader classes."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    scheme: str = Field(min_length=1)
    label: str = Field(min_length=1)
    broader: list[str] = Field(default_factory=list)


class CatalogPlacementSpec(BaseModel):
    """One canonical source placed in one or more classes."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    source: BlockId
    classes: list[str] = Field(min_length=1)


class CatalogManifest(BaseModel):
    """The committed, reviewable input format for catalog changes."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_: Literal["vitruvio.catalog/v1"] = Field(alias="schema")
    schemes: list[CatalogSchemeSpec] = Field(default_factory=list)
    classes: list[CatalogClassSpec] = Field(default_factory=list)
    placements: list[CatalogPlacementSpec] = Field(default_factory=list)


class CatalogOps:
    """Declare and navigate the portable semantic catalog."""

    def __init__(self, session: BrainSession) -> None:
        self.session = session

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration."""
        return self.session.config

    @staticmethod
    def _reference(scheme: str, label: str) -> str:
        return f"{scheme}/{label}"

    @staticmethod
    def _split_reference(reference: str) -> tuple[str, str]:
        scheme, separator, label = reference.partition("/")
        if not separator or not scheme or not label:
            raise UsageError(
                f"catalog class {reference!r} is not a scheme/label reference",
                hint="use the exact case-sensitive scheme and label, for example topic/Mathematics",
            )
        return scheme, label

    def _class_id(
        self,
        catalog: Catalog,
        reference: str,
        declared: Mapping[str, BlockId] | None = None,
    ) -> BlockId:
        if declared and reference in declared:
            return declared[reference]
        scheme, label = self._split_reference(reference)
        return catalog.class_id(scheme, label)

    def catalog_show(self) -> dict[str, Any]:
        """List every scheme, class, hierarchy edge and effective source set."""
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            catalog = Catalog(brain.modules())
            schemes = []
            for scheme in catalog.schemes:
                classes = []
                for label, class_id in catalog.classes_in(scheme):
                    node = catalog.browse(class_id).nodes[0]
                    classes.append(
                        {
                            "reference": self._reference(scheme, label),
                            **node.model_dump(mode="json"),
                        }
                    )
                schemes.append({"name": scheme, "classes": classes})
        return {"schema": "vitruvio.catalog/v1", "schemes": schemes}

    def catalog_apply(self, manifest: Mapping[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        """Validate and atomically apply one ``vitruvio.catalog/v1`` manifest.

        Any non-duplicate rejection aborts the whole manifest. Duplicate declarations are
        harmless and make applying the same file idempotent.
        """
        with translated():
            typed = CatalogManifest.model_validate(manifest)
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            catalog = Catalog(brain.modules())
        declarations: list[Any] = []
        declared: dict[str, BlockId] = {}

        for scheme in typed.schemes:
            declarations.append(SchemeDeclaration(scheme=scheme.name, exclusive=scheme.exclusive))
        for item in typed.classes:
            declaration = ClassDeclaration(scheme=item.scheme, label=item.label)
            reference = self._reference(item.scheme, item.label)
            if reference in declared:
                raise UsageError(f"catalog manifest declares {reference!r} more than once")
            declared[reference] = declaration.block_id
            declarations.append(declaration)
        with translated():
            for item in typed.classes:
                narrower = declared[self._reference(item.scheme, item.label)]
                declarations.extend(
                    HierarchyDeclaration(
                        broader=self._class_id(catalog, broader, declared),
                        narrower=narrower,
                    )
                    for broader in item.broader
                )
            for placement in typed.placements:
                declarations.extend(
                    PlacementDeclaration(
                        source=placement.source,
                        class_id=self._class_id(catalog, reference, declared),
                    )
                    for reference in placement.classes
                )
        if not declarations:
            raise UsageError("catalog manifest contains no declarations")

        request = ClassificationRequest(declarations=declarations)
        with translated():
            verdicts, _blocks, _placements = validate_declarations(request, brain.modules())
        fatal = [
            verdict
            for verdict in verdicts
            if verdict.status is not ValidationStatus.VALIDATED
            and any(issue.code != CATALOG_DUPLICATE for issue in verdict.issues)
        ]
        payload = {
            "schema": "vitruvio.catalog/v1",
            "clean": not fatal,
            "dry_run": dry_run,
            "applied": False,
            "snapshot": str(brain.snapshot().digest),
            "verdicts": [verdict.model_dump(mode="json") for verdict in verdicts],
        }
        if fatal or dry_run:
            return payload

        with self.session.write() as writable, translated():
            result = writable.classify(request)
            payload.update(
                applied=any(verdict.status is ValidationStatus.VALIDATED for verdict in result.verdicts),
                snapshot=str(result.commit.snapshot.digest),
                verdicts=[verdict.model_dump(mode="json") for verdict in result.verdicts],
            )
        return payload

    def catalog_browse(self, classes: Sequence[str]) -> dict[str, Any]:
        """Browse the AND intersection of case-sensitive ``scheme/label`` references."""
        if not classes:
            raise UsageError("catalog browse needs at least one class")
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            catalog = Catalog(brain.modules())
            result = catalog.browse([self._class_id(catalog, item) for item in classes])
        return result.model_dump(mode="json")

    def catalog_path(self, schemes: Sequence[str], path: str = "") -> dict[str, Any]:
        """List one virtual path whose segment order is defined by ``schemes``."""
        brain = self.session.brain(Capability.INSPECT)
        with translated():
            result = brain.catalog_path(schemes).iterdir(path)
        return result.model_dump(mode="json")


__all__ = ["CatalogManifest", "CatalogOps"]
