"""Registry credentials, the Docker traps, and a full publish/install round trip."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from vitruvio.kernel import CredentialError, resolve
from vitruvio.runtime import BrainService, Capability
from vitruvio.runtime.registry import (
    HUB_INDEX_HOSTS,
    HUB_REGISTRY_HOST,
    credential_for,
    forget,
    from_docker,
    host_of,
    is_docker_hub,
    isolate_docker_config,
    normalize_reference,
    store,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class TestReferenceNormalisation:
    @pytest.mark.parametrize("host", sorted(HUB_INDEX_HOSTS))
    def test_docker_hubs_index_hostname_is_rewritten_to_its_api(self, host: str) -> None:
        """`https://docker.io/v2/...` serves the website: HTTP 200 and HTML where a manifest was expected."""
        configured, effective = normalize_reference(f"{host}/alex/brain")
        assert configured == f"{host}/alex/brain"
        assert effective == f"{HUB_REGISTRY_HOST}/alex/brain"

    def test_a_bare_namespace_goes_to_docker_hub(self) -> None:
        """Docker's own shorthand, and Hub is where it resolves."""
        assert normalize_reference("alex/brain")[1] == f"{HUB_REGISTRY_HOST}/alex/brain"

    def test_another_registry_is_left_alone(self) -> None:
        assert normalize_reference("ghcr.io/alex/brain")[1] == "ghcr.io/alex/brain"

    def test_a_port_is_not_mistaken_for_a_tag(self) -> None:
        assert normalize_reference("localhost:5000/alex/brain")[1] == "localhost:5000/alex/brain"

    def test_a_tag_in_the_reference_is_refused(self) -> None:
        """`repo:tag` plus `--tag` produces `repo:tag:tag`."""
        with pytest.raises(CredentialError, match="carries a tag"):
            normalize_reference("docker.io/alex/brain:v1")

    def test_a_reference_with_no_repository_is_refused(self) -> None:
        with pytest.raises(CredentialError, match="names no repository"):
            normalize_reference("docker.io")

    def test_the_host_resolves_through_the_same_rule(self) -> None:
        assert host_of("docker.io/a/b") == HUB_REGISTRY_HOST
        assert host_of("ghcr.io/a/b") == "ghcr.io"

    def test_docker_hub_is_recognised_under_every_spelling(self) -> None:
        assert is_docker_hub("docker.io/a/b")
        assert is_docker_hub("index.docker.io/a/b")
        assert is_docker_hub("a/b")
        assert not is_docker_hub("ghcr.io/a/b")


class TestCredentials:
    def test_a_flag_wins_over_everything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOCKER_USERNAME", "env-account")
        monkeypatch.setenv("DOCKER_TOKEN", "env-token")
        credential = credential_for("docker.io/a/b", username="flag-account", token="flag-token")
        assert credential.username == "flag-account"
        assert credential.source == "flag"

    def test_the_environment_beats_the_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store(HUB_REGISTRY_HOST, "stored-account", "stored-token")
        monkeypatch.setenv("VITRUVIO_REGISTRY_USERNAME", "env-account")
        monkeypatch.setenv("VITRUVIO_REGISTRY_TOKEN", "env-token")
        assert credential_for("docker.io/a/b").username == "env-account"

    def test_the_store_round_trips(self) -> None:
        store(HUB_REGISTRY_HOST, "alex", "dckr_pat_secret")
        credential = credential_for("docker.io/a/b")
        assert credential.username == "alex"
        assert credential.token.reveal() == "dckr_pat_secret"

    def test_a_stored_token_never_prints_itself(self) -> None:
        store(HUB_REGISTRY_HOST, "alex", "dckr_pat_secret")
        credential = credential_for("docker.io/a/b")
        assert "dckr_pat_secret" not in f"{credential.token}"
        assert "dckr_pat_secret" not in repr(credential)

    def test_the_fallback_file_is_not_world_readable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no keyring exists a token lands on disk, and it must not be readable by anyone else."""
        from vitruvio.kernel import credentials_file
        from vitruvio.runtime import registry

        monkeypatch.setattr(registry, "_keyring", lambda: None)
        store("ghcr.io", "alex", "token")
        assert credentials_file().stat().st_mode & 0o077 == 0

    def test_logout_removes_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from vitruvio.runtime import registry

        monkeypatch.setattr(registry, "_keyring", lambda: None)
        store("ghcr.io", "alex", "token")
        assert forget("ghcr.io") is True
        assert credential_for("ghcr.io/a/b").anonymous

    def test_anonymous_is_explicit(self) -> None:
        assert credential_for("docker.io/a/b", anonymous=True).anonymous is True

    def test_docker_is_not_read_unless_asked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A prior `docker login` must not silently decide which account publishes."""
        import base64

        config = tmp_path / "docker"
        config.mkdir()
        auth = base64.b64encode(b"docker-account:docker-token").decode()
        (config / "config.json").write_text(json.dumps({"auths": {HUB_REGISTRY_HOST: {"auth": auth}}}))
        monkeypatch.setenv("DOCKER_CONFIG", str(config))

        assert credential_for("docker.io/a/b").anonymous is True
        assert credential_for("docker.io/a/b", allow_docker=True).username == "docker-account"

    def test_docker_config_is_read_under_either_hostname(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Docker files Hub under its index hostname; vitruvio asks about the API host."""
        import base64

        config = tmp_path / "docker"
        config.mkdir()
        auth = base64.b64encode(b"alex:token").decode()
        (config / "config.json").write_text(json.dumps({"auths": {"docker.io": {"auth": auth}}}))
        monkeypatch.setenv("DOCKER_CONFIG", str(config))

        imported = from_docker(HUB_REGISTRY_HOST)
        assert imported is not None
        assert imported.username == "alex"

    def test_a_missing_docker_config_is_not_an_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "absent"))
        assert from_docker(HUB_REGISTRY_HOST) is None

    def test_a_credential_helper_runs_under_a_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The trap that matters: ORAS runs the same program with *no* timeout, and a blocking helper hangs the push."""
        from vitruvio.runtime import registry

        config = tmp_path / "docker"
        config.mkdir()
        (config / "config.json").write_text(json.dumps({"credsStore": "slow"}))
        monkeypatch.setenv("DOCKER_CONFIG", str(config))

        calls: dict[str, float | None] = {}

        def record(*args: object, **kwargs: object) -> None:
            calls["timeout"] = kwargs.get("timeout")  # type: ignore[assignment]
            raise subprocess.TimeoutExpired(cmd="docker-credential-slow", timeout=1.0)

        monkeypatch.setattr(registry.subprocess, "run", record)
        assert from_docker(HUB_REGISTRY_HOST) is None
        assert calls["timeout"] == registry.HELPER_TIMEOUT, "the helper must be bounded"


class TestOrasIsolation:
    def test_the_credential_store_is_neutralised(self) -> None:
        """Without this, a blocking Docker helper hangs the process with no output at all."""

        class Auth:
            _auth_config = {"auths": {"docker.io": {"auth": "x"}}, "credsStore": "desktop"}

        class Registry:
            auth = Auth()

        class Client:
            registry = Registry()

        client = Client()
        assert isolate_docker_config(client) is None
        assert client.registry.auth._auth_config == {"auths": {}, "credsStore": None, "credHelpers": {}}

    def test_a_reorganised_oras_is_reported_rather_than_silently_skipped(self) -> None:
        """The symptom without the workaround is a hang with no output, which is expensive to rediscover."""

        class Client:
            registry = type("R", (), {"auth": object()})()

        warning = isolate_docker_config(Client())
        assert warning is not None
        assert "hangs" in warning


class TestLocalRoundTrip:
    """A filesystem registry, over the SDK's real contract. No network, no credentials, same code path."""

    @pytest.fixture
    def published(self, tmp_path: Path, source_file: Path) -> tuple[Path, str]:
        """A brain with canonical evidence, derived semantic knowledge, indices, and a published artifact.

        Semantic blocks matter here: a canonical block carries no text until a normalization pipeline produces a view, so
        a brain of canonical blocks alone has nothing embeddable and no vector index to publish. The interesting
        assertions are about the index that travels.
        """
        from boltzmann.blocks.memory_type import MemoryType
        from boltzmann.ingest.proposer import Candidate, CandidateSet

        config = resolve(brain=tmp_path / "producer", actor_id="producer@example.com", require_layout=False)
        service = BrainService(config)
        service.init()
        registered = service.register(source_file, media_type="text/markdown")

        from boltzmann.identity.digest import BlockId

        brain = service.brain(Capability.WRITE)
        source = BlockId.parse(registered["block_id"])
        task = brain.define_task(source, allowed=[MemoryType.SEMANTIC])
        candidates = CandidateSet(
            task_id=task.task_id,
            candidates=[
                Candidate(
                    memory_type=MemoryType.SEMANTIC,
                    evidence=[source],
                    locator="p1",
                    payload={
                        "kind": "concept",
                        "label": "Serie de Fourier",
                        "subject": "senales",
                        "statement": "Descompone una funcion periodica en senos y cosenos.",
                    },
                )
            ],
        )
        brain.commit(brain.validate(candidates, task))
        service.index_build()

        registry_root = tmp_path / "registry"
        registry_root.mkdir()
        service.push("demo/brain", tag="v1", local=registry_root)
        return registry_root, "demo/brain"

    def test_a_pull_installs_a_verifiable_brain(self, published: tuple[Path, str], tmp_path: Path) -> None:
        registry_root, reference = published
        config = resolve(brain=tmp_path / "consumer", actor_id="consumer@example.com", require_layout=False)
        consumer = BrainService(config)
        consumer.init()

        consumer.pull(reference, tag="v1", local=registry_root)
        assert consumer.verify()["verified"] is True
        assert "canonical" in consumer.state()["installed"]

    def test_plan_pull_reports_the_transfer_before_paying_for_it(
        self, published: tuple[Path, str], tmp_path: Path
    ) -> None:
        """A canonical layer can be gigabytes, so this has to be answerable without transferring it."""
        registry_root, reference = published
        config = resolve(brain=tmp_path / "consumer", actor_id="c@example.com", require_layout=False)
        consumer = BrainService(config)
        consumer.init()

        plan = consumer.plan_pull(reference, tag="v1", local=registry_root)
        assert plan["fetch_layers"]
        assert plan["fetch_bytes"] > 0
        assert plan["is_noop"] is False

    def test_pulling_twice_is_a_noop(self, published: tuple[Path, str], tmp_path: Path) -> None:
        registry_root, reference = published
        config = resolve(brain=tmp_path / "consumer", actor_id="c@example.com", require_layout=False)
        consumer = BrainService(config)
        consumer.init()
        consumer.pull(reference, tag="v1", local=registry_root)

        assert consumer.plan_pull(reference, tag="v1", local=registry_root)["is_noop"] is True

    def test_a_selective_pull_leaves_the_rest_missing(self, published: tuple[Path, str], tmp_path: Path) -> None:
        """Missing rather than broken, which is what makes selective installation usable."""
        registry_root, reference = published
        config = resolve(brain=tmp_path / "consumer", actor_id="c@example.com", require_layout=False)
        consumer = BrainService(config)
        consumer.init()

        consumer.pull(reference, tag="v1", modules=["canonical"], local=registry_root)
        assert consumer.state()["installed"] == ["canonical"]

    def test_tags_lists_what_was_published(self, published: tuple[Path, str], tmp_path: Path) -> None:
        registry_root, reference = published
        config = resolve(brain=tmp_path / "consumer", actor_id="c@example.com", require_layout=False)
        consumer = BrainService(config)
        consumer.init()
        assert consumer.tags(reference, local=registry_root)["tags"] == ["v1"]

    def test_an_unpublished_repository_has_no_tags_rather_than_an_error(self, tmp_path: Path) -> None:
        """The ordinary state before a first push. Reporting it as a failure made the CLI exit 1."""
        registry_root = tmp_path / "registry"
        registry_root.mkdir()
        config = resolve(brain=tmp_path / "brain", actor_id="c@example.com", require_layout=False)
        service = BrainService(config)
        service.init()

        result = service.tags("demo/nothing", local=registry_root)
        assert result["tags"] == []
        assert result["published"] is False

    def test_the_vector_index_travels(self, published: tuple[Path, str], tmp_path: Path) -> None:
        """The one index a consumer cannot rebuild. If it does not travel, a pulled brain cannot be searched
        semantically -- and the omission is otherwise silent."""
        registry_root, reference = published
        config = resolve(brain=tmp_path / "consumer", actor_id="c@example.com", require_layout=False)
        consumer = BrainService(config)
        consumer.init()

        plan = consumer.plan_pull(reference, tag="v1", local=registry_root)
        assert plan["fetch_vector_indices"], "no vector index was published"

    def test_a_reference_that_names_no_repository_is_a_usage_error(self, tmp_path: Path) -> None:
        """Checked on the remote path: a local layout takes the reference verbatim as a directory name, so there is no
        host to resolve and nothing to validate."""
        config = resolve(brain=tmp_path / "brain", actor_id="c@example.com", require_layout=False)
        service = BrainService(config)
        service.init()
        with pytest.raises(CredentialError, match="names no repository"):
            service.tags("docker.io", anonymous=True)


def _docker_available() -> bool:
    """Whether a container runtime is reachable. Installed is not the same as running."""
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    # Not `returncode == 0`: with Docker Desktop installed and its daemon stopped, `docker info` still exits 0 and
    # simply leaves ServerVersion empty (the error goes to stderr). Trusting the exit status is how these tests came to
    # be attempted against a dead daemon, where `docker run` then hangs rather than failing.
    return bool(completed.stdout.strip())


CONTAINER_NAME = "vitruvio-test-registry"
CONTAINER_PORT = 5555


@pytest.fixture(scope="module")
def endpoint() -> Iterator[str]:
    """An ephemeral ``registry:2`` on a high port, removed afterwards.

    Module-scoped and defined at module level: a class-scoped fixture written as an instance method is deprecated, and
    with ``filterwarnings = error`` the deprecation becomes a collection error that fires *before* the skip is consulted.
    """
    import time
    import urllib.error
    import urllib.request

    # Every docker call is bounded. A daemon that is reachable but wedged makes these block indefinitely, and a test
    # suite that hangs is worse than one that fails.
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True, check=False, timeout=30)
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", CONTAINER_NAME, "-p", f"{CONTAINER_PORT}:5000", "registry:2"],
        capture_output=True,
        check=True,
        timeout=180,  # generous: the first run pulls the registry:2 image.
    )
    # Waited on the *API*, not the container: `docker run -d` returns as soon as the container starts, which is before
    # the registry answers.
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://localhost:{CONTAINER_PORT}/v2/", timeout=1)
            break
        except urllib.error.HTTPError:
            break  # 401 is an answer: the API is up.
        except OSError:
            time.sleep(0.25)

    yield f"localhost:{CONTAINER_PORT}"
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True, check=False, timeout=30)


@pytest.mark.slow
@pytest.mark.registry
@pytest.mark.skipif(not _docker_available(), reason="needs a running Docker daemon")
class TestContainerRegistry:
    """The real HTTP path, against ``registry:2`` in a container.

    Separate from Docker Hub deliberately. This exercises auth-less HTTP, the insecure flag, resolve/pull/push, the
    fast-forward guard and the media-type preflight -- with no credentials and no rate limit. Docker Hub and ghcr.io are
    only worth touching from a manual workflow with secrets: publishing to a public registry on every test run would
    pollute it and burn quota.
    """

    def test_the_preflight_accepts_a_brain_shaped_artifact(
        self, endpoint: str, tmp_path: Path, source_file: Path
    ) -> None:
        """The question a first push otherwise answers the hard way: is a custom config media type accepted?"""
        config = resolve(brain=tmp_path / "brain", actor_id="p@example.com", require_layout=False)
        service = BrainService(config)
        service.init()
        service.register(source_file, media_type="text/markdown")

        result = service.registry_check(f"{endpoint}/demo/brain", anonymous=True, insecure=True)
        assert result["ok"] is True, result

    def test_a_full_round_trip_over_http(self, endpoint: str, tmp_path: Path, source_file: Path) -> None:
        config = resolve(brain=tmp_path / "producer", actor_id="p@example.com", require_layout=False)
        producer = BrainService(config)
        producer.init()
        producer.register(source_file, media_type="text/markdown")
        producer.index_build()
        producer.push(f"{endpoint}/demo/brain", tag="v1", anonymous=True, insecure=True)

        consumer_config = resolve(brain=tmp_path / "consumer", actor_id="c@example.com", require_layout=False)
        consumer = BrainService(consumer_config)
        consumer.init()
        consumer.pull(f"{endpoint}/demo/brain", tag="v1", anonymous=True, insecure=True)
        assert consumer.verify()["verified"] is True
