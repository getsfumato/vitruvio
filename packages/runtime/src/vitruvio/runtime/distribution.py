"""Publishing and installing a brain, and the preflight that makes a first push predictable.

The SDK does the transfer. What lives here is the part that decides whether a transfer will *work*, which for OCI
artifacts is not obvious: the manifest a brain publishes carries a custom ``artifactType`` and a custom
``config.mediaType``, and registries have historically disagreed about whether that is allowed.

Docker Hub's documentation used to state plainly that it accepted no ``config.mediaType`` other than
``application/vnd.oci.image.config.v1+json``. Its current documentation no longer publishes that restriction, and Docker
now promotes OCI artifacts for AI model packaging -- so support appears to have broadened. But that is a reading of
documentation, not a verified fact, and the manifest's shape is fixed by the protocol: vitruvio cannot change it without
producing an artifact that is no longer a Boltzmann brain.

So it is **checked** rather than assumed. :func:`preflight` pushes a probe artifact with *exactly* the manifest shape a
brain uses, reports what the registry did with it, and cleans up. If a registry refuses, the error says so and names the
real alternatives -- ghcr.io, or a self-hosted ``registry:2`` -- rather than inventing a compatibility mode that would
change the artifact's identity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vitruvio.kernel import VitruvioError
from vitruvio.runtime.registry import PREFLIGHT_TAG, is_docker_hub, normalize_reference

if TYPE_CHECKING:
    from boltzmann.store.base import BlockStore

PROBE_CONTENT = b'{"vitruvio":"preflight"}'
"""What the probe layer holds. Tiny, and self-describing if anyone finds it."""


async def preflight(
    reference: str,
    client: Any,
    store: BlockStore,
    *,
    tag: str = PREFLIGHT_TAG,
) -> dict[str, Any]:
    """
    Push a probe artifact shaped exactly like a brain, and report what the registry accepted.

    Four questions, answered by doing rather than by guessing: is ``/v2/`` reachable, do the credentials carry write
    scope, is a custom ``config.mediaType`` accepted, and does the ``artifactType`` survive a round trip.

    The probe uses the brain's real media types on purpose. A probe with a conventional config media type would prove
    only that the registry accepts *images*, which is exactly the thing that was never in doubt.

    Args:
        reference (str): The repository to probe.
        client (Any): An authenticated registry client.
        store (BlockStore): Where the probe blobs are written. The brain's own store, since they are content-addressed
            and a stray 24-byte blob costs nothing.
        tag (str): The tag to publish under.

    Returns:
        dict[str, Any]: Per-check outcomes, plus ``ok`` and, when it failed, ``hint``.
    """
    from boltzmann.blocks.memory_type import MemoryType
    from boltzmann.distribution.manifest import BrainManifest, Descriptor
    from boltzmann.distribution.media_types import ARTIFACT_TYPE, CONFIG_MEDIA_TYPE, module_media_type

    configured, effective = normalize_reference(reference)
    checks: list[dict[str, Any]] = []

    def note(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    note("reference", True, f"{configured} resolves to {effective}")

    config_digest = store.put_bytes(PROBE_CONTENT)
    layer_digest = store.put_bytes(PROBE_CONTENT)
    manifest = BrainManifest(
        artifact_type=ARTIFACT_TYPE,
        config=Descriptor(media_type=CONFIG_MEDIA_TYPE, digest=config_digest, size=len(PROBE_CONTENT)),
        layers=[
            Descriptor(
                media_type=module_media_type(MemoryType.SEMANTIC),
                digest=layer_digest,
                size=len(PROBE_CONTENT),
                annotations={"ai.gaussia.boltzmann.memory-type": MemoryType.SEMANTIC.value},
            )
        ],
    )

    try:
        published = await client.push(effective, tag, manifest, store)
    except Exception as error:
        message = str(error)
        note("write", False, message)
        hint = (
            f"the registry refused an artifact with a custom config media type ({CONFIG_MEDIA_TYPE}). The manifest "
            f"shape is fixed by the protocol, so the alternatives are a registry that accepts it -- ghcr.io does -- or "
            f"a local one: docker run -d -p 5000:5000 registry:2, then push to localhost:5000 with --insecure"
            if "media" in message.lower() or "unsupported" in message.lower()
            else "check the credentials and the repository name; a first push also needs the repository to exist or the "
            "account to be allowed to create it"
        )
        return {"reference": configured, "effective": effective, "checks": checks, "ok": False, "hint": hint}

    note("write", True, f"accepted, filed under {str(published)[:20]}...")
    note("config_media_type", True, f"{CONFIG_MEDIA_TYPE} accepted")

    try:
        resolved = await client.resolve(effective, tag)
    except Exception as error:
        note("round_trip", False, f"pushed but could not be resolved back: {error}")
        return {
            "reference": configured,
            "effective": effective,
            "checks": checks,
            "ok": False,
            "hint": "the registry accepted the push and will not serve it back, which a brain cannot be published to",
        }

    preserved = resolved.artifact_type == ARTIFACT_TYPE
    note(
        "artifact_type",
        preserved,
        f"{ARTIFACT_TYPE} preserved" if preserved else f"rewritten to {resolved.artifact_type!r}",
    )
    if is_docker_hub(reference):
        note("rate_limits", True, "Docker Hub's free tier rate-limits pulls; a public brain may be throttled")

    return {
        "reference": configured,
        "effective": effective,
        "checks": checks,
        "ok": all(check["ok"] for check in checks),
        "hint": None
        if preserved
        else "the registry rewrote artifactType, so a consumer cannot tell this artifact is a brain without reading it",
    }


def local_registry(root: Any) -> Any:
    """
    A filesystem "registry" of OCI layouts.

    No network, no credentials, no rate limits, and the same code path as a remote push. Which makes it the right thing
    to test the travelling contract against before pointing anything at Docker Hub.

    Args:
        root (Any): Directory to hold the layouts.

    Returns:
        Any: The registry client.
    """
    from boltzmann.distribution.local import LocalLayoutRegistry

    return LocalLayoutRegistry(root)


def require_reference(configured: str | None, given: str | None) -> str:
    """
    The repository to use, preferring what was passed.

    Args:
        configured (str | None): ``[registry].reference`` from the project configuration.
        given (str | None): What the command was given.

    Returns:
        str: The reference.

    Raises:
        VitruvioError: If neither names one.
    """
    reference = given or configured
    if not reference:
        raise VitruvioError(
            "no registry reference was given and none could be derived",
            hint=(
                "pass one; or run `vitruvio registry login docker.io --from-docker` so every brain in the project "
                'derives its own repository from your account; or set [registry] namespace = "docker.io/you" for '
                'a project, or [registry] reference = "docker.io/you/my-brain" for a single brain'
            ),
        )
    return reference
