"""Portable request configuration and temporary OCI installations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w

from vitruvio.http.models import BrainConfig, BrainRef
from vitruvio.kernel import ExitCode, VitruvioError, load_project, resolve
from vitruvio.runtime import BrainService
from vitruvio.runtime.ops.remote import RemoteOps
from vitruvio.runtime.wire import manifest as manifest_wire


class TagMovedError(VitruvioError):
    code = "OCI_TAG_MOVED"
    exit_code = ExitCode.BUSY
    retryable = True
    http_status = 409


@dataclass(slots=True)
class InstalledBrain:
    service: BrainService
    manifest_digest: str
    snapshot_digest: str
    authenticity: dict[str, Any]
    attribution: dict[str, Any] | None
    warnings: list[str]

    def metadata(self) -> dict[str, Any]:
        return {
            "manifest_digest": self.manifest_digest,
            "snapshot_digest": self.snapshot_digest,
            "authenticity": self.authenticity,
            "attribution": self.attribution,
        }


def local_registry() -> Path | None:
    value = os.environ.get("VITRUVIO_HTTP_LOCAL_REGISTRY")
    return Path(value) if value else None


def _insecure() -> bool:
    return os.environ.get("VITRUVIO_HTTP_INSECURE", "").lower() in {"1", "true", "yes"}


def make_service(
    root: Path,
    brain: BrainRef,
    config: BrainConfig,
    *,
    assisted_by: list[str] | None = None,
) -> BrainService:
    root.mkdir(parents=True, exist_ok=True)
    layout = root / "brain"
    document: dict[str, Any] = config.model_dump(exclude_none=True, exclude_defaults=True)
    # Pull opens a WRITE view even for a read-only consumer. This identity is used only for its
    # disposable bootstrap genesis; the installed snapshot keeps its original provenance.
    document.setdefault("actor", {"id": "vitruvio/http-reader", "kind": "service"})
    document["brain"] = {"path": str(layout), "publish": True}
    document["registry"] = {"reference": brain.repository, "tag": brain.tag, "insecure": _insecure()}
    config_path = root / "vitruvio.toml"
    config_path.write_text(tomli_w.dumps(document), encoding="utf-8")
    # Use the same loader as a normal project, including its actor-id and field diagnostics.
    load_project(config_path)
    selected = resolve(
        brain=layout,
        config=config_path,
        assisted_by=assisted_by,
        require_layout=False,
    )
    return BrainService(selected)


async def resolve_manifest(service: BrainService, brain: BrainRef) -> str:
    remote = RemoteOps(service.session)
    prepared = remote._prepare(brain.repository, tag=brain.tag, local=local_registry())
    resolved = await remote._request(prepared.client.resolve(prepared.effective, prepared.tag))
    return str(manifest_wire(resolved)["digest"])


async def install(service: BrainService, brain: BrainRef, expected_digest: str) -> InstalledBrain:
    service.init(write_config=False)
    await service.plan_pull_async(brain.repository, tag=brain.tag, local=local_registry())
    pulled = await service.pull_async(brain.repository, tag=brain.tag, local=local_registry())
    observed = await resolve_manifest(service, brain)
    if observed != expected_digest:
        raise TagMovedError("the OCI tag moved while the brain was being installed")
    verification = service.verify()
    if not verification["verified"]:
        raise VitruvioError("the installed brain failed integrity verification")
    authenticity = service.auth_status()
    attribution = service.auth_attribution() if authenticity.get("trust_root") else None
    warnings = list(pulled.get("warnings", []))
    state = authenticity.get("state")
    if authenticity.get("trust_root") and state != "authorized":
        warnings.append(f"governed head authenticity is {state}")
    if attribution and (not attribution["complete"] or not attribution["fully_vouched"]):
        warnings.append("governed head attribution is not fully vouched")
    return InstalledBrain(
        service=service,
        manifest_digest=expected_digest,
        snapshot_digest=pulled["snapshot"]["digest"],
        authenticity=authenticity,
        attribution=attribution,
        warnings=warnings,
    )
