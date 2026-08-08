"""Resolving an embedder from configuration.

Providers are looked up lazily, by name, and the import that could be expensive happens inside the factory. That is
what keeps ``import vitruvio.embeddings`` cheap enough for a CLI that has to start in tens of milliseconds while still
letting a vector index reach a real model when one is configured.

An unavailable provider is reported with what to install, **not substituted**. Falling back to hashing when a caller
asked for a real model would produce an index whose tag says one thing and whose vectors mean another -- the exact
failure the model tag exists to make impossible.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from vitruvio.embeddings.base import Embedder, EmbedderUnavailableError, FakeEmbedder, HashingEmbedder

if TYPE_CHECKING:
    from vitruvio.kernel import EmbedderSpec

Factory = Callable[["EmbedderSpec"], Embedder]

EXTRAS = {
    "local-st": "vitruvio[local]",
    "local-siglip": "vitruvio[vision]",
    "openai": "vitruvio[api]",
    "openrouter": "vitruvio[api]",
    "ollama": "vitruvio[api]",
    "voyage": "vitruvio[api]",
    "cohere": "vitruvio[api]",
}
"""Provider name to what installs it, so an error names the fix rather than the symptom."""

MODULES = {
    "local-st": "sentence_transformers",
    "local-siglip": "sentence_transformers",
    "openai": "httpx",
    "openrouter": "httpx",
    "ollama": "httpx",
    "voyage": "httpx",
    "cohere": "httpx",
}
"""What each provider needs importable. Probed with ``find_spec``, which does not execute the module."""


def _hashing(spec: EmbedderSpec) -> Embedder:
    """The zero-dependency default."""
    return HashingEmbedder(dimensions=spec.dims or 256)


def _fake(spec: EmbedderSpec) -> Embedder:
    """Deterministic vectors, for tests."""
    return FakeEmbedder(dimensions=spec.dims or 32)


def _openrouter(spec: EmbedderSpec) -> Embedder:
    """Embeddings through OpenRouter's OpenAI-shaped endpoint."""
    from vitruvio.embeddings.openai_api import OpenRouterEmbedder

    return OpenRouterEmbedder(spec)


def _ollama(spec: EmbedderSpec) -> Embedder:
    """Embeddings from a local Ollama, through its OpenAI-compatible endpoint."""
    from vitruvio.embeddings.openai_api import OllamaEmbedder

    return OllamaEmbedder(spec)


_REGISTRY: dict[str, Factory] = {
    "hashing": _hashing,
    "fake": _fake,
    "openrouter": _openrouter,
    "ollama": _ollama,
}
"""Providers this build can construct. A real model registers itself when its extra is installed."""


def register(provider: str, factory: Factory) -> None:
    """
    Add a provider.

    Args:
        provider (str): The name used in configuration.
        factory (Factory): Builds an embedder from a spec.
    """
    _REGISTRY[provider] = factory


def available() -> list[dict[str, object]]:
    """
    Every provider this build knows, and whether it can run.

    Returns:
        list[dict[str, object]]: Name, whether it is constructible here, what installs it otherwise, and whether its
        vectors carry meaning -- which is what ``inspect doctor`` reports, so that hashed features are never taken for
        semantics.
    """
    from importlib.util import find_spec

    rows: list[dict[str, object]] = []
    for provider in sorted(set(_REGISTRY) | set(EXTRAS)):
        module = MODULES.get(provider)
        installed = provider in _REGISTRY and (module is None or find_spec(module) is not None)
        rows.append(
            {
                "provider": provider,
                "installed": installed,
                "extra": EXTRAS.get(provider),
                "semantic": provider not in {"hashing", "fake"},
            }
        )
    return rows


def resolve(spec: EmbedderSpec) -> Embedder:
    """
    Build the embedder a configuration names.

    Args:
        spec (EmbedderSpec): Provider, model, revision, dimensions.

    Returns:
        Embedder: The embedder.

    Raises:
        EmbedderUnavailableError: If the provider is unknown or its extra is not installed. Deliberately **not**
            falling back to hashing: an index whose tag claims one model and whose vectors came from another is the
            precise failure the tag exists to prevent, and a silent substitution would manufacture it.
    """
    factory = _REGISTRY.get(spec.provider)
    if factory is None:
        extra = EXTRAS.get(spec.provider)
        known = ", ".join(sorted(_REGISTRY))
        detail = f"install {extra}" if extra else f"known providers: {known}"
        raise EmbedderUnavailableError(f"no embedder provider named {spec.provider!r} in this build; {detail}")
    return factory(spec)
