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

Build the independently deployable images with `docker build -f apps/http/Dockerfile --target query .` and
`docker build -f apps/http/Dockerfile --target ingest .`. Run the ingest worker from the ingest image with
`vitruvio-ingest-worker` as its command. The services intentionally have no HTTP authentication; deploy them
behind a private network or gateway.

`VITRUVIO_HTTP_LOCAL_REGISTRY` can point at a local OCI layout registry for development and tests.
`VITRUVIO_HTTP_INSECURE=true` allows an HTTP registry.

## API

All brain requests take `brain: {"repository": "registry.example/team/brain", "tag": "v1"}` and a portable
`config` object. The config may contain `actor`, `assisted_by`, `authenticity`, `policy`, `embedding`, `index`,
`planner`, and `ingest`. Server-local paths, source commands and credentials are excluded. The adapter validates
it through Vitruvio's project schema and writes a temporary `vitruvio.toml` so provenance uses the declared actor.

The query service exposes `POST /v1/search`, `/v1/explain`, `/v1/brain/state`, and `/v1/blocks/resolve`.
Search accepts the runtime's text, filters, mode, limit and expansion depth. Responses include the runtime's
evidence, snapshot and manifest digests, authenticity and warnings. Each request resolves its tag; a changed
manifest gets a fresh cache entry. A governed unsigned head is served with a warning; an unauthorized head is
refused by the runtime's pull gate.

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
after seven days. For a job interrupted during publication, the worker compares the prepared manifest digest with
the output tag before retrying.
