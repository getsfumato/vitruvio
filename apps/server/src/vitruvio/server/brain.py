"""Portable request configuration, server-approved destinations, and temporary OCI installations.

Everything here is synchronous on purpose. The SDK's registry client performs blocking network I/O behind an async
interface, so awaiting it from a FastAPI handler stalls the event loop for the whole transfer. Callers run these
functions in a worker thread (``asyncio.to_thread``); the runtime's synchronous methods then open a private loop of
their own, which is the path the CLI already exercises.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import tomli_w

from vitruvio.kernel import ConfigError, ExitCode, ProjectConfig, UsageError, VitruvioError, load_project, resolve
from vitruvio.runtime import BrainService
from vitruvio.runtime.ops.remote import PreparedRemote, RemoteOps
from vitruvio.runtime.registry import host_of
from vitruvio.runtime.wire import manifest as manifest_wire
from vitruvio.server.models import BrainConfig, BrainRef

ENV_ALLOWED_REGISTRIES = "VITRUVIO_SERVER_ALLOWED_REGISTRIES"
"""Registry hosts a request may name. The deployment's registry credentials are offered to these and to nothing else."""

ENV_ALLOWED_EMBEDDING_URLS = "VITRUVIO_SERVER_ALLOWED_EMBEDDING_URLS"
"""Embedding endpoints a request may select with ``embedding.<kind>.base_url``; the provider's API key goes with it."""

REF_NAME_ANNOTATION = "org.opencontainers.image.ref.name"
"""The OCI annotation a layout's ``index.json`` tags a manifest with -- how a pull records which manifest it installed."""


class TagMovedError(VitruvioError):
    code = "OCI_TAG_MOVED"
    exit_code = ExitCode.BUSY
    retryable = True
    http_status = 409


class DestinationRefusedError(VitruvioError):
    """A request named a host this deployment has not approved for its credentials.

    The request configuration is caller-controlled and the credentials are not: embedding providers attach the
    environment's API key to whatever endpoint the configuration names, and the registry client offers the
    environment's registry token to whatever host the repository names. Excluding credential fields from the request
    does not help -- the disclosure is in the destination -- so destinations are bound to a server-side list.
    """

    code = "DESTINATION_REFUSED"
    exit_code = ExitCode.POLICY
    http_status = 403


@dataclass(slots=True)
class InstalledBrain:
    service: BrainService
    manifest_digest: str
    snapshot_digest: str
    authenticity: dict[str, Any]
    attribution: dict[str, Any] | None
    warnings: list[str]

    @property
    def governed(self) -> bool:
        """Whether the head carries a trust root, and therefore whether detached signatures decide its state."""
        return bool(self.authenticity.get("trust_root"))

    def metadata(self) -> dict[str, Any]:
        return {
            "manifest_digest": self.manifest_digest,
            "snapshot_digest": self.snapshot_digest,
            "authenticity": self.authenticity,
            "attribution": self.attribution,
        }


@dataclass(slots=True)
class RemoteManifest:
    """One resolution of a tag, holding the client it used so later questions about the artifact reuse it."""

    digest: str
    _remote: RemoteOps
    _prepared: PreparedRemote
    _resolved: Any

    def signatures(self) -> list[str]:
        """
        The referrers attached to this manifest right now, by digest.

        Signatures are published as OCI referrers of the brain manifest, so they can arrive after the manifest
        without changing its digest. Listing them is what lets a cache keyed by manifest notice.
        """
        referrers = self._remote._run(
            self._remote._request(self._prepared.client.referrers(self._prepared.effective, self._resolved.digest))
        )
        return sorted(str(item.digest) for item in referrers)


def local_registry() -> Path | None:
    value = os.environ.get("VITRUVIO_SERVER_LOCAL_REGISTRY")
    return Path(value) if value else None


def _insecure() -> bool:
    return os.environ.get("VITRUVIO_SERVER_INSECURE", "").lower() in {"1", "true", "yes"}


def _listed(variable: str) -> list[str]:
    return [item.strip() for item in os.environ.get(variable, "").split(",") if item.strip()]


def _endpoint(url: str) -> str:
    """One spelling for an endpoint, so ``https://Host/v1/`` and ``https://host/v1`` compare equal."""
    parts = urlsplit(url.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def require_approved_registry(repository: str) -> None:
    """
    Refuse a repository whose host the deployment has not approved.

    A filesystem registry (``VITRUVIO_SERVER_LOCAL_REGISTRY``) is exempt: it is addressed by path, carries no
    credentials, and the SDK refuses a path outside its root.

    Args:
        repository (str): The requested repository.

    Raises:
        UsageError: If the repository is not a well-formed reference.
        DestinationRefusedError: If its host is not listed in ``VITRUVIO_SERVER_ALLOWED_REGISTRIES``.
    """
    if local_registry() is not None:
        return
    try:
        host = host_of(repository)
    except VitruvioError as error:
        raise UsageError(f"brain.repository: {error.message}", hint=error.hint) from error
    # Each listed host goes through the same normalization as the request's, so `docker.io` in the list matches
    # the API host the client actually authenticates against.
    allowed = {host_of(f"{item.lower()}/probe/probe") for item in _listed(ENV_ALLOWED_REGISTRIES)}
    if host.lower() not in allowed:
        raise DestinationRefusedError(
            f"registry {host!r} is not an approved destination for this deployment",
            hint=f"the operator lists the registries this service may present its credentials to in {ENV_ALLOWED_REGISTRIES}",
        )


def require_approved_embedding(project: ProjectConfig) -> None:
    """
    Refuse an embedding endpoint the deployment has not approved.

    A request that names no ``base_url`` uses the provider's own endpoint, which is the one the server's key for
    that provider was issued for. Naming one redirects the key, so it has to match a listed endpoint exactly.

    Args:
        project (ProjectConfig): The validated request configuration.

    Raises:
        DestinationRefusedError: If any embedder names an endpoint outside ``VITRUVIO_SERVER_ALLOWED_EMBEDDING_URLS``.
    """
    allowed = {_endpoint(item) for item in _listed(ENV_ALLOWED_EMBEDDING_URLS)}
    for kind, spec in project.embedding.items():
        if spec.base_url is None:
            continue
        parts = urlsplit(spec.base_url)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise UsageError(f"embedding.{kind}.base_url must not contain credentials, query parameters or a fragment")
        if _endpoint(spec.base_url) not in allowed:
            raise DestinationRefusedError(
                f"embedding.{kind}.base_url {spec.base_url!r} is not an approved endpoint for this deployment",
                hint=(
                    f"leave base_url out to use the provider's own endpoint, or ask the operator to list it in "
                    f"{ENV_ALLOWED_EMBEDDING_URLS}"
                ),
            )


def _without_none(value: Any, *, path: str) -> Any:
    """
    Drop ``null`` from a JSON configuration before it becomes TOML, which has no way to spell it.

    A nullable field left null means what an absent field means in ``vitruvio.toml`` -- every optional field in the
    project schema defaults to ``None`` -- so dropping it is the normalization, not a loss. A null inside a list has
    no such reading and is refused as a caller error rather than crashing the serializer.
    """
    if isinstance(value, dict):
        return {key: _without_none(item, path=f"{path}.{key}") for key, item in value.items() if item is not None}
    if isinstance(value, list):
        if any(item is None for item in value):
            raise ConfigError(f"{path} contains null, which has no meaning in a vitruvio configuration")
        return [_without_none(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    return value


def portable_config(config: BrainConfig) -> dict[str, Any]:
    """The request configuration as the sections a temporary ``vitruvio.toml`` receives, normalized."""
    document: dict[str, Any] = _without_none(config.model_dump(exclude_none=True, exclude_defaults=True), path="config")
    return document


def make_service(
    root: Path,
    brain: BrainRef,
    config: BrainConfig,
    *,
    assisted_by: list[str] | None = None,
) -> BrainService:
    require_approved_registry(brain.repository)
    root.mkdir(parents=True, exist_ok=True)
    layout = root / "brain"
    document = portable_config(config)
    # Pull opens a WRITE view even for a read-only consumer. This identity is used only for its
    # disposable bootstrap genesis; the installed snapshot keeps its original provenance.
    document.setdefault("actor", {"id": "vitruvio/server-reader", "kind": "service"})
    document["brain"] = {"path": str(layout), "publish": True}
    document["registry"] = {"reference": brain.repository, "tag": brain.tag, "insecure": _insecure()}
    config_path = root / "vitruvio.toml"
    config_path.write_text(tomli_w.dumps(document), encoding="utf-8")
    # The same loader as a normal project, including its actor-id and field diagnostics, so a malformed request is
    # the caller's error rather than the serializer's.
    project = load_project(config_path)
    require_approved_embedding(project)
    selected = resolve(
        brain=layout,
        config=config_path,
        assisted_by=assisted_by,
        require_layout=False,
    )
    return BrainService(selected)


def resolve_remote(service: BrainService, brain: BrainRef) -> RemoteManifest:
    """Resolve a tag to the manifest it names now. Blocking; run it off the event loop."""
    remote = RemoteOps(service.session)
    prepared = remote._prepare(brain.repository, tag=brain.tag, local=local_registry())
    resolved = remote._run(remote._request(prepared.client.resolve(prepared.effective, prepared.tag)))
    return RemoteManifest(str(manifest_wire(resolved)["digest"]), remote, prepared, resolved)


def installed_manifest_digest(layout: Path, tag: str) -> str | None:
    """
    Which manifest a pull actually wrote into a layout, under the tag it was asked for.

    Read from the layout rather than by resolving the tag again: a tag that moved A -> B -> A during the pull
    resolves to A afterwards while B is what was downloaded, and a second resolve would bless it.
    """
    index_path = layout / "index.json"
    if not index_path.is_file():
        return None
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for entry in index.get("manifests", []):
        if entry.get("annotations", {}).get(REF_NAME_ANNOTATION) == tag:
            return str(entry["digest"])
    return None


def install(service: BrainService, brain: BrainRef, expected_digest: str) -> InstalledBrain:
    """Pull one tag into a fresh layout and verify it is the artifact the caller resolved. Blocking."""
    service.init(write_config=False)
    pulled = service.pull(brain.repository, tag=brain.tag, local=local_registry())
    observed = installed_manifest_digest(service.config.brain, brain.tag)
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
