"""Resolving secrets from the environment, and never printing them.

Two rules, both structural rather than advisory:

**Secrets come from the environment.** There is no field for an API key or a registry token anywhere in
:mod:`vitruvio.kernel.config`, so a secret cannot end up in the file people are told to commit. The
credential *store* for registries (a keyring, or a ``0600`` file) lives in ``vitruvio.runtime.registry``,
because it needs to talk to hosts; this module is only about reading and redacting.

**A resolved secret is wrapped, not returned bare.** :class:`Secret` prints as a redaction under ``str``,
``repr`` and f-strings, so a value can be logged, put in an error message, or dumped into a JSON envelope
by mistake without leaking. Getting the real bytes takes an explicit :meth:`Secret.reveal`, which is the
one call worth grepping for in review.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

REDACTED = "<redacted>"
"""What a secret looks like anywhere other than :meth:`Secret.reveal`."""

TOKEN_URL = "Docker Hub -> Account settings -> Personal access tokens"
"""Where a Docker Hub token comes from. Named once so that every error message agrees."""

# Provider key, then the variables to try in order. The vitruvio-prefixed name wins so that a user can
# point vitruvio at a different account than the rest of their shell, and the bare name is honoured so that
# an existing environment -- a CI job, a direnv, a `docker login` shell -- just works.
PROVIDER_VARIABLES: dict[str, tuple[str, ...]] = {
    "openai": ("VITRUVIO_OPENAI_API_KEY", "OPENAI_API_KEY"),
    "voyage": ("VITRUVIO_VOYAGE_API_KEY", "VOYAGE_API_KEY"),
    "cohere": ("VITRUVIO_COHERE_API_KEY", "COHERE_API_KEY"),
    "anthropic": ("VITRUVIO_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    "huggingface": ("VITRUVIO_HF_TOKEN", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"),
}

REGISTRY_USERNAME_VARIABLES = ("VITRUVIO_REGISTRY_USERNAME", "DOCKER_USERNAME")
REGISTRY_TOKEN_VARIABLES = ("VITRUVIO_REGISTRY_TOKEN", "DOCKER_TOKEN", "DOCKER_PASSWORD")


@dataclass(frozen=True)
class Secret:
    """
    A secret that refuses to print itself.

    Attributes:
        source (str): Where it came from -- an environment variable name, ``keyring``, ``file``, or
            ``flag``. Safe to display, and the answer to "why is it authenticating as someone else".
    """

    _value: str = field(repr=False)
    source: str

    def __str__(self) -> str:
        """The redaction, so an f-string cannot leak the value."""
        return REDACTED

    def __repr__(self) -> str:
        """The redaction, so a traceback or a container dump cannot leak the value."""
        return f"Secret(source={self.source!r})"

    def __bool__(self) -> bool:
        """Whether a non-empty value is held."""
        return bool(self._value)

    def reveal(self) -> str:
        """
        The actual secret.

        The only way to obtain it, named so that every use site is one grep away.

        Returns:
            str: The secret value.
        """
        return self._value

    def masked(self) -> str:
        """
        A partial rendering that identifies the credential without disclosing it.

        Useful in ``registry whoami``, where "which token is this" is exactly the question and the answer
        needs to be recognisable to the person who created it.

        Returns:
            str: The last four characters behind an ellipsis, or the redaction if the value is short enough
            that four characters would be most of it.
        """
        if len(self._value) < 12:
            return REDACTED
        return f"{self._value[:4]}...{self._value[-4:]}"


def from_environment(*names: str, source_prefix: str = "env:") -> Secret | None:
    """
    Return the first non-empty environment variable among ``names``.

    Args:
        *names: Variables to try, in precedence order.
        source_prefix (str): Prefix for the reported source.

    Returns:
        Secret | None: The secret, or ``None`` if none of the variables is set.
    """
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return Secret(value, source=f"{source_prefix}{name}")
    return None


def provider_key(provider: str) -> Secret | None:
    """
    The API key for an embedding or proposer provider.

    Args:
        provider (str): Registry key, e.g. ``openai``.

    Returns:
        Secret | None: The key, or ``None`` when the provider needs none or it is unset.
    """
    variables = PROVIDER_VARIABLES.get(provider)
    if not variables:
        return None
    return from_environment(*variables)


def registry_credentials() -> tuple[str | None, Secret | None]:
    """
    Registry credentials as declared in the environment.

    ``DOCKER_USERNAME`` / ``DOCKER_TOKEN`` are honoured alongside the vitruvio-prefixed names because that
    is the pair already present in CI jobs and in the SDK's own sandbox.

    Returns:
        tuple[str | None, Secret | None]: The username and the token, either of which may be absent.
    """
    username = None
    for name in REGISTRY_USERNAME_VARIABLES:
        value = os.environ.get(name, "").strip()
        if value:
            username = value
            break
    return username, from_environment(*REGISTRY_TOKEN_VARIABLES)
