"""Registry credentials, the Docker traps, and a full publish/install round trip."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

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

    def test_a_keyring_that_refuses_is_reported_rather_than_called_a_logout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`logout` printing "logged out of" over a secret that still authenticates is worse than failing.

        The delete was wrapped in `except Exception: pass`, so a backend that refused produced a successful-looking
        logout as long as *some* other store had something to remove.
        """
        from vitruvio.kernel import CredentialError
        from vitruvio.runtime import registry

        class Refusing:
            def get_password(self, service: str, key: str) -> str | None:
                return "token"

            def set_password(self, service: str, key: str, value: str) -> None:
                return None

            def delete_password(self, service: str, key: str) -> None:
                raise RuntimeError("the keychain is locked")

        monkeypatch.setattr(registry, "_keyring", Refusing)
        store("ghcr.io", "alex", "token")

        with pytest.raises(CredentialError, match="would not delete"):
            forget("ghcr.io")

    def test_a_refused_delete_does_not_orphan_the_credential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The username mapping is how vitruvio addresses a keyring entry, and it was popped either way.

        So a refused delete left a secret that still authenticated and that `forget` could never find again --
        undeletable by vitruvio, and invisible to `whoami`.
        """
        from vitruvio.runtime import registry

        class Refusing:
            def get_password(self, service: str, key: str) -> str | None:
                return "token"

            def set_password(self, service: str, key: str, value: str) -> None:
                return None

            def delete_password(self, service: str, key: str) -> None:
                raise RuntimeError("the keychain is locked")

        monkeypatch.setattr(registry, "_keyring", Refusing)
        store("ghcr.io", "alex", "token")
        assert "ghcr.io" in registry._read_usernames()

        with pytest.raises(Exception, match="would not delete"):
            forget("ghcr.io")
        assert "ghcr.io" in registry._read_usernames(), "the mapping is the only way back to that secret"

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


@pytest.fixture
def published(tmp_path: Path, source_file: Path) -> tuple[Path, str]:
    """A brain with canonical evidence, derived semantic knowledge, indices, and a published artifact.

    Semantic blocks matter here: a canonical block carries no text until a normalization pipeline produces a view, so
    a brain of canonical blocks alone has nothing embeddable and no vector index to publish. The interesting
    assertions are about the index that travels.

    Module level rather than inside one class, because two classes need it -- and a class-scoped fixture reached from
    a second class fails as "fixture 'published' not found", which reads like a typo rather than like a scope.
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


class TestLocalRoundTrip:
    """A filesystem registry, over the SDK's real contract. No network, no credentials, same code path."""

    def test_a_pull_installs_a_verifiable_brain(self, published: tuple[Path, str], tmp_path: Path) -> None:
        registry_root, reference = published
        config = resolve(brain=tmp_path / "consumer", actor_id="consumer@example.com", require_layout=False)
        consumer = BrainService(config)
        consumer.init()

        consumer.pull(reference, tag="v1", local=registry_root)
        assert consumer.verify()["verified"] is True
        assert "canonical" in consumer.state()["installed"]

    async def test_async_runtime_round_trips_without_owning_the_callers_loop(
        self, tmp_path: Path, source_file: Path
    ) -> None:
        registry_root = tmp_path / "registry"
        registry_root.mkdir()
        producer = BrainService(resolve(brain=tmp_path / "producer", actor_id="p@example.com", require_layout=False))
        producer.init()
        producer.register(source_file, media_type="text/markdown")

        pushed = await producer.push_async("demo/async", tag="v1", local=registry_root)

        consumer = BrainService(resolve(brain=tmp_path / "consumer", actor_id="c@example.com", require_layout=False))
        consumer.init()
        plan = await consumer.plan_pull_async("demo/async", tag="v1", local=registry_root)
        pulled = await consumer.pull_async("demo/async", tag="v1", local=registry_root)
        fetched = await consumer.fetch_async("demo/async", tag="v1", reconcile=False, local=registry_root)

        assert pushed["digest"]
        assert plan["is_noop"] is False
        assert pulled["snapshot"]["digest"]
        assert fetched["reconciliation"]["why"] == "not requested"
        assert consumer.verify()["verified"] is True

    async def test_sync_compatibility_methods_are_safe_inside_an_existing_loop(
        self, published: tuple[Path, str], tmp_path: Path
    ) -> None:
        registry_root, reference = published
        consumer = BrainService(resolve(brain=tmp_path / "consumer", actor_id="c@example.com", require_layout=False))
        consumer.init()

        plan = consumer.plan_pull(reference, tag="v1", local=registry_root)
        pulled = consumer.pull(reference, tag="v1", local=registry_root)

        assert plan["is_noop"] is False
        assert pulled["snapshot"]["digest"]

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

    def test_vector_indices_can_be_ignored_without_omitting_their_modules(
        self, published: tuple[Path, str], tmp_path: Path
    ) -> None:
        registry_root, reference = published
        config = resolve(brain=tmp_path / "consumer", actor_id="c@example.com", require_layout=False)
        consumer = BrainService(config)
        consumer.init()

        plan = consumer.plan_pull(reference, tag="v1", ignore_vector_indices=True, local=registry_root)
        result = consumer.pull(reference, tag="v1", ignore_vector_indices=True, local=registry_root)

        assert plan["fetch_vector_indices"] == []
        assert plan["ignored_vector_indices"] == ["semantic"]
        assert result["ignored_vector_indices"] == ["semantic"]
        assert result["partial"] is False
        assert consumer.verify()["verified"] is True
        assert set(consumer.state()["installed"]) == {"canonical", "semantic", "provenance"}
        assert any("index build --force" in warning for warning in result["warnings"])

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


class TestWhatAPullReplaces:
    """A pull adopts the published composition and moves the head to it, with no fast-forward check.

    That asymmetry is deliberate -- the divergence guard belongs on `push`, where overwriting means overwriting
    somebody *else's* work -- but the loss used to be silent. Blocks committed locally since the last pull stop being
    members of any composition: they do not verify into a root, do not appear in a search, and a pack does not carry
    them. The blobs stay and the snapshot stays in `brain history`, so nothing is destroyed; nothing said it happened
    either, and the discovery came days later when a search returned nothing.
    """

    @pytest.fixture
    def mine(self, tmp_path: Path) -> Path:
        """A file the producer never published, so registering it is a real commit rather than a duplicate."""
        path = tmp_path / "mine.md"
        path.write_text("# Nota mia\n\nEsto no esta en el upstream.\n", encoding="utf-8")
        return path

    @pytest.fixture
    def consumer(self, published: tuple[Path, str], tmp_path: Path) -> BrainService:
        """A brain that pulled the published version and has committed nothing of its own."""
        registry_root, reference = published
        config = resolve(brain=tmp_path / "consumer", actor_id="c@example.com", require_layout=False)
        service = BrainService(config)
        service.init()
        service.pull(reference, tag="v1", local=registry_root)
        return service

    def test_a_clean_copy_reports_nothing_to_lose(self, consumer: BrainService, published: tuple[Path, str]) -> None:
        """The half that matters for noise: a warning on every pull would be ignored by the third one."""
        registry_root, reference = published
        plan = consumer.plan_pull(reference, tag="v1", local=registry_root)
        assert plan["local_work"]["diverged"] is False
        assert plan["local_work"]["blocks"] == 0
        assert plan["impact"]["certainty"] == "exact"

    def test_a_fresh_empty_brain_reports_nothing_to_lose(self, published: tuple[Path, str], tmp_path: Path) -> None:
        """An empty brain has never pulled and has no origin, which is the case that would otherwise be reported as
        "everything you have is at stake" over a brain holding nothing."""
        registry_root, reference = published
        config = resolve(brain=tmp_path / "fresh", actor_id="c@example.com", require_layout=False)
        service = BrainService(config)
        service.init()
        assert service.plan_pull(reference, tag="v1", local=registry_root)["local_work"]["diverged"] is False

    def test_a_local_commit_is_reported_before_the_pull(
        self, consumer: BrainService, published: tuple[Path, str], mine: Path
    ) -> None:
        """`plan-pull` exists to answer "what would this cost" before paying it, and losing local work is a cost."""
        registry_root, reference = published
        consumer.register(mine, media_type="text/markdown", origin="local://mine")

        work = consumer.plan_pull(reference, tag="v1", local=registry_root)["local_work"]
        assert work["diverged"] is True
        assert work["blocks"] is not None
        assert work["blocks"] > 0
        assert work["certainty"] == "approximate"
        assert work["snapshot"] is not None

    def test_selective_plan_and_pull_count_local_work_in_modules_the_install_omits(
        self, consumer: BrainService, published: tuple[Path, str], mine: Path
    ) -> None:
        registry_root, reference = published
        local = consumer.register(mine, media_type="text/markdown", origin="local://mine")

        plan = consumer.plan_pull(reference, tag="v1", modules=["semantic"], local=registry_root)
        result = consumer.pull(reference, tag="v1", modules=["semantic"], local=registry_root, allow_rollback=True)

        assert plan["local_work"]["diverged"] is True
        assert plan["impact"]["certainty"] == "approximate"
        assert plan["impact"]["blocks"] > 0
        assert result["impact"]["certainty"] == "exact"
        assert result["impact"]["blocks"] > 0
        assert local["block_id"] in result["impact"]["block_ids"]
        assert "canonical" not in consumer.state()["installed"]

    def test_the_pull_reports_exactly_what_left_the_composition(
        self, consumer: BrainService, published: tuple[Path, str], mine: Path
    ) -> None:
        """Counted here rather than estimated: this is the one moment both compositions are known, so the report can
        say what happened instead of what was likely to."""
        registry_root, reference = published
        registered = consumer.register(mine, media_type="text/markdown", origin="local://mine")

        result = consumer.pull(reference, tag="v1", local=registry_root, allow_rollback=True)
        assert result["discarded"] > 0
        assert registered["block_id"] in result["discarded_blocks"]
        assert result["impact"]["certainty"] == "exact"

    def test_the_discarded_block_really_is_out_of_the_composition(
        self, consumer: BrainService, published: tuple[Path, str], mine: Path
    ) -> None:
        """The report would be worthless if it were describing something that had not happened. Checked against the
        module rather than against the report."""
        registry_root, reference = published
        registered = consumer.register(mine, media_type="text/markdown", origin="local://mine")
        consumer.pull(reference, tag="v1", local=registry_root, allow_rollback=True)

        held = consumer.module("canonical", limit=100)["block_ids"]
        assert registered["block_id"] not in held
        assert consumer.verify()["verified"] is True, "and the brain is not broken by it, only narrower"

    def test_the_snapshot_that_held_them_is_still_recoverable(
        self, consumer: BrainService, published: tuple[Path, str], mine: Path
    ) -> None:
        """What makes this a warning rather than an error: the state is still there to go back to by hand. If this
        ever stops being true, the warning has to become a refusal."""
        registry_root, reference = published
        consumer.register(mine, media_type="text/markdown", origin="local://mine")
        before = str(consumer.state()["snapshot"]["digest"])

        consumer.pull(reference, tag="v1", local=registry_root, allow_rollback=True)
        assert before in [entry["digest"] for entry in consumer.history()["snapshots"]]

    def test_a_pull_that_changes_nothing_discards_nothing(
        self, consumer: BrainService, published: tuple[Path, str]
    ) -> None:
        registry_root, reference = published
        assert consumer.pull(reference, tag="v1", local=registry_root)["discarded"] == 0
