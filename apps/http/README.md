# Vitruvio HTTP services

This workspace member builds two independent HTTP services from the same Vitruvio runtime. Both accept an OCI
repository and tag on each request. No brain or job state is kept on a service instance: query layouts are a
disposable local cache, ingest layouts are job scratch space, and Redis holds ingest jobs and results.

## Run

From the repository root:

```sh
uv sync --all-packages
uv run uvicorn vitruvio.http.query:app --port 8080
uv run uvicorn vitruvio.http.ingest:app --port 8081
uv run vitruvio-ingest-worker
```

The ingest API and worker require `VITRUVIO_HTTP_REDIS_URL` (default `redis://localhost:6379/0`). Registry
credentials follow Vitruvio's existing environment conventions. The worker reads `s3://` URIs using its AWS
identity or `https://` signed URLs. `VITRUVIO_HTTP_S3_ENDPOINT` selects an S3 compatible endpoint. Document
downloads have a deployment ceiling of 50 MiB by default, adjustable with
`VITRUVIO_HTTP_MAX_DOCUMENT_BYTES`; a lower `config.ingest.max_bytes` also applies. For governed brains, mount an
SSH agent socket into the worker and set `SSH_AUTH_SOCK`; the request names the authorized fingerprints to use.
`VITRUVIO_HTTP_MAX_JOB_BYTES` limits the JSON job body to 8 MiB by default.

### Where credentials may go

The request chooses the registry and may choose an embedding endpoint, and the environment holds the credentials
for both. The services therefore only present credentials to destinations the operator listed:

- `VITRUVIO_HTTP_ALLOWED_REGISTRIES` — comma-separated registry hosts (`ghcr.io,registry.example:5000`;
  `docker.io` covers Docker Hub). A `brain.repository` on any other host is refused with `DESTINATION_REFUSED`
  (403) before a registry client is built. Unset, every remote registry is refused.
- `VITRUVIO_HTTP_ALLOWED_EMBEDDING_URLS` — comma-separated endpoints a request may name in
  `config.embedding.<kind>.base_url`, compared exactly after trimming a trailing slash. A request that names no
  `base_url` uses the provider's own endpoint, which needs no listing. Any other endpoint is refused the same way.

Every release publishes both services as images, under the release's version and `latest`, for `linux/amd64`
and `linux/arm64`:

```sh
docker run --rm -p 8080:8080 -e VITRUVIO_HTTP_ALLOWED_REGISTRIES=ghcr.io ghcr.io/getsfumato/vitruvio-query:0.31.0
docker run --rm -p 8081:8080 -e VITRUVIO_HTTP_REDIS_URL=redis://redis:6379/0 ghcr.io/getsfumato/vitruvio-ingest:0.31.0
docker run --rm -e VITRUVIO_HTTP_REDIS_URL=redis://redis:6379/0 ghcr.io/getsfumato/vitruvio-ingest:0.31.0 vitruvio-ingest-worker
```

The ingest API and its worker are one image with two commands. The image carries the same `vitruvio-http`
version as the release it was cut with; the CLI's installer never installs that distribution, because the CLI
does not depend on it. To build locally instead, `docker build -f apps/http/Dockerfile --target query .` or
`--target ingest`. The services intentionally have no HTTP authentication; deploy them behind a private network
or gateway.

`VITRUVIO_HTTP_LOCAL_REGISTRY` can point at a local OCI layout registry for development and tests; it is addressed
by path, carries no credentials, and is exempt from the registry allowlist. `VITRUVIO_HTTP_INSECURE=true` allows an
HTTP registry.

## API

All brain requests take `brain: {"repository": "registry.example/team/brain", "tag": "v1"}` and a portable
`config` object. The config may contain `actor`, `assisted_by`, `authenticity`, `policy`, `embedding`, `index`,
`planner`, and `ingest`. Server-local paths, source commands and credentials are excluded. The adapter validates
it through Vitruvio's project schema and writes a temporary `vitruvio.toml` so provenance uses the declared actor.
A `null` field means what an absent one means; a field the schema does not know is a `CONFIG_INVALID` (400).

The query service exposes `POST /v1/search`, `/v1/explain`, `/v1/brain/state`, and `/v1/blocks/resolve`.
Search accepts the runtime's text, filters, mode, limit and expansion depth. Responses include the runtime's
evidence, snapshot and manifest digests, authenticity and warnings. Each request resolves its tag; a changed
manifest gets a fresh cache entry, and an install whose downloaded manifest is not the one resolved is refused
with `OCI_TAG_MOVED` (409) rather than served under the wrong digest. A governed head is also checked for
signatures published after it was cached -- they are referrers, so they do not move the manifest digest -- and
reinstalled when they changed. A governed unsigned head is served with a warning; an unauthorized head is refused
by the runtime's pull gate. Registry I/O runs off the event loop, and one brain's installation does not hold up
requests for brains already cached.

The ingest service exposes `POST /v1/ingestions` and `GET /v1/ingestions/{job_id}`. A job body adds explicit
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

Both services expose `/health/live` and `/health/ready`; ingest readiness checks Redis. Job results expire
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
