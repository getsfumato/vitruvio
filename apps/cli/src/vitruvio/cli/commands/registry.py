"""``vitruvio registry`` -- credentials, and checking a registry before trusting it.

Two things worth knowing before using any of this.

**A prior ``docker login`` does not authenticate vitruvio.** ORAS would read Docker's config, but it shells out to the
credential helper with no timeout, and a helper that blocks blocks the whole push -- silently, with no output. So
vitruvio isolates itself from that store and asks you to import deliberately: `registry login --from-docker` runs the
helper under a timeout of its own, once.

**Run ``registry check`` before a first push.** A brain's manifest carries a custom ``config.mediaType``, and registries
have historically disagreed about whether that is allowed. Checking takes one tiny artifact; discovering it during a real
push takes a confusing error.
"""

from __future__ import annotations

import sys
from typing import Annotated

from cyclopts import App, Parameter

from vitruvio.cli.context import current
from vitruvio.kernel import ExitCode, VitruvioError

app = App(
    name="registry",
    help="Manage registry credentials, and check a registry before publishing.",
    result_action="return_value",
    exit_on_error=False,
)


def _read_token(token_stdin: bool, prompt: bool = True) -> str:
    """
    Read a token from stdin or an unechoed prompt.

    Never from argv: a token on a command line ends up in the shell history, in the process list, and in whatever CI log
    captured the invocation.
    """
    if token_stdin:
        return sys.stdin.read().strip()
    if not prompt:
        return ""
    from getpass import getpass

    return getpass("token (not echoed): ").strip()


@app.command(name="login")
def login(
    host: str,
    *,
    username: Annotated[str | None, Parameter(name=["--username", "-u"])] = None,
    token_stdin: Annotated[bool, Parameter(name=["--token-stdin"])] = False,
    from_docker: Annotated[bool, Parameter(name=["--from-docker"])] = False,
) -> ExitCode:
    """Store credentials for a registry host.

    The token is read from stdin or an unechoed prompt, never from a flag: a token on a command line lands in the shell
    history and the process list.

    Validated against `/v2/` before being stored. A login that "succeeds" and fails at the first push is worse than one
    that fails now.

    Parameters
    ----------
    host
        The registry host. `docker.io` is accepted and resolved to `registry-1.docker.io`, which is where the API lives.
    username
        The account. Required unless importing from Docker.
    token_stdin
        Read the token from stdin, for scripts and CI.
    from_docker
        Import from `~/.docker/config.json`, invoking any credential helper under vitruvio's own timeout rather than
        ORAS's absent one.
    """
    from vitruvio.runtime.registry import (
        Credential,
        build_client,
        host_of,
        store,
    )
    from vitruvio.runtime.registry import (
        from_docker as import_docker,
    )

    console = current().console
    # Normalised through a repository-shaped reference, since that is what the resolver takes -- and it is where the
    # docker.io/registry-1.docker.io substitution happens.
    resolved = host_of(f"{host}/probe/probe" if "/" not in host else host)

    if from_docker:
        imported = import_docker(resolved)
        if imported is None:
            raise VitruvioError(
                f"~/.docker/config.json holds no usable credential for {resolved}",
                hint="run `docker login` first, or pass --username with --token-stdin",
            )
        credential = imported
    else:
        if not username:
            raise VitruvioError("--username is required", hint="or pass --from-docker to import an existing one")
        token = _read_token(token_stdin)
        if not token:
            raise VitruvioError("no token was given", hint="pipe it with --token-stdin, or type it at the prompt")
        from vitruvio.kernel import Secret

        credential = Credential(host=resolved, username=username, token=Secret(token, source="flag"), source="flag")

    # Validated before storing. Raises with the Docker Hub token guidance if the registry refuses.
    build_client(f"{resolved}/probe/probe", credential)
    where = store(resolved, credential.username, credential.token.reveal())

    if where != "keyring":
        console.warn(f"no system keyring is available, so the token is stored in plain text at {where} (mode 0600)")
    return console.emit(
        "registry.login",
        {"host": resolved, "username": credential.username, "stored": where, "source": credential.source},
        lines=[f"logged in to {resolved} as {credential.username} (stored in {where})"],
    )


@app.command(name="logout")
def logout(host: str) -> ExitCode:
    """Remove a host's stored credentials.

    Parameters
    ----------
    host
        The registry host.
    """
    from vitruvio.runtime.registry import forget, host_of

    console = current().console
    resolved = host_of(f"{host}/probe/probe" if "/" not in host else host)
    removed = forget(resolved)
    if not removed:
        console.warn(f"nothing was stored for {resolved}")
    return console.emit("registry.logout", {"host": resolved, "removed": removed}, lines=[f"logged out of {resolved}"])


@app.command(name="whoami")
def whoami(host: str | None = None) -> ExitCode:
    """Report which credentials would be used, and where they came from.

    The source is the useful part: it answers "why is it publishing as someone else" without guessing. Note that
    `docker` never appears unless you imported deliberately -- a prior `docker login` does not authenticate vitruvio.

    Parameters
    ----------
    host
        Which host to report on. Defaults to the configured registry.
    """
    from vitruvio.runtime.registry import credential_for, host_of, is_docker_hub

    console = current().console
    context = current()
    configured = context.resolve().project.registry.reference
    reference = host if host and "/" in host else f"{host or 'docker.io'}/probe/probe"
    if host is None and configured:
        reference = configured

    credential = credential_for(reference)
    payload = {
        "host": host_of(reference),
        "username": credential.username or None,
        "source": credential.source,
        "token": credential.token.masked() if credential.token else None,
        "can_write": not credential.anonymous,
        "docker_hub": is_docker_hub(reference),
    }
    lines = [
        f"host      {payload['host']}",
        f"username  {payload['username'] or '(none -- anonymous)'}",
        f"token     {payload['token'] or '(none)'}",
        f"source    {payload['source']}",
    ]
    if credential.anonymous:
        lines.append("")
        lines.append(
            "a prior `docker login` does not authenticate vitruvio: run `vitruvio registry login --from-docker`"
        )
    return console.emit("registry.whoami", payload, lines=lines)


@app.command(name="list")
def list_() -> ExitCode:
    """List the hosts vitruvio holds credentials for."""
    from vitruvio.kernel import credentials_file
    from vitruvio.runtime.registry import _read_file, _read_usernames

    console = current().console
    hosts = sorted(set(_read_usernames()) | set(_read_file(credentials_file())))
    rows = [{"host": host, "username": _read_usernames().get(host)} for host in hosts]
    lines = [f"{row['host']:<32} {row['username'] or ''}" for row in rows]
    if not rows:
        console.warn("no credentials stored; run `vitruvio registry login <host>`")
    return console.emit("registry.list", {"hosts": rows}, lines=lines)


@app.command(name="check")
def check(
    reference: str | None = None,
    *,
    username: Annotated[str | None, Parameter(name=["--username", "-u"])] = None,
    token_stdin: Annotated[bool, Parameter(name=["--token-stdin"])] = False,
    anonymous: bool = False,
    insecure: bool = False,
) -> ExitCode:
    """Push a probe artifact shaped exactly like a brain, and report what the registry accepted.

    Four questions answered by doing rather than guessing: is the endpoint reachable, do the credentials carry write
    scope, is a custom `config.mediaType` accepted, and does the `artifactType` survive a round trip.

    The probe deliberately uses the brain's real media types. A probe with a conventional config media type would only
    prove the registry accepts container images, which was never in doubt.

    Parameters
    ----------
    reference
        The repository to probe. Defaults to the configured one.
    username
        Account, when not using stored credentials.
    token_stdin
        Read the token from stdin.
    anonymous
        Probe without credentials, for a local registry.
    insecure
        Allow plain HTTP.
    """
    console = current().console
    token = _read_token(token_stdin, prompt=False) if token_stdin else None

    result = (
        current()
        .service()
        .registry_check(reference, username=username, token=token, anonymous=anonymous, insecure=insecure)
    )
    for warning in result.get("warnings", []):
        console.warn(warning)

    lines = [
        f"{'ok ' if check['ok'] else 'FAIL'}  {check['check']:<20} {check['detail']}" for check in result["checks"]
    ]
    if not result["ok"]:
        lines += ["", f"hint: {result['hint']}"]
        raise VitruvioError(f"{result['reference']} cannot hold a Boltzmann brain as published", hint=result["hint"])
    return console.emit("registry.check", result, lines=lines)
