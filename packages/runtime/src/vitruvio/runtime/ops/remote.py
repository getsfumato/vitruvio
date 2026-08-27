"""Naming a registry and opening a client to it.

The part every distribution operation needs and none of them owns: which repository this brain publishes to, and
an authenticated client for it. Split out so that `publish` and `install` depend on it rather than on each other.
"""

from __future__ import annotations

from collections.abc import Awaitable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from inspect import signature
from pathlib import Path
from typing import Any, TypeVar

from vitruvio.kernel import ResolvedConfig, VitruvioError
from vitruvio.runtime.mapping import translated
from vitruvio.runtime.session import BrainSession

ResultT = TypeVar("ResultT")


@dataclass(slots=True)
class PreparedRemote:
    """A fully resolved registry destination shared by every distribution operation."""

    reference: str
    effective: str
    tag: str
    client: Any
    warnings: list[str]


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

    def _prepare(
        self,
        reference: str | None = None,
        *,
        tag: str | None = None,
        username: str | None = None,
        token: str | None = None,
        anonymous: bool = False,
        allow_docker: bool = False,
        insecure: bool | None = None,
        local: Path | None = None,
    ) -> PreparedRemote:
        """Resolve naming, credentials, transport selection, and tag once for an operation."""
        target = self.reference_for(reference)
        client, effective, warnings = self._client(
            target,
            username=username,
            token=token,
            anonymous=anonymous,
            allow_docker=allow_docker,
            insecure=insecure,
            local=local,
        )
        return PreparedRemote(
            reference=target,
            effective=effective,
            tag=tag or self.config.project.registry.tag,
            client=client,
            warnings=warnings,
        )

    async def _request(self, operation: Awaitable[ResultT]) -> ResultT:
        """Await one registry operation through the shared exception-translation boundary."""
        with translated():
            return await operation

    def _run(self, operation: Coroutine[Any, Any, ResultT]) -> ResultT:
        """Adapt an async registry operation for synchronous callers.

        CLI calls normally have no event loop and use ``asyncio.run`` here. If a synchronous compatibility method
        is invoked while its caller already owns a loop, the coroutine runs in an isolated worker thread instead of
        attempting a nested loop. Async protocol adapters should call the public ``*_async`` methods directly.
        """
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(operation)

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="vitruvio-registry") as executor:
            return executor.submit(lambda: asyncio.run(operation)).result()

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
