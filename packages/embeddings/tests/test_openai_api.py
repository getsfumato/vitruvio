"""The OpenAI-shaped providers, tested without a network.

Every request is intercepted. What is under test is the part that would be wrong in a way nothing downstream
reports -- ordering, width, batching -- and none of that needs a real endpoint to establish. Reaching for one would
buy a slower suite that fails when someone's key expires.
"""

from __future__ import annotations

from typing import Any

import pytest

from vitruvio.embeddings import (
    EmbedderUnavailableError,
    OllamaEmbedder,
    OpenRouterEmbedder,
    RemoteEmbedderError,
    known_dimensions,
    resolve,
)
from vitruvio.embeddings.openai_api import MAX_CHARACTERS
from vitruvio.kernel import EmbedderSpec

pytest.importorskip("httpx", reason="the api extra is not installed")

import httpx


class Recorder:
    """Stands in for `httpx.post`, recording what was sent and replying with what a test dictates."""

    def __init__(self, *replies: httpx.Response) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.urls: list[str] = []

    def __call__(self, url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float) -> httpx.Response:
        self.urls.append(url)
        self.headers.append(headers)
        self.requests.append(json)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def reply(vectors: list[list[float]], *, shuffle: bool = False, status: int = 200) -> httpx.Response:
    """An OpenAI-shaped embeddings response."""
    data = [{"index": position, "embedding": vector} for position, vector in enumerate(vectors)]
    if shuffle:
        data.reverse()
    return httpx.Response(status, json={"data": data}, request=httpx.Request("POST", "http://test"))


def refusal(status: int, body: str = "") -> httpx.Response:
    """A refusal with a body, the way a vendor sends one."""
    return httpx.Response(status, text=body, request=httpx.Request("POST", "http://test"))


def openrouter(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> OpenRouterEmbedder:
    """An OpenRouter embedder with a key in the environment."""
    monkeypatch.setenv("VITRUVIO_OPENROUTER_API_KEY", "sk-test")
    spec = EmbedderSpec(provider="openrouter", model="openai/text-embedding-3-small", **overrides)
    return OpenRouterEmbedder(spec)


def ollama(**overrides: Any) -> OllamaEmbedder:
    """An Ollama embedder."""
    overrides.setdefault("model", "nomic-embed-text")
    return OllamaEmbedder(EmbedderSpec(provider="ollama", **overrides))


class TestWidth:
    def test_a_known_model_needs_no_dims_in_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert openrouter(monkeypatch).dimensions == 1536
        assert ollama().dimensions == 768

    def test_a_vendor_namespace_is_looked_past(self) -> None:
        """`openai/text-embedding-3-small` and `text-embedding-3-small` are one model reached two ways; a namespace
        says which route got there, not what answered."""
        assert known_dimensions("openai/text-embedding-3-small") == 1536
        assert known_dimensions("text-embedding-3-small") == 1536
        assert known_dimensions("nomic-embed-text:latest") == 768

    def test_an_unknown_model_is_refused_at_construction_naming_the_fix(self) -> None:
        """Refused here rather than at the first request: the width is part of the model tag, and the tag is read
        before anything is embedded -- so discovering it later means an index that already named a width."""
        with pytest.raises(EmbedderUnavailableError, match="config embedder test"):
            OllamaEmbedder(EmbedderSpec(provider="ollama", model="algo-nuevo"))

    def test_configured_dims_cover_a_model_vitruvio_does_not_know(self) -> None:
        assert ollama(model="algo-nuevo", dims=512).dimensions == 512

    def test_a_response_of_the_wrong_width_is_refused_rather_than_indexed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tag that says 768 over 1024-wide vectors is the exact failure the tag exists to prevent."""
        embedder = ollama()
        monkeypatch.setattr(httpx, "post", Recorder(reply([[0.1] * 1024])))
        with pytest.raises(RemoteEmbedderError, match="dims = 1024"):
            embedder.embed_text(["hola"])


class TestOrdering:
    def test_vectors_are_placed_by_index_not_zipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The response is not promised to arrive sorted. Zipping it against the request pairs text with the wrong
        vector, and an index built that way is wrong without ever looking broken."""
        embedder = ollama(dims=2)
        first, second = [1.0, 0.0], [0.0, 1.0]
        monkeypatch.setattr(httpx, "post", Recorder(reply([first, second], shuffle=True)))

        vectors = embedder.embed_text(["primero", "segundo"])
        assert vectors[0] == pytest.approx(tuple(first))
        assert vectors[1] == pytest.approx(tuple(second))

    def test_a_short_response_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fewer vectors than inputs makes the pairing unknowable, so nothing is guessed."""
        embedder = ollama(dims=2)
        monkeypatch.setattr(httpx, "post", Recorder(reply([[1.0, 0.0]])))
        with pytest.raises(RemoteEmbedderError, match="unknowable"):
            embedder.embed_text(["uno", "dos"])

    def test_an_out_of_range_index_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        embedder = ollama(dims=2)
        broken = httpx.Response(
            200,
            json={"data": [{"index": 7, "embedding": [1.0, 0.0]}]},
            request=httpx.Request("POST", "http://test"),
        )
        monkeypatch.setattr(httpx, "post", Recorder(broken))
        with pytest.raises(RemoteEmbedderError, match="cannot place"):
            embedder.embed_text(["uno"])


class TestBatching:
    def test_a_long_input_is_split_and_reassembled_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        embedder = ollama(dims=1, batch=2)
        recorder = Recorder(reply([[1.0], [2.0]]), reply([[3.0]]))
        monkeypatch.setattr(httpx, "post", recorder)

        vectors = embedder.embed_text(["a", "b", "c"])
        assert len(recorder.requests) == 2
        assert [request["input"] for request in recorder.requests] == [["a", "b"], ["c"]]
        assert len(vectors) == 3

    def test_no_input_means_no_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = Recorder(reply([]))
        monkeypatch.setattr(httpx, "post", recorder)
        assert ollama().embed_text([]) == []
        assert recorder.requests == []

    def test_an_over_long_input_is_cut_rather_than_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bounded before the request rather than after a refusal, and the bound is in the tag because it changes
        which string got embedded."""
        embedder = ollama(dims=1)
        recorder = Recorder(reply([[1.0]]))
        monkeypatch.setattr(httpx, "post", recorder)

        embedder.embed_text(["x" * (MAX_CHARACTERS + 5000)])
        assert len(recorder.requests[0]["input"][0]) == MAX_CHARACTERS
        assert f"cut{MAX_CHARACTERS}" in embedder.tag.render()


class TestVectors:
    def test_vectors_come_back_normalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The tag says l2, so a dot product has to be a cosine."""
        embedder = ollama(dims=2)
        monkeypatch.setattr(httpx, "post", Recorder(reply([[3.0, 4.0]])))
        (vector,) = embedder.embed_text(["hola"])
        assert sum(value * value for value in vector) == pytest.approx(1.0)

    def test_an_image_is_refused_rather_than_hashed_into_something_rankable(self) -> None:
        from vitruvio.embeddings import ImageInput

        with pytest.raises(EmbedderUnavailableError, match="vision"):
            ollama().embed_images([ImageInput(data=b"x", media_type="image/png")])


class TestTag:
    def test_the_base_url_is_not_in_the_tag(self) -> None:
        """Which host answered does not change the vector. In the tag, it would make a brain unpublishable between
        two machines that reach the same model by different routes."""
        default = ollama().tag.render()
        elsewhere = ollama(base_url="http://gpu-box:11434/v1").tag.render()
        assert default == elsewhere

    def test_the_provider_is_in_the_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert ollama().tag.provider == "ollama"
        assert openrouter(monkeypatch).tag.provider == "openrouter"

    def test_a_remote_model_is_semantic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unlike hashing, which says so in its tag so nothing mistakes it for meaning."""
        assert openrouter(monkeypatch).tag.is_semantic is True

    def test_an_unpinned_revision_is_recorded_honestly(self) -> None:
        """ "We do not know what produced these vectors" is exactly what a consumer needs to be told."""
        assert ollama().tag.revision == "unpinned"
        assert ollama(revision="2026-01").tag.revision == "2026-01"


class TestOpenRouterParticulars:
    def test_it_sends_the_key_and_the_attribution_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        embedder = openrouter(monkeypatch, dims=2)
        recorder = Recorder(reply([[1.0, 0.0]]))
        monkeypatch.setattr(httpx, "post", recorder)
        embedder.embed_text(["hola"])

        headers = recorder.headers[0]
        assert headers["authorization"] == "Bearer sk-test"
        assert headers["x-title"] == "vitruvio"
        assert recorder.urls[0] == "https://openrouter.ai/api/v1/embeddings"

    def test_routing_options_pass_through_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Routing policy is the user's to state; vitruvio has no opinion to impose on it."""
        routing = {"provider": {"order": ["openai"], "allow_fallbacks": False, "data_collection": "deny"}}
        embedder = openrouter(monkeypatch, dims=2, options=routing)
        recorder = Recorder(reply([[1.0, 0.0]]))
        monkeypatch.setattr(httpx, "post", recorder)
        embedder.embed_text(["hola"])

        assert recorder.requests[0]["provider"] == routing["provider"]

    def test_no_credit_is_reported_as_no_credit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        embedder = openrouter(monkeypatch, dims=2)
        monkeypatch.setattr(httpx, "post", Recorder(refusal(402, "insufficient credits")))
        monkeypatch.setattr("time.sleep", lambda _seconds: None)
        with pytest.raises(RemoteEmbedderError, match="no credit"):
            embedder.embed_text(["hola"])

    def test_a_missing_key_makes_it_unavailable_rather_than_failing_mid_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VITRUVIO_OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        embedder = OpenRouterEmbedder(EmbedderSpec(provider="openrouter", model="openai/text-embedding-3-small"))
        assert embedder.available is False


class TestOllamaParticulars:
    def test_it_needs_no_credential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        embedder = ollama(dims=2)
        recorder = Recorder(reply([[1.0, 0.0]]))
        monkeypatch.setattr(httpx, "post", recorder)
        embedder.embed_text(["hola"])
        assert "authorization" not in recorder.headers[0]

    def test_it_asks_for_the_width_the_tag_claims(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ollama honours `dimensions`, so the width is requested rather than only checked afterwards."""
        embedder = ollama(dims=2)
        recorder = Recorder(reply([[1.0, 0.0]]))
        monkeypatch.setattr(httpx, "post", recorder)
        embedder.embed_text(["hola"])
        assert recorder.requests[0]["dimensions"] == 2

    def test_a_model_that_was_never_pulled_is_reported_as_the_command_that_pulls_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        embedder = ollama(dims=2)
        monkeypatch.setattr(httpx, "post", Recorder(refusal(404, "model not found")))
        monkeypatch.setattr("time.sleep", lambda _seconds: None)
        with pytest.raises(RemoteEmbedderError, match="ollama pull nomic-embed-text"):
            embedder.embed_text(["hola"])

    def test_the_default_endpoint_is_the_local_daemon(self) -> None:
        assert ollama().base_url == "http://localhost:11434/v1"

    def test_another_host_is_reachable(self) -> None:
        assert ollama(base_url="http://gpu-box:11434/v1").base_url == "http://gpu-box:11434/v1"


class TestOllamaReachabilityProbe:
    """`available` does I/O, and `VectorIndex._apply` reads it once per block."""

    def test_the_probe_runs_once_across_many_reads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unmemoized, a rebuild of fifty thousand blocks was fifty thousand requests to /api/tags, each behind a
        two-second timeout."""
        embedder = ollama(dims=2)
        calls: list[str] = []

        def probe(url: str, **kwargs: Any) -> Any:
            calls.append(url)
            return reply([[1.0, 0.0]])

        monkeypatch.setattr(httpx, "get", probe)
        assert all(embedder.available for _ in range(50))
        assert len(calls) == 1, f"the daemon was probed {len(calls)} times for one answer"
        assert calls[0].endswith("/api/tags")

    def test_the_answer_is_re_probed_once_the_ttl_lapses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Starting the daemon during a long-lived session has to be noticed without a restart."""
        from vitruvio.embeddings import openai_api

        embedder = ollama(dims=2)
        calls: list[str] = []

        def probe(url: str, **kwargs: Any) -> Any:
            calls.append(url)
            return reply([[1.0, 0.0]])

        monkeypatch.setattr(httpx, "get", probe)

        clock = [1000.0]
        monkeypatch.setattr("time.monotonic", lambda: clock[0])
        assert embedder.available
        clock[0] += openai_api.PROBE_TTL_SECONDS + 1
        assert embedder.available
        assert len(calls) == 2

    def test_an_unreachable_daemon_is_unavailable_and_says_why(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(url: str, **kwargs: Any) -> Any:
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "get", refuse)
        embedder = ollama(dims=2)
        assert embedder.available is False
        assert "ConnectError" in (embedder.probe_failure or "")

    def test_a_malformed_endpoint_is_not_reported_as_a_stopped_daemon(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The bare `except Exception` reported a typo'd base_url as "the daemon is not running", which sends the
        user to restart a service that was never the problem."""

        def reject(url: str, **kwargs: Any) -> Any:
            raise httpx.UnsupportedProtocol("Request URL has an unsupported protocol 'htp://'")

        monkeypatch.setattr(httpx, "get", reject)
        embedder = ollama(dims=2, base_url="htp://localhost:11434/v1")
        assert embedder.available is False
        assert "UnsupportedProtocol" in (embedder.probe_failure or "")

    def test_a_bug_in_the_probe_is_not_swallowed_as_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`except Exception` hid programming errors here too, as a permanent silent degradation."""

        def broken(url: str, **kwargs: Any) -> Any:
            raise TypeError("someone changed the signature")

        monkeypatch.setattr(httpx, "get", broken)
        with pytest.raises(TypeError):
            _ = ollama(dims=2).available


class TestFailure:
    def test_a_rate_limit_is_retried_then_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        embedder = ollama(dims=1)
        recorder = Recorder(refusal(429, "slow down"), refusal(429, "slow down"), reply([[1.0]]))
        monkeypatch.setattr(httpx, "post", recorder)
        monkeypatch.setattr("time.sleep", lambda _seconds: None)

        assert len(embedder.embed_text(["hola"])) == 1
        assert len(recorder.requests) == 3, "it should have retried twice before succeeding"

    def test_a_bad_request_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 400 will fail identically forever, so retrying only delays the error."""
        embedder = ollama(dims=1)
        recorder = Recorder(refusal(400, "malformed"))
        monkeypatch.setattr(httpx, "post", recorder)
        monkeypatch.setattr("time.sleep", lambda _seconds: None)

        with pytest.raises(RemoteEmbedderError):
            embedder.embed_text(["hola"])
        assert len(recorder.requests) == 1

    def test_html_from_a_wrong_base_url_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pointing at a web page rather than an API is a common mistake, and a JSON parse error a long way from
        its cause is the worst way to learn about it."""
        embedder = ollama(dims=1)
        page = httpx.Response(200, text="<html>hola</html>", request=httpx.Request("POST", "http://test"))
        monkeypatch.setattr(httpx, "post", Recorder(page))
        with pytest.raises(RemoteEmbedderError, match="not a web page"):
            embedder.embed_text(["hola"])

    def test_a_remote_failure_is_an_unavailability_so_a_build_degrades(self) -> None:
        """The index build catches EmbedderUnavailableError and leaves the block unindexed. A network blip must
        degrade a build, never corrupt one."""
        assert issubclass(RemoteEmbedderError, EmbedderUnavailableError)


class TestRegistry:
    def test_both_providers_resolve_from_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VITRUVIO_OPENROUTER_API_KEY", "sk-test")
        assert isinstance(
            resolve(EmbedderSpec(provider="openrouter", model="openai/text-embedding-3-small")), OpenRouterEmbedder
        )
        assert isinstance(resolve(EmbedderSpec(provider="ollama", model="nomic-embed-text")), OllamaEmbedder)

    def test_an_unknown_provider_is_still_refused_rather_than_substituted(self) -> None:
        with pytest.raises(EmbedderUnavailableError, match="no embedder provider"):
            resolve(EmbedderSpec(provider="telepathy", model="m"))
