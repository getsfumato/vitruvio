"""Embedders that speak OpenAI's ``/embeddings`` shape, and the two that ship.

OpenAI's embeddings request became the de-facto interface, so a surprising number of very different things answer it:
a router in front of a dozen vendors, a model running on your laptop, a self-hosted gateway. That shared shape is what
this module turns into one base class, with each provider supplying only what it genuinely differs on.

What the base owns, because getting any of it wrong is a correctness bug rather than a feature gap:

* **Order.** The response carries an ``index`` per item and is *not* promised to arrive sorted. Zipping the reply
  against the request positionally -- the obvious implementation -- silently pairs text with the wrong vector, and an
  index built that way is wrong in a way no test of "did it return vectors" would catch.
* **Width.** Every response is checked against the width the tag claims. A tag that says 768 over 1024-wide vectors is
  precisely the failure the tag exists to prevent, and a remote model can change under a name without telling anyone.
* **Batching.** Long inputs are split into batches, and results are reassembled in request order. The Embedder
  contract says batching must not change results; for an OpenAI-shaped API each input is independent, so what has to
  be preserved is only the ordering, which is why that is the part written carefully.
* **Truncation.** Bounded before the request rather than after a rejection, and the bound is named in the tag, because
  a truncation policy changes which string got embedded and therefore where the vector lands.

What a subclass supplies: where to send the request, how to authenticate, what to add to the body, and how to read
this vendor's failures. Nothing else.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from typing import Any, ClassVar

from vitruvio.embeddings.base import (
    EmbedderUnavailableError,
    ImageInput,
    Modality,
    TextRole,
    Vector,
    _normalize,
)
from vitruvio.embeddings.tag import UNPINNED, ModelTag
from vitruvio.kernel import EmbedderSpec

DEFAULT_BATCH = 64
"""Inputs per request.

Small enough to stay inside every vendor's payload limit, large enough that a rebuild is not one round trip per
block. Overridable, because a local Ollama has no payload limit worth respecting and a slow one benefits from less.
"""

MAX_CHARACTERS = 24_000
"""Where one input is cut, in characters.

Characters rather than tokens, for the same reason the chunker is character-based: a token count depends on which
tokenizer happens to be installed, so a token-based bound would truncate differently between two installs and move
the vectors with it. Roughly 6k tokens, comfortably inside every embedding model's window -- and the chunker upstream
has already cut to 1600, so this only fires on text that reached the embedder without being chunked.
"""

RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})
"""What is worth retrying. A 400 or a 401 will fail identically forever, so retrying one only delays the error."""

MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 1.5

KNOWN_DIMENSIONS: dict[str, int] = {
    # OpenAI, reachable directly and through any router that fronts it.
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    # Ollama's common embedding models.
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "snowflake-arctic-embed": 1024,
    "bge-m3": 1024,
    # Frequently routed open models.
    "qwen3-embedding-8b": 4096,
    "qwen3-embedding-4b": 2560,
    "qwen3-embedding-0.6b": 1024,
    "embed-v1-0.6b": 1024,
}
"""Widths vitruvio already knows, so the common cases need no ``dims`` in configuration.

A convenience, never an authority: whatever comes back is checked against it, and a mismatch is raised rather than
accepted. The table being wrong for a model is a bug that surfaces on the first request, not one that ships an index.
"""


def known_dimensions(model: str) -> int | None:
    """
    The width of a model this build already knows, ignoring any vendor namespace.

    ``openai/text-embedding-3-small`` and ``text-embedding-3-small`` are the same model reached two ways, and a
    router's namespace says where a request went rather than what answered it.

    Args:
        model (str): The model identifier, with or without a namespace and a tag.

    Returns:
        int | None: The width, or ``None`` when this build has never heard of it.
    """
    bare = model.rsplit("/", 1)[-1].split(":", 1)[0].strip().lower()
    return KNOWN_DIMENSIONS.get(bare)


class RemoteEmbedderError(EmbedderUnavailableError):
    """A remote embedding endpoint refused, was unreachable, or answered something unusable.

    Subclasses ``EmbedderUnavailableError`` on purpose: the index build already treats that as "leave this block
    unindexed and report the index as short", which is the right response to a provider being down. A network blip
    must degrade a build, not corrupt one.
    """


class OpenAICompatibleEmbedder:
    """
    The shared half of every provider that answers OpenAI's ``/embeddings``.

    Attributes:
        spec (EmbedderSpec): What was configured.
        model (str): The model identifier sent in every request.
    """

    PROVIDER: ClassVar[str] = "openai-compatible"
    """The registry key, and the ``provider`` field of the tag."""

    DEFAULT_BASE_URL: ClassVar[str] = ""
    """Where requests go when configuration names no ``base_url``."""

    REQUIRES_KEY: ClassVar[bool] = True
    """Whether a missing credential makes this embedder unavailable rather than merely unauthenticated."""

    KEY_PROVIDER: ClassVar[str] = ""
    """Which entry of the kernel's provider-key table holds this one's credential."""

    def __init__(self, spec: EmbedderSpec, *, timeout: float = 60.0) -> None:
        """
        Build the embedder.

        Args:
            spec (EmbedderSpec): Provider, model, dimensions, endpoint and vendor options.
            timeout (float): Per-request timeout in seconds.

        Raises:
            EmbedderUnavailableError: If the width cannot be established. Raised at construction rather than at the
                first request, because the width is part of the model tag and the tag is read before anything is
                embedded -- discovering it later would mean an index that had already named a width it could not
                justify.
        """
        self.spec = spec
        self.model = spec.model
        self.timeout = timeout
        self.base_url = (spec.base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.batch = spec.batch or DEFAULT_BATCH

        width = spec.dims or known_dimensions(spec.model)
        if width is None:
            raise EmbedderUnavailableError(
                f"vitruvio does not know how wide {spec.model!r} is, and the model tag has to carry a width. "
                f"Run `vitruvio config embedder test` to find it, then set dims in [embedding.text]"
            )
        self._dimensions = width

    # --- Identity -------------------------------------------------------------

    @property
    def tag(self) -> ModelTag:
        """
        Where these vectors land.

        Note what is **not** in here: the base URL. Which host answered does not change the vector, and putting the
        endpoint in the tag would make a brain unpublishable between two machines that reach the same model by
        different routes -- one through a local Ollama, one through a gateway.
        """
        return ModelTag(
            provider=self.PROVIDER,
            model=self.model,
            revision=self.spec.revision or UNPINNED,
            dimensions=self._dimensions,
            dtype="f32",
            normalization="l2",
            pooling="none",
            prompts="none",
            preprocess=f"cut{MAX_CHARACTERS}",
            projection="none",
            chunker="none",
        )

    @property
    def dimensions(self) -> int:
        """Vector width."""
        return self._dimensions

    @property
    def modalities(self) -> frozenset[Modality]:
        """Text only. Both providers here expose text embeddings; images go through a vision embedder."""
        return frozenset({Modality.TEXT})

    @property
    def available(self) -> bool:
        """Whether a request could be made at all: the HTTP client installed, and a credential when one is needed."""
        from importlib.util import find_spec

        if find_spec("httpx") is None:
            return False
        return not self.REQUIRES_KEY or self.api_key() is not None

    def api_key(self) -> str | None:
        """
        The credential for this provider, from the environment.

        Returns:
            str | None: The key, or ``None`` when unset or not needed.
        """
        if not self.KEY_PROVIDER:
            return None
        from vitruvio.kernel import provider_key

        secret = provider_key(self.KEY_PROVIDER)
        return secret.reveal() if secret else None

    # --- The parts a subclass changes ----------------------------------------

    def headers(self) -> dict[str, str]:
        """
        The HTTP headers for a request.

        Returns:
            dict[str, str]: Headers. The base sends JSON and a bearer token when there is one.
        """
        headers = {"content-type": "application/json"}
        if key := self.api_key():
            headers["authorization"] = f"Bearer {key}"
        return headers

    def extra_body(self) -> dict[str, Any]:
        """
        Vendor-specific request fields, merged over the standard ones.

        Returns:
            dict[str, Any]: Extra fields. The base contributes whatever ``[embedding.*].options`` declared, which is
            the escape hatch for a vendor feature vitruvio has no opinion about.
        """
        return dict(self.spec.options)

    def interpret(self, status: int, body: str) -> str:
        """
        Turn a refusal into a sentence that names the fix.

        Args:
            status (int): The HTTP status.
            body (str): The response body, already truncated.

        Returns:
            str: What went wrong and what to do about it.
        """
        if status in {401, 403}:
            variable = f"VITRUVIO_{self.KEY_PROVIDER.upper()}_API_KEY" if self.KEY_PROVIDER else "the credential"
            return f"{self.PROVIDER} rejected the credential ({status}); check {variable}"
        if status == 404:
            return f"{self.PROVIDER} has no model called {self.model!r} ({status})"
        return f"{self.PROVIDER} refused with {status}: {body}"

    # --- Embedding ------------------------------------------------------------

    def embed_text(self, texts: Sequence[str], *, role: TextRole = TextRole.PASSAGE) -> list[Vector]:
        """
        Embed strings, positionally aligned with the input.

        The role is not sent. These endpoints take no asymmetry parameter, and prefixing a query by hand would put a
        prompt template into the embedded string without it appearing in the tag -- so a query and a passage would
        land in different places while the tag insisted they were comparable.

        Args:
            texts (Sequence[str]): Strings to embed.
            role (TextRole): Ignored, deliberately.

        Returns:
            list[Vector]: Unit vectors, in the order the inputs were given.

        Raises:
            RemoteEmbedderError: If the provider refused, was unreachable, or answered something unusable.
        """
        if not texts:
            return []

        vectors: list[Vector] = []
        for start in range(0, len(texts), self.batch):
            window = [text[:MAX_CHARACTERS] for text in texts[start : start + self.batch]]
            vectors.extend(self._embed_batch(window))
        return vectors

    def embed_images(self, images: Sequence[ImageInput]) -> list[Vector]:
        """
        Refuse rather than invent.

        Args:
            images (Sequence[ImageInput]): Ignored.

        Raises:
            EmbedderUnavailableError: Always. A text embedder given an image could hash its bytes and return
                something shaped like a vector, and that vector would rank -- meaninglessly -- against real ones.
        """
        raise EmbedderUnavailableError(
            f"{self.PROVIDER} embeds text, not images; configure [embedding.vision] with a vision model"
        )

    def _embed_batch(self, texts: Sequence[str]) -> list[Vector]:
        """
        One request, with retries, reordered and checked.

        Args:
            texts (Sequence[str]): One batch, already truncated.

        Returns:
            list[Vector]: Unit vectors in request order.

        Raises:
            RemoteEmbedderError: If the request cannot be completed or the answer cannot be trusted.
        """
        payload = self.request_body(texts)
        body = self._post(payload)

        if not isinstance(body, dict):
            raise RemoteEmbedderError(f"{self.PROVIDER} returned a JSON response that is not an object")
        items = body.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise RemoteEmbedderError(
                f"{self.PROVIDER} returned {len(items) if isinstance(items, list) else 'no'} embeddings for "
                f"{len(texts)} inputs, so which vector belongs to which text is unknowable"
            )

        # Placed by `index`, never zipped positionally. The field exists because the order is not promised, and
        # pairing text with the wrong vector produces an index that is wrong without ever looking broken.
        ordered: list[Vector | None] = [None] * len(texts)
        for item in items:
            if not isinstance(item, dict):
                raise RemoteEmbedderError(f"{self.PROVIDER} returned an embedding item that is not an object: {item!r}")
            position = item.get("index")
            raw = item.get("embedding")
            if (
                not isinstance(position, int)
                or isinstance(position, bool)
                or not 0 <= position < len(texts)
                or not isinstance(raw, list)
            ):
                raise RemoteEmbedderError(f"{self.PROVIDER} returned an embedding vitruvio cannot place: {item!r}")
            ordered[position] = self._check(raw)

        if any(vector is None for vector in ordered):
            missing = [position for position, vector in enumerate(ordered) if vector is None]
            raise RemoteEmbedderError(f"{self.PROVIDER} skipped inputs at {missing}, leaving gaps in the batch")
        return [vector for vector in ordered if vector is not None]

    def request_body(self, texts: Sequence[str]) -> dict[str, Any]:
        """
        The JSON body for one batch.

        Args:
            texts (Sequence[str]): The inputs.

        Returns:
            dict[str, Any]: The request.
        """
        return {
            "model": self.model,
            "input": list(texts),
            "encoding_format": "float",
            **self.extra_body(),
        }

    def _check(self, raw: list[Any]) -> Vector:
        """
        Verify a vector's width and values against the tag, then normalize it.

        Args:
            raw (list[Any]): The numbers the provider returned.

        Returns:
            Vector: A unit vector.

        Raises:
            RemoteEmbedderError: If the vector is not finite, non-zero numeric data of the claimed width.
        """
        if len(raw) != self._dimensions:
            raise RemoteEmbedderError(
                f"{self.PROVIDER} returned {len(raw)}-wide vectors for {self.model!r} and the model tag says "
                f"{self._dimensions}. An index whose tag misstates its width is exactly what the tag exists to "
                f"prevent, so nothing was indexed. Set dims = {len(raw)} in [embedding.text]"
            )

        if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in raw):
            raise RemoteEmbedderError(
                f"{self.PROVIDER} returned a vector for {self.model!r} containing a non-numeric component"
            )
        try:
            values = [float(value) for value in raw]
        except (OverflowError, ValueError) as error:
            raise RemoteEmbedderError(
                f"{self.PROVIDER} returned a vector for {self.model!r} that cannot be represented as floats"
            ) from error
        if any(not math.isfinite(value) for value in values):
            raise RemoteEmbedderError(
                f"{self.PROVIDER} returned a vector for {self.model!r} containing a non-finite component"
            )
        if not any(value != 0.0 for value in values):
            raise RemoteEmbedderError(
                f"{self.PROVIDER} returned an all-zero vector for {self.model!r}, which cannot be L2-normalized"
            )
        return _normalize(values)

    def _post(self, payload: dict[str, Any]) -> Any:
        """
        Send one request, retrying what is worth retrying.

        Args:
            payload (dict[str, Any]): The request body.

        Returns:
            Any: The parsed JSON response, whose shape is validated by :meth:`_embed_batch`.

        Raises:
            RemoteEmbedderError: If every attempt failed.
        """
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - depends on the installed extras
            raise RemoteEmbedderError(f"{self.PROVIDER} needs an HTTP client; install vitruvio[api]") from error

        url = f"{self.base_url}/embeddings"
        last = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = httpx.post(url, headers=self.headers(), json=payload, timeout=self.timeout)
            except httpx.HTTPError as error:
                last = f"{self.PROVIDER} at {self.base_url} was unreachable: {error}"
                if attempt == MAX_ATTEMPTS:
                    break
                time.sleep(BACKOFF_SECONDS * attempt)
                continue

            if response.status_code == 200:
                try:
                    parsed: Any = response.json()
                except ValueError as error:
                    raise RemoteEmbedderError(
                        f"{self.PROVIDER} answered 200 with something that is not JSON; is {self.base_url} the "
                        f"API and not a web page?"
                    ) from error
                return parsed

            last = self.interpret(response.status_code, response.text[:300])
            if response.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                break
            time.sleep(BACKOFF_SECONDS * attempt)

        raise RemoteEmbedderError(last)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.model} d{self._dimensions} at {self.base_url}>"


class OpenRouterEmbedder(OpenAICompatibleEmbedder):
    """
    Embeddings through OpenRouter, which fronts several vendors behind one key.

    Two things it adds over the base. It accepts a ``provider`` routing object -- ordering, fallbacks, and a
    ``data_collection`` setting -- which is passed through from ``options`` untouched, because routing policy is the
    user's to state and not vitruvio's to have an opinion about. And it takes optional attribution headers that put a
    name next to the request on the account's dashboard.

    Its model names are namespaced (``openai/text-embedding-3-small``), which the width table looks past: a namespace
    says which route reached the model, not which model answered.
    """

    PROVIDER = "openrouter"
    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
    REQUIRES_KEY = True
    KEY_PROVIDER = "openrouter"

    REFERER = "https://github.com/getsfumato/vitruvio"
    TITLE = "vitruvio"

    def headers(self) -> dict[str, str]:
        """The base's headers, plus the attribution OpenRouter shows on the dashboard."""
        return {**super().headers(), "http-referer": self.REFERER, "x-title": self.TITLE}

    def interpret(self, status: int, body: str) -> str:
        """OpenRouter's two failures that are not about the request being wrong."""
        if status == 402:
            return (
                "OpenRouter has no credit left on this key (402); embeddings are billed per token like any other call"
            )
        if status == 429:
            return f"OpenRouter is rate-limiting this key (429): {body}"
        return super().interpret(status, body)


PROBE_TTL_SECONDS = 30.0
"""How long a reachability probe's answer is reused before the daemon is asked again.

``VectorIndex._apply`` reads ``available`` once per block, so the probe has to be memoized or a rebuild becomes one
HTTP request per block. A TTL rather than a permanent answer because the interesting state change -- starting Ollama
while a TUI session is open -- happens on a human timescale, and thirty seconds is short enough to notice it and long
enough that no build pays for the probe more than once.
"""


class OllamaEmbedder(OpenAICompatibleEmbedder):
    """
    Embeddings from a local Ollama.

    The provider that makes a *semantic* index reachable without a key, a bill, or 2.5 GB of torch in vitruvio's own
    environment -- the model runs in a process that already exists on the machine.

    It authenticates nobody, so `available` does not look for a credential; what it looks for is whether the daemon
    answers. And its characteristic failure is a model that was never pulled, which the base would report as a bare
    404 -- so it is translated into the command that fixes it.
    """

    PROVIDER = "ollama"
    DEFAULT_BASE_URL = "http://localhost:11434/v1"
    REQUIRES_KEY = False
    KEY_PROVIDER = ""

    def extra_body(self) -> dict[str, Any]:
        """Ollama honours ``dimensions``, so the width the tag claims is also requested rather than only checked."""
        return {"dimensions": self._dimensions, **super().extra_body()}

    def interpret(self, status: int, body: str) -> str:
        """A model that was never pulled, said as the command that pulls it."""
        if status == 404 or "not found" in body.lower():
            return (
                f"Ollama has no model called {self.model!r}. Pull it first: `ollama pull {self.model}` -- and note "
                f"that a chat model will not answer an embeddings call, so it has to be an embedding model"
            )
        return super().interpret(status, body)

    def __init__(self, spec: EmbedderSpec, *, timeout: float = 60.0) -> None:
        """As the base, plus the memo :attr:`available` needs. See :data:`PROBE_TTL_SECONDS`."""
        super().__init__(spec, timeout=timeout)
        self._probe: tuple[float, bool] | None = None
        self.probe_failure: str | None = None
        """Why the last probe decided the daemon was unreachable, or ``None``.

        Kept because ``available`` has to answer with a bool and a configuration mistake and a stopped daemon are
        not the same problem. A caller reporting unavailability can say which one it was.
        """

    @property
    def available(self) -> bool:
        """
        Whether the daemon is reachable, probed at most once per :data:`PROBE_TTL_SECONDS`.

        Probed rather than assumed, because "Ollama is installed" and "Ollama is running" are different states and
        only the second one can answer. A short timeout: this runs on the path that decides whether to degrade, and
        waiting sixty seconds to find out a local port is closed would make a search hang on a laptop.

        Memoized because of where it is read from. ``VectorIndex._apply`` consults it once per block, so an
        unmemoized probe turned a rebuild of fifty thousand blocks into fifty thousand requests to ``/api/tags``,
        each with a two-second timeout in front of it. A TTL rather than a permanent answer, so that starting the
        daemon during a long-lived session is noticed without restarting it.
        """
        import time
        from importlib.util import find_spec

        if find_spec("httpx") is None:
            return False

        now = time.monotonic()
        if self._probe is not None and now - self._probe[0] < PROBE_TTL_SECONDS:
            return self._probe[1]

        import httpx

        failure: str | None = None
        try:
            httpx.get(self.base_url.removesuffix("/v1") + "/api/tags", timeout=2.0)
        except (httpx.HTTPError, httpx.InvalidURL) as error:
            # Narrowed from a bare `except Exception`, which reported a typo'd `base_url` and a TLS failure as
            # "the daemon is not running" -- sending the user to restart a service that was never the problem.
            # `InvalidURL` is named separately because it does not descend from `HTTPError`. Anything else is a bug
            # here rather than an unreachable endpoint, and is left to surface as one.
            failure = f"{type(error).__name__}: {error}"

        self._probe = (now, failure is None)
        self.probe_failure = failure
        return failure is None
