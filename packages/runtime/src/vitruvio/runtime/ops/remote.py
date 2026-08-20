"""Naming a registry and opening a client to it.

The part every distribution operation needs and none of them owns: which repository this brain publishes to, and
an authenticated client for it. Split out so that `publish` and `install` depend on it rather than on each other.
"""

from __future__ import annotations

from inspect import signature
from pathlib import Path
from typing import Any

from vitruvio.kernel import ResolvedConfig, VitruvioError
from vitruvio.runtime.session import BrainSession


def require_vector_index_ignore(method: Any) -> None:
    """Fail clearly when the CLI feature is used with an SDK that predates the supporting pull contract."""
    if "ignore_vector_indices" in signature(method).parameters:
        return
    raise VitruvioError(
        "the installed pyboltzmann does not support ignoring vector indices during a pull",
        hint="upgrade pyboltzmann to a release whose Brain.pull exposes `ignore_vector_indices`, then retry",
    )


class RemoteOps:
    """The registry endpoint, as shared machinery."""

    def __init__(self, session: BrainSession) -> None:
        """
        Args:
            session (BrainSession): The shared session.
        """
        self.session = session

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration, read through the session that owns it."""
        return self.session.config

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
