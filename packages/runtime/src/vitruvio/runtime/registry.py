"""Talking to an OCI registry, and the four Docker-specific traps that make it work.

The SDK owns the transport: ``OrasRegistryClient`` over ``oras-py``, plus ``LocalLayoutRegistry`` for a filesystem
"registry". Vitruvio adds credentials, the endpoint, a preflight, and error messages -- which is precisely where this
path fails in practice. Every workaround below was learned the hard way, three of them in the SDK's own sandbox.

**1. ``docker.io`` is not the API.** It is the *index* hostname: ``https://docker.io/v2/...`` serves Docker Hub's
website, so a registry client that takes the name literally gets HTTP 200 and a page of HTML where it expected a
manifest. The failure then surfaces as a JSON parse error a long way from its cause. The API lives at
``registry-1.docker.io``, and the ``docker`` CLI performs this substitution for you.

**2. Docker Hub's upload challenge advertises ``pull`` alone.** ORAS asks for exactly the scope the challenge names,
receives a read-only token, retries, and is refused by the same registry -- whose error then lists ``pull`` *and*
``push``. The credentials were never the problem. Fixed in pyboltzmann 0.3.0's ``_authorize_write``, which is one
concrete reason the dependency is pinned at ``>=0.3.0``.

**3. ORAS shells out to the Docker credential helper with no timeout.** Before every request it resolves credentials,
and if ``~/.docker/config.json`` names a ``credsStore`` it runs ``docker-credential-<x>`` through ``subprocess.run``
*without* a timeout. With Docker Desktop on macOS, a helper that blocks blocks the whole process: no output, no error, a
push that never returns. Pre-seeding an empty credential set stops the lookup, because ORAS loads the config once and
only when it holds none. The corollary is worth stating plainly: **a prior ``docker login`` does not authenticate
vitruvio.** Import it deliberately with ``registry login --from-docker``, which runs the helper under *our* timeout.

**4. ORAS narrates to stderr.** It reports a failed credential helper whenever one is configured and unused -- expected
against a local registry -- and prints ``manifest unknown`` for a tag that does not exist yet, which the SDK already
handles. Neither is an error here and both read like one, so its logger is quieted. Nothing is lost: the SDK wraps every
ORAS failure in a ``DistributionError`` carrying the same message.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vitruvio.kernel import TOKEN_URL, CredentialError, Secret, credentials_file, registry_credentials

HUB_INDEX_HOSTS = frozenset({"docker.io", "index.docker.io"})
"""How people write Docker Hub, and what the registry API is not."""

HUB_REGISTRY_HOST = "registry-1.docker.io"
"""Docker Hub's registry API endpoint."""

HELPER_TIMEOUT = 5.0
"""Seconds to wait for a Docker credential helper.

The number that matters. ORAS runs the same helper with no timeout at all, which is how a push comes to hang with no
output; when vitruvio invokes it, it invokes it bounded.
"""

PREFLIGHT_TAG = "vitruvio-preflight"
"""Tag used by ``registry check``. Named so an operator seeing it in a repository knows what left it there."""


def normalize_reference(reference: str) -> tuple[str, str]:
    """
    Split a reference into what to show a user and what to hand a registry client.

    Only Docker Hub needs this, and it needs it badly: a request to ``https://docker.io/v2/...`` lands on the website and
    comes back HTTP 200 with HTML, which resembles no registry error at all.

    Args:
        reference (str): ``<host>/<namespace>/<repo>``, or ``<namespace>/<repo>`` for Docker Hub.

    Returns:
        tuple[str, str]: The reference as configured, and the reference to use against the API.

    Raises:
        CredentialError: If the reference carries a tag or names no repository.
    """
    cleaned = reference.strip().rstrip("/")
    if ":" in cleaned.rsplit("/", 1)[-1]:
        raise CredentialError(
            f"{cleaned!r} carries a tag; a repository and a tag are separate",
            hint="pass the repository, and the tag with --tag",
        )
    if "/" not in cleaned:
        raise CredentialError(
            f"{cleaned!r} names no repository",
            hint="a reference needs at least <namespace>/<repo>, e.g. docker.io/you/my-brain",
        )

    head, _, rest = cleaned.partition("/")
    # A bare `namespace/repo` is Docker Hub's official-image shorthand, and Hub is where it goes.
    if "." not in head and ":" not in head and head != "localhost":
        return cleaned, f"{HUB_REGISTRY_HOST}/{cleaned}"
    if head in HUB_INDEX_HOSTS:
        return cleaned, f"{HUB_REGISTRY_HOST}/{rest}"
    return cleaned, cleaned


def host_of(reference: str) -> str:
    """
    The host a reference authenticates against.

    Args:
        reference (str): A reference, configured or effective.

    Returns:
        str: The registry host, with Docker Hub's index hostname resolved to its API endpoint.
    """
    _, effective = normalize_reference(reference)
    return effective.split("/", 1)[0]


def is_docker_hub(reference: str) -> bool:
    """Whether a reference points at Docker Hub, whose free-tier rate limits are worth a warning."""
    return host_of(reference) == HUB_REGISTRY_HOST


# --- Credentials --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Credential:
    """
    One host's credentials, and where they came from.

    Attributes:
        host (str): The registry host.
        username (str): The account.
        token (Secret): The token, which refuses to print itself.
        source (str): flag, env, keyring, file, or docker. Displayed, and the answer to "why is it authenticating as
            someone else".
    """

    host: str
    username: str
    token: Secret
    source: str

    @property
    def anonymous(self) -> bool:
        """Whether this is really no credential at all."""
        return not (self.username and self.token)


def _keyring() -> Any | None:
    """The keyring module, or ``None`` when the extra is absent."""
    try:
        import keyring
    except ModuleNotFoundError:
        return None
    return keyring


SERVICE = "vitruvio-registry"
"""Keyring service name."""


def store(host: str, username: str, token: str) -> str:
    """
    Save a credential, preferring the system keyring.

    Args:
        host (str): The registry host.
        username (str): The account.
        token (str): The token.

    Returns:
        str: Where it went -- ``keyring`` or the path of the fallback file, so a user knows whether a secret is on disk.
    """
    module = _keyring()
    if module is not None:
        module.set_password(SERVICE, f"{host}\x00{username}", token)
        _remember_username(host, username)
        return "keyring"

    path = credentials_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    held = _read_file(path)
    held[host] = {"username": username, "token": token}
    # Written 0600 *before* the secret goes in, so there is no window where the file exists world-readable.
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(held, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def forget(host: str) -> bool:
    """
    Remove a host's credential from wherever it is held.

    Args:
        host (str): The registry host.

    Returns:
        bool: Whether anything was removed.
    """
    removed = False
    module = _keyring()
    usernames = _read_usernames()
    if module is not None and host in usernames:
        try:
            module.delete_password(SERVICE, f"{host}\x00{usernames[host]}")
            removed = True
        except Exception:
            pass
        usernames.pop(host, None)
        _write_usernames(usernames)

    path = credentials_file()
    held = _read_file(path)
    if host in held:
        held.pop(host)
        path.write_text(json.dumps(held, indent=2, sort_keys=True), encoding="utf-8")
        removed = True
    return removed


def _read_file(path: Path) -> dict[str, dict[str, str]]:
    """The fallback credential file, tolerating absence and damage."""
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _usernames_path() -> Path:
    """Where keyring usernames are noted. A keyring is keyed by account, so the account has to be recorded."""
    return credentials_file().with_name("registry-accounts.json")


def _read_usernames() -> dict[str, str]:
    """Host to account, for the keyring path."""
    path = _usernames_path()
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_usernames(usernames: dict[str, str]) -> None:
    """Record host-to-account, which holds no secret."""
    path = _usernames_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(usernames, indent=2, sort_keys=True), encoding="utf-8")


def _remember_username(host: str, username: str) -> None:
    """Note which account a host's keyring entry belongs to."""
    usernames = _read_usernames()
    usernames[host] = username
    _write_usernames(usernames)


def from_docker(host: str) -> Credential | None:
    """
    Import a credential from ``~/.docker/config.json``.

    This is trap 3 turned into a feature. ORAS would read the same file and run the same helper, but *without a timeout*
    -- so a blocking helper hangs the push with no output. Here the helper runs under :data:`HELPER_TIMEOUT`, once, and
    the result is stored, so nothing shells out again on the hot path.

    Args:
        host (str): The registry host.

    Returns:
        Credential | None: What Docker holds for that host, or ``None``.
    """
    path = Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))) / "config.json"
    if not path.is_file():
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    # Docker files the API host under either name; try both rather than guessing.
    candidates = [host, *(HUB_INDEX_HOSTS if host == HUB_REGISTRY_HOST else ())]
    auths = config.get("auths", {})
    for candidate in candidates:
        entry = auths.get(candidate) or auths.get(f"https://{candidate}/v1/")
        if isinstance(entry, dict) and entry.get("auth"):
            try:
                decoded = base64.b64decode(entry["auth"]).decode("utf-8")
                username, _, token = decoded.partition(":")
            except Exception:
                continue
            if username and token:
                return Credential(host=host, username=username, token=Secret(token, source="docker"), source="docker")

    helper = config.get("credHelpers", {}).get(host) or config.get("credsStore")
    if not helper:
        return None
    for candidate in candidates:
        found = _run_helper(str(helper), candidate)
        if found is not None:
            return Credential(host=host, username=found[0], token=Secret(found[1], source="docker"), source="docker")
    return None


def _run_helper(helper: str, host: str) -> tuple[str, str] | None:
    """
    Ask a Docker credential helper for one host, under a timeout.

    The timeout is the entire point. ORAS runs the same program with none, which is how a push comes to hang silently on
    a machine with Docker Desktop.
    """
    try:
        completed = subprocess.run(
            [f"docker-credential-{helper}", "get"],
            input=f"{host}\n",
            capture_output=True,
            text=True,
            timeout=HELPER_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    username, token = payload.get("Username"), payload.get("Secret")
    if username and token:
        return str(username), str(token)
    return None


def credential_for(
    reference: str,
    *,
    username: str | None = None,
    token: str | None = None,
    anonymous: bool = False,
    allow_docker: bool = False,
) -> Credential:
    """
    Resolve a host's credentials, in precedence order.

    Flags, then the environment, then vitruvio's own store, then -- only when asked -- Docker's config. Docker is opt-in
    because reading it silently would make "which account am I publishing as" depend on a file vitruvio does not own.

    Args:
        reference (str): The repository being addressed.
        username (str | None): From a flag.
        token (str | None): From a flag.
        anonymous (bool): Force no credentials, for a public pull or a local registry.
        allow_docker (bool): Permit importing from Docker's config.

    Returns:
        Credential: What to authenticate with. May be anonymous.
    """
    host = host_of(reference)
    if anonymous:
        return Credential(host=host, username="", token=Secret("", source="anonymous"), source="anonymous")

    if username and token:
        return Credential(host=host, username=username, token=Secret(token, source="flag"), source="flag")

    env_username, env_token = registry_credentials()
    if env_username and env_token:
        return Credential(host=host, username=env_username, token=env_token, source=env_token.source)

    module = _keyring()
    usernames = _read_usernames()
    if module is not None and host in usernames:
        held = module.get_password(SERVICE, f"{host}\x00{usernames[host]}")
        if held:
            return Credential(
                host=host, username=usernames[host], token=Secret(held, source="keyring"), source="keyring"
            )

    stored = _read_file(credentials_file()).get(host)
    if stored and stored.get("username") and stored.get("token"):
        return Credential(
            host=host,
            username=str(stored["username"]),
            token=Secret(str(stored["token"]), source="file"),
            source="file",
        )

    if allow_docker:
        imported = from_docker(host)
        if imported is not None:
            return imported

    return Credential(host=host, username="", token=Secret("", source="none"), source="none")


def account_for(host: str = HUB_REGISTRY_HOST, *, allow_docker: bool = True) -> str | None:
    """
    Which registry account a derived repository should be published under.

    This is what makes "log in once, then publish every brain in the project" work. A project that sets no
    ``[registry].namespace`` derives one from whoever is logged in, so adding a subject to a project is adding a
    directory rather than editing a registry reference.

    Docker's own config is consulted here by default, unlike in :func:`credential_for`. The asymmetry is
    deliberate: reading Docker's *token* silently would make "which account am I publishing as" depend on a file
    vitruvio does not own, while reading the *username* only ever proposes a destination, which is then printed
    before anything is pushed and can be overridden by configuration.

    Args:
        host (str): The registry host to look the account up for.
        allow_docker (bool): Whether ``~/.docker/config.json`` may supply it.

    Returns:
        str | None: The account name, or ``None`` when nothing knows one.
    """
    credential = credential_for(f"{host}/probe/probe", allow_docker=allow_docker)
    return credential.username or None


# --- The client ---------------------------------------------------------------


def quiet_oras() -> None:
    """
    Stop ORAS narrating to stderr.

    It reports a failed credential helper whenever one is configured and unused -- expected against a local registry --
    and prints ``manifest unknown`` for a tag that does not exist yet, which the SDK already handles. Neither is an error
    here and both read like one. Nothing is lost: the SDK wraps every ORAS failure in a ``DistributionError`` carrying
    the same message.
    """
    import logging

    logging.getLogger("oras").setLevel(logging.CRITICAL)
    try:
        import oras.logger

        oras.logger.logger.quiet = True
    except (ImportError, AttributeError):
        pass


def isolate_docker_config(client: Any) -> str | None:
    """
    Keep ORAS away from the Docker credential store.

    Before every request ORAS resolves credentials, and if ``~/.docker/config.json`` names a ``credsStore`` it shells out
    to the helper through ``subprocess.run`` **with no timeout**. On a Mac running Docker Desktop, a helper that blocks
    blocks the whole process: no output, no error, a push that never returns.

    Pre-seeding an empty credential set stops the lookup, because ORAS loads the config only once and only when it holds
    none. It costs nothing here, since vitruvio's credentials are explicit and an explicit ``login`` sets them by a
    different path this does not touch.

    Args:
        client (Any): The ORAS registry client.

    Returns:
        str | None: ``None`` on success, or a warning when a newer ORAS has reorganised its auth backend -- worth saying
        out loud, because without the workaround the symptom is a run that hangs with no output, which is an expensive
        thing to rediscover.
    """
    auth = getattr(getattr(client, "registry", None), "auth", None)
    if auth is None or not hasattr(auth, "_auth_config"):
        return (
            "could not isolate ORAS from the Docker credential store; if a push or pull hangs with no output, that is "
            "why -- see vitruvio.runtime.registry.isolate_docker_config"
        )
    auth._auth_config = {"auths": {}, "credsStore": None, "credHelpers": {}}
    return None


def build_client(reference: str, credential: Credential, *, insecure: bool = False) -> tuple[Any, list[str]]:
    """
    An authenticated registry client, with every workaround applied.

    Args:
        reference (str): The repository, used only to decide the host.
        credential (Credential): What to authenticate with.
        insecure (bool): Allow plain HTTP, for a local registry.

    Returns:
        tuple[Any, list[str]]: The client and any warnings worth surfacing.

    Raises:
        CredentialError: If the SDK's ``[oci]`` extra is absent.
    """
    try:
        from boltzmann.distribution.oras_client import OrasRegistryClient
    except ModuleNotFoundError as error:  # pragma: no cover - a declared dependency of vitruvio-runtime
        raise CredentialError(
            "the registry transport needs the SDK's [oci] extra", hint="pip install 'pyboltzmann[oci]'"
        ) from error

    quiet_oras()
    client = OrasRegistryClient(insecure=insecure)
    warnings: list[str] = []
    if warning := isolate_docker_config(client):
        warnings.append(warning)

    if not credential.anonymous:
        try:
            client.login(credential.username, credential.token.reveal())
        except Exception as error:
            raise CredentialError(
                f"{credential.host} refused the credentials for {credential.username}: {error}",
                hint=(
                    f"for Docker Hub, create a Personal Access Token with Read & Write scope at {TOKEN_URL} -- "
                    f"the account password will not do"
                ),
            ) from error
    elif is_docker_hub(reference):
        warnings.append("publishing to Docker Hub anonymously will be refused; its free tier also rate-limits pulls")

    return client, warnings
