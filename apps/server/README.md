# Vitruvio Server

The Vitruvio runtime over HTTP: a **query** API that reads any published brain, an **ingest** API that accepts jobs
against one, and a **worker** that runs them. Every request names an OCI repository and a tag. No brain or job state
lives on an instance: query keeps a disposable local cache of installed layouts, the worker installs into scratch
space, and Redis holds the ingest jobs and their results. One image, one command, three roles.

The user documentation is at [vitruvio.sh/docs/server](https://vitruvio.sh/docs/server); this file is the operator's
reference.

## Run it

One container, the query API only:

```sh
docker run --rm -p 8080:8080 -e VITRUVIO_SERVER_ALLOWED_REGISTRIES=ghcr.io ghcr.io/getsfumato/vitruvio-server
curl -s localhost:8080/health/ready
```

Everything — query, ingest, worker and the Redis they share — from a checkout:

```sh
docker compose -f apps/server/compose.yaml up
```

That publishes query on `8080` and ingest on `8081`. Registry credentials come from the shell or from a `.env`
beside the compose file (git-ignored); `VITRUVIO_VERSION=<version>` pins the image tag instead of `latest`.

From a checkout, without Docker:

```sh
uv sync --all-packages
uv run vitruvio-server query                 # 127.0.0.1:8080
uv run vitruvio-server ingest --port 8081    # needs a Redis on VITRUVIO_SERVER_REDIS_URL
uv run vitruvio-server worker
```

## One image, three roles

`ghcr.io/getsfumato/vitruvio-server` has `vitruvio-server` as its entrypoint and `query` as its default argument:

```sh
docker run --rm -p 8080:8080 ghcr.io/getsfumato/vitruvio-server            # query
docker run --rm -p 8081:8080 ghcr.io/getsfumato/vitruvio-server ingest     # ingest API
docker run --rm --init       ghcr.io/getsfumato/vitruvio-server worker     # ingest worker
docker run --rm              ghcr.io/getsfumato/vitruvio-server --help
```

`--init` for the worker: it installs no signal handler and runs as PID 1, so without an init process `docker stop`
waits out the grace period and kills it. Harmless — an abandoned job is reclaimed through its lease — but slow.
`compose.yaml` sets `init: true` for the same reason.

Every release publishes the image under its version and `latest`, for `linux/amd64` and `linux/arm64`. The
`vitruvio-server` distribution inside carries the same version as the CLI release it was cut with; `install.sh`
never installs it, because the CLI does not depend on it. A deployment pins the version; `latest` is for trying it.
To build locally, from the repository root:

```sh
docker build -f apps/server/Dockerfile -t ghcr.io/getsfumato/vitruvio-server:latest .
```

Anything that is not a role goes through the entrypoint's argument parser, so to run something else inside the
image pass `--entrypoint`:

```sh
docker run --rm --entrypoint python ghcr.io/getsfumato/vitruvio-server -c 'import vitruvio.kernel; print(vitruvio.kernel.__version__)'
```

## Configuration

Everything is read from the environment. `query` and `ingest` also take `--host` and `--port`.

| variable | default | does |
|---|---|---|
| `VITRUVIO_SERVER_HOST` | `127.0.0.1`; `0.0.0.0` in the image | interface query and ingest bind |
| `VITRUVIO_SERVER_PORT` | `8080` | port they bind |
| `VITRUVIO_SERVER_ALLOWED_REGISTRIES` | unset: **every remote registry is refused** | comma-separated hosts a request may name in `brain.repository`; `docker.io` covers Docker Hub |
| `VITRUVIO_SERVER_ALLOWED_EMBEDDING_URLS` | unset | endpoints a request may name in `config.embedding.<kind>.base_url` |
| `VITRUVIO_SERVER_REDIS_URL` | `redis://localhost:6379/0` | the Redis the ingest API and worker share; query ignores it |
| `VITRUVIO_SERVER_MAX_JOB_BYTES` | 8 MiB | ceiling on a job body; over it is 413 |
| `VITRUVIO_SERVER_MAX_DOCUMENT_BYTES` | 50 MiB | ceiling on one document download; a lower `config.ingest.max_bytes` also applies |
| `VITRUVIO_SERVER_S3_ENDPOINT` | AWS | an S3-compatible endpoint for `s3://` documents |
| `VITRUVIO_SERVER_LOCAL_REGISTRY` | unset | a filesystem registry of OCI layouts for development and tests; addressed by path, no credentials, exempt from the allowlist |
| `VITRUVIO_SERVER_INSECURE` | false | `true` allows a plain-HTTP registry |

Do not set the CLI's selection variables — `VITRUVIO_ACTOR_ID`, `VITRUVIO_BRAIN`, `VITRUVIO_CONFIG`,
`VITRUVIO_PROJECT` — in the server's environment. The request supplies the brain and the actor; an override is
refused (`ACTOR_OVERRIDE_REFUSED`) and every request fails.

Credentials are the ones vitruvio reads everywhere: `VITRUVIO_REGISTRY_USERNAME` / `VITRUVIO_REGISTRY_TOKEN` (or
`DOCKER_USERNAME` / `DOCKER_TOKEN`) for the registry; the provider's key (`OPENAI_API_KEY`, `VOYAGE_API_KEY`,
`COHERE_API_KEY`, …) for API embedders; the standard AWS variables for `s3://`. The worker reads `s3://` URIs with
that AWS identity and `https://` URIs as signed URLs. For governed brains, mount an SSH agent socket into the
worker and set `SSH_AUTH_SOCK`; the request names the authorized fingerprints to sign with.

### Where credentials may go

The request chooses the registry and may choose an embedding endpoint, and the environment holds the credentials
for both. The server therefore only presents credentials to destinations the operator listed:

- `VITRUVIO_SERVER_ALLOWED_REGISTRIES` — comma-separated registry hosts (`ghcr.io,registry.example:5000`;
  `docker.io` covers Docker Hub). A `brain.repository` on any other host is refused with `DESTINATION_REFUSED`
  (403) before a registry client is built. Unset, every remote registry is refused.
- `VITRUVIO_SERVER_ALLOWED_EMBEDDING_URLS` — comma-separated endpoints a request may name in
  `config.embedding.<kind>.base_url`, compared exactly after trimming a trailing slash. A request that names no
  `base_url` uses the provider's own endpoint, which needs no listing. Any other endpoint is refused the same way.

**The server has no HTTP authentication of its own.** Deploy it on a private network or behind a gateway that
authenticates.

## API

All brain requests take `brain: {"repository": "registry.example/team/brain", "tag": "v1"}` and a portable
`config` object. The config may contain `actor`, `assisted_by`, `authenticity`, `policy`, `embedding`, `index`,
`planner`, and `ingest`. Server-local paths, source commands and credentials are excluded. The adapter validates
it through Vitruvio's project schema and writes a temporary `vitruvio.toml` so provenance uses the declared actor.
A `null` field means what an absent one means; a field the schema does not know is a `CONFIG_INVALID` (400).
Responses are the CLI's `--json` envelope. Both listening roles serve `/docs` and `/openapi.json`.

The query role exposes `POST /v1/search`, `/v1/explain`, `/v1/brain/state`, and `/v1/blocks/resolve`.
Search accepts the runtime's text, filters, mode, limit and expansion depth. Responses include the runtime's
evidence, snapshot and manifest digests, authenticity and warnings. Each request resolves its tag; a changed
manifest gets a fresh cache entry, and an install whose downloaded manifest is not the one resolved is refused
with `OCI_TAG_MOVED` (409) rather than served under the wrong digest. A governed head is also checked for
signatures published after it was cached -- they are referrers, so they do not move the manifest digest -- and
reinstalled when they changed. A governed unsigned head is served with a warning; an unauthorized head is refused
by the runtime's pull gate. Registry I/O runs off the event loop, and one brain's installation does not hold up
requests for brains already cached.

The ingest role exposes `POST /v1/ingestions` and `GET /v1/ingestions/{job_id}`. A job body adds explicit
`assisted_by` selection, optional `sign_with` fingerprints, and up to 100 mixed items:

```json
{
  "brain": {"repository": "registry.example/team/brain", "tag": "v1"},
  "config": {"actor": {"id": "alex@example.org"}},
  "assisted_by": [],
  "sign_with": [],
  "items": [
    {"kind": "document", "uri": "https://example.org/signed/document.md", "origin": "https://example.org/document.md", "media_type": "text/markdown", "proposer": "structure"}
  ]
}
```

To submit candidates, add an item with `kind: "candidates"`, the task returned by `task define`, and a matching
`boltzmann.candidates/v1` document under `candidates`. Each item gets a verdict.
The job continues after an item failure and publishes a new `ingest-<job_id>` tag when the brain changed. A
governed output is signed and checked before publication. The job reports `partial` when some items failed.
`ingest_run` registers canonical evidence before validating derived candidates; a failed document item may
therefore report `changed: true` and that evidence may be present in the published brain.

Both listening roles expose `/health/live` and `/health/ready`; ingest readiness checks Redis. Job results expire
after seven days.

### How a job survives its worker

Each attempt at a job holds a lease in Redis (`vitruvio:ingest:lease:<job_id>`, 120 s, renewed every 10 s by
the worker's heartbeat). Every job-state write, the publication and the acknowledgement are conditioned on that
lease in the same Redis transaction, so an attempt whose heartbeats stopped -- paused, partitioned, or dead -- can
neither publish nor overwrite the work of the attempt that replaced it. A delivery idle for longer than the lease
is claimed by another worker; the scan carries Redis's `XAUTOCLAIM` cursor so an abandoned delivery behind busy
ones is still found.

The request body stays in the job hash until a terminal state is recorded, so an attempt interrupted at any point
can be resumed. Before pushing, a job records the exact manifest digest it is about to publish. A later attempt --
or the same attempt, after a push that raised once the registry had already taken the artifact -- resolves the
output tag, requires it to name that digest, installs and verifies it, and for a governed brain requires the head
to be authorized: the SDK uploads the manifest before its signature referrers, so the worker may have died between
the two. In that case the recovering worker signs the installed head with the request's `sign_with` keys, pushes
again, and verifies from the registry before reporting the job `completed`. If the registry cannot be reached to
decide, the job is left `running` with its state intact and the delivery pending, to be checked again rather than
reported `failed`.
