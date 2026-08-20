"""Which embedding providers this build can run, and what one actually returns.

Neither operation opens a brain. An embedder is a property of the *configuration* and of what is installed, not of
any stored composition, which is why `vitruvio config embedder list` answers on a machine that has no brain at all.
"""

from __future__ import annotations

from typing import Any

from vitruvio.kernel import ResolvedConfig, VitruvioError
from vitruvio.runtime.session import BrainSession


class EmbedderOps:
    """The embedding providers, as operations."""

    def __init__(self, session: BrainSession) -> None:
        """
        Args:
            session (BrainSession): The shared session. Held rather than unpacked, so that these operations read
                whatever configuration the session holds rather than a copy taken at construction.
        """
        self.session = session

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration, read through the session that owns it."""
        return self.session.config

    def embedders(self) -> dict[str, Any]:
        """
        Every embedding provider this build knows, whether it can run, and what is configured.

        The ``semantic`` column is the one to read. Hashed features rank, and rank plausibly, so a brain built with
        the zero-dependency default looks exactly like one built with a real model until you notice it never finds
        a synonym.

        Returns:
            dict[str, Any]: The providers, and the configured text and vision embedders.
        """
        from vitruvio.embeddings import available

        text = self.config.project.text_embedder
        vision = self.config.project.vision_embedder
        return {
            "providers": available(),
            "text": text.model_dump(mode="json"),
            "vision": vision.model_dump(mode="json") if vision else None,
            "semantic": text.provider not in {"hashing", "fake"},
        }

    def test_embedder(self, *, which: str = "text", text: str | None = None) -> dict[str, Any]:
        """
        Actually embed something, and report what came back.

        The point is the *width*: a remote model's dimensionality is what the model tag carries, and vitruvio
        refuses to guess it. For a model it does not already know, this is how you find the number to write into
        configuration -- which is why the failure path reports the width it saw rather than only that it disagreed.

        Args:
            which (str): ``text`` or ``vision``.
            text (str | None): What to embed. A short Spanish and English phrase by default, so a wrong-language
                model shows up as a plausible vector rather than as an error.

        Returns:
            dict[str, Any]: The tag, the measured width, the elapsed time, and whether the vector is normalized.

        Raises:
            VitruvioError: If nothing is configured for that modality, or the provider could not run.
        """
        import time

        from vitruvio.embeddings import EmbedderUnavailableError, resolve

        spec = self.config.project.text_embedder if which == "text" else self.config.project.vision_embedder
        if spec is None:
            raise VitruvioError(
                f"no {which} embedder is configured",
                hint=f"add [embedding.{which}] with provider and model to {self.config.config_file or 'vitruvio.toml'}",
            )

        probe = text or "una funcion periodica se descompone en senos y cosenos"
        try:
            embedder = resolve(spec)
            started = time.perf_counter()
            vectors = embedder.embed_text([probe])
            elapsed = (time.perf_counter() - started) * 1000
        except EmbedderUnavailableError as error:
            raise VitruvioError(
                f"the {which} embedder could not run: {error}",
                hint="`vitruvio config embedder list` shows which providers this build can construct",
            ) from error

        vector = vectors[0] if vectors else ()
        norm = sum(value * value for value in vector) ** 0.5
        return {
            "which": which,
            "provider": spec.provider,
            "model": spec.model,
            "tag": embedder.tag.render(),
            "semantic": embedder.tag.is_semantic,
            "declared_dimensions": embedder.dimensions,
            "measured_dimensions": len(vector),
            "normalized": abs(norm - 1.0) < 1e-6,
            "elapsed_ms": round(elapsed, 1),
            "probe": probe,
        }
