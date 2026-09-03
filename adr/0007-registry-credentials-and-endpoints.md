# ADR-0007: Registry Credentials, Endpoints And The Preflight

**Decision status:** Accepted, implemented in M8.

## Context

A brain is distributed as an OCI Artifact, and the SDK already carries the transport (`OrasRegistryClient` over
`oras-py`, plus `LocalLayoutRegistry` for a filesystem "registry"). Vitruvio adds none of that. What it adds is the
part that decides whether a transfer will *work*, because publishing a brain to a container registry fails in five
specific ways that have nothing to do with the protocol and everything to do with Docker.

Each was hit for real before this was written, which is why each one has code rather than a docs note.

## Decision

### 1. `docker.io` is an index hostname, not an API endpoint

`https://docker.io/v2/…` returns the Docker Hub web page with HTTP 200 and HTML, so the failure surfaces as a JSON
parse error a long way from its cause. The API lives at `registry-1.docker.io`. The `docker` CLI performs that
substitution for you; a registry client does not.

`vitruvio.runtime.registry.normalize_reference()` rewrites `docker.io/*` and `index.docker.io/*`, and returns **both**
forms: the configured one, which is what the user typed and what gets shown back, and the effective one, which is what
the client is handed. Every command that touches a registry prints the effective endpoint, so a substitution is never
invisible.

### 2. A prior `docker login` does not authenticate vitruvio

ORAS re-reads `~/.docker/config.json` before each request and, when a `credsStore` is configured, runs the credential
helper via `subprocess.run` **with no timeout**. With Docker Desktop on macOS a helper that blocks hangs the whole
push: no output, no error, a command that never returns. That is the most expensive failure mode in this area,
because there is nothing on screen to search for.

So vitruvio **isolates itself from that store** — `isolate_docker_config()` pre-seeds
`auth._auth_config = {"auths": {}, "credsStore": None, "credHelpers": {}}` — and keeps its own credential store, with
this precedence:

1. `--username` / `--token-stdin`
2. `VITRUVIO_REGISTRY_USERNAME` / `VITRUVIO_REGISTRY_TOKEN`, then `DOCKER_USERNAME` / `DOCKER_TOKEN`
3. vitruvio's own store, per host: system keyring when available, else
   `$XDG_CONFIG_HOME/vitruvio/credentials.json` at mode `0600`, with an explicit warning that the token is on disk
4. anonymous

Reusing a Docker login is then an explicit act: `registry login --from-docker` reads `auths[host].auth` and, when a
helper is configured, invokes `docker-credential-<x> get` **under vitruvio's own five-second timeout**, once, rather
than letting ORAS do it unbounded on every request. The trap becomes a feature.

Tokens are never read from argv — a token on a command line is in the shell history, the process list and the CI log.
Never written to `vitruvio.toml`, which is committed. Always redacted in output (`dckr_pat_…9f2a`), by a `Secret` type
that redacts under `str`, `repr` and `format` so a stray f-string cannot leak one.

`registry whoami` reports where the credential came from, because "why is it publishing as someone else" is a real
question with a factual answer, and it says out loud that a `docker login` is not enough.

### 3. Docker Hub's write challenge announces only `pull`

ORAS asks for exactly the scope the challenge names, gets a read token, retries, and is refused by an error naming
both `pull` and `push`. The credentials were never the problem. Fixed upstream in pyboltzmann 0.3.0
(`_authorize_write` requests `pull,push` up front), which is a concrete reason for the `>=0.3.0` pin.

### 4. A custom `config.mediaType` is verified, never assumed

A brain's manifest carries `artifactType = application/vnd.gaussia.boltzmann.brain.v1+json` and
`config.mediaType = application/vnd.gaussia.boltzmann.snapshot.v1+json`. Docker Hub's documentation used to state that
it accepted no config media type other than `application/vnd.oci.image.config.v1+json`; the current documentation no
longer publishes that restriction and Docker now promotes OCI artifacts for AI models, so support appeared to have
broadened. That was a reading of documentation, not a verified fact.

**It is now a verified fact.** Run against Docker Hub on 2026-08-07:

```
ok   reference            docker.io/<account>/vitruvio-preflight resolves to registry-1.docker.io/...
ok   write                accepted, filed under sha256:e5781a2d85421...
ok   config_media_type    application/vnd.gaussia.boltzmann.snapshot.v1+json accepted
ok   artifact_type        application/vnd.gaussia.boltzmann.brain.v1+json preserved
```

Docker Hub accepts the protocol's config media type and preserves the artifact type through a round trip, and a full
brain published there pulls back into a fresh install, verifies against its Merkle roots, and is searchable. The
preflight stays: what was verified is one registry on one day, and the next registry is the one that refuses.

`registry check` therefore pushes a probe artifact with **exactly** the manifest shape a brain uses — same
`artifactType`, same `config.mediaType`, one 24-byte layer — under the tag `vitruvio-preflight`, and reports four
things: `/v2/` reachable, credentials carrying write scope, custom config media type accepted, and `artifactType`
preserved through a round trip. A probe with a conventional config media type would only prove the registry accepts
container images, which was never in doubt.

If a registry refuses, the error names the real alternatives — `ghcr.io`, or a self-hosted `registry:2` — and **not** a
compatibility mode. Changing the media type would change the artifact's identity: it would no longer be a Boltzmann
brain. That is a protocol decision, not a runtime one, and it goes upstream if anyone wants it.

### 5. ORAS narrates to stderr

It reports a failed credential helper when one is configured and unused (expected against an anonymous local
registry) and prints `manifest unknown` for a tag that does not exist yet (which the SDK handles). Neither is an error
here and both read like one, so the `oras` logger is silenced to CRITICAL. Nothing is lost: the SDK wraps every real
failure in a `DistributionError` carrying the same message, attributed and in context.

## Consequences

- **A local layout registry is the default test surface.** `dist push --local PATH` uses `LocalLayoutRegistry`: no
  network, no credentials, no rate limits, and the same code path as a remote push. The full round trip —
  pack → push → plan-pull → pull → verify → search — is exercised there on every run.
- **The HTTP path is tested against an ephemeral `registry:2`**, marked `slow` and skipped when no daemon is
  reachable. That check tests for a non-empty `ServerVersion` rather than a zero exit status, because `docker info`
  exits 0 with the daemon stopped — and trusting the exit status is how these tests came to be attempted against a
  dead daemon, where `docker run` hangs instead of failing. Every docker call in the fixture is bounded by a timeout
  for the same reason.
- **Docker Hub and ghcr.io run in a manual `workflow_dispatch` job** (`.github/workflows/registry.yml`), never per
  PR: publishing on every push pollutes a public registry with a test artifact per commit and burns rate limit. It
  takes the host and repository as inputs and needs `REGISTRY_USERNAME` / `REGISTRY_TOKEN` in the repository's
  secrets.
- **A test asserts `auth._auth_config` still exists** in the pinned `oras`. It is a private attribute and this is
  deliberately fragile — but the alternative failure mode is a push that hangs with no output, so the fragility is
  arranged to break a build rather than someone's afternoon.
- `[registry]` in `vitruvio.toml` holds `reference`, default `tag` and `insecure` — never a credential — so
  `vitruvio dist push` with no arguments publishes to the destination the repository declares.
