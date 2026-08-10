---
name: vitruvio-dist
description: Publish a Boltzmann brain to a registry, or install one from a registry. Use when asked to push, pull, publish, share, or install a brain, when a push was refused, or when registry credentials need setting up.
allowed-tools: Bash(vitruvio:*), Read
---

# Publishing and installing a brain

A published brain is an OCI Artifact: a small manifest, one layer per memory module, and a config blob that is the
snapshot document. Because the modules are separate blobs, a consumer can install one module and later update only
what changed.

## Always plan-pull before you pull

```bash
vitruvio dist plan-pull <REF> --tag v1 --json
```

A canonical layer can be gigabytes. "How much is this going to cost" should be answerable without paying it, and
`fetch_bytes` answers it. `is_noop: true` means the brain is already at the published state.

```bash
vitruvio dist pull <REF> --tag v1 --json
vitruvio brain verify --json          # do this after every pull
```

A **selective** pull (`--module semantic`) is a legitimate, permanent state. The modules you did not take are
*missing*, not broken, and `inspect resolvability` reports them as such — do not treat that report as corruption.

## Publishing

```bash
vitruvio dist pack --tag v1 --json                 # build it locally, push nothing
vitruvio dist push <REF> --tag v1 --json
```

### A project publishes several brains at once

When the repository holds a *project* — several named brains under one `vitruvio.toml` — you usually pass no
reference at all. Each brain derives `<namespace>/<project>-<brain>`:

```bash
vitruvio project show --json                       # read `repository` before pushing: it is the destination
vitruvio dist push --all --tag v1 --json
```

`--all` **skips** a brain with nothing committed rather than failing it — a subject nobody has started yet is the
ordinary state of a project. Read `skipped` in the payload before concluding something went wrong. A brain that
fails for a real reason does not stop the others, and the command exits non-zero at the end.

`--all` refuses a reference, because a reference names one repository and this publishes several. It also **skips a
brain declaring `publish = false`**, which is how a project marks somebody else's upstream.

### Never publish a brain the project marked unpublishable

`vitruvio --brain X dist push` exits **6** with code `PUBLISH_FORBIDDEN` when brain X declares `publish = false`.
That is a declaration, not a malfunction: the brain was installed from someone else, and pushing it would publish a
fork under this project's repository while the real one moves on. Do not work around it by passing an explicit
reference, and do not edit `vitruvio.toml` to flip the flag. Report the refusal and stop; flipping it is the user's
decision to state, not yours to infer from being asked to publish.

The prohibition is on publishing only. `source pull`, `ingest run` and `index build` on that brain are all fine.

`pack` first, and **read the warnings**. The one that matters: *"the vector index will not be published"*. A brain
published without its vector layer is a brain nobody else can search semantically, because the vector index is the
one index a consumer cannot rebuild. The usual cause is a stale index — `vitruvio index build` and pack again.

Two refusals come from the protocol and both are correct:

- **Exit 8 — not a fast-forward.** Someone pushed since this brain was pulled, and the histories diverged. Pull,
  re-commit, push again. **Never `--force`**: it discards their version, and there is no undo.
- **Narrowing refused.** Publishing fewer modules than the last version would make a consumer's selective update
  silently lose one.

## Credentials

**A prior `docker login` does not authenticate vitruvio.** This is deliberate, not an oversight: the ORAS client
shells out to Docker's credential helper with no timeout, and a helper that blocks hangs the whole push with no
output at all. vitruvio isolates itself from that store and asks you to import once, under its own timeout:

```bash
vitruvio registry login docker.io --from-docker --json
vitruvio registry login docker.io --username <U> --token-stdin < pat.txt
vitruvio registry whoami docker.io --json
```

Docker Hub needs a **Personal Access Token with Read & Write** scope; the account password does not work. The token
is read from stdin or an unechoed prompt, never from a flag — a token on a command line lands in the shell history
and the process list. Never put one in `vitruvio.toml`, which is committed.

`whoami` reports where the credential came from, which is how you answer "why is it publishing as someone else".

## Check a registry before the first push

```bash
vitruvio registry check <REF> --json
```

A brain's manifest carries a custom `artifactType` and a custom `config.mediaType`, and registries have
historically disagreed about whether that is allowed. This pushes a probe artifact with exactly that manifest shape
and reports four things: `/v2/` reachable, credentials carry write scope, the custom config media type was
accepted, and the `artifactType` survived a round trip.

If a registry refuses, the alternatives are a registry that accepts it (`ghcr.io` does) or a local one:

```bash
docker run -d -p 5000:5000 --name registry registry:2
vitruvio registry check localhost:5000/you/brain --insecure --anonymous --json
vitruvio dist push localhost:5000/you/brain --tag v1 --insecure --anonymous --json
```

There is no compatibility mode, and asking for one is asking for a different artifact: changing the media type
would mean the thing published is no longer a Boltzmann brain.

## No network at all

```bash
vitruvio dist push demo/brain --tag v1 --local ./registry --json
vitruvio dist pull demo/brain --tag v1 --local ./registry --json
```

A filesystem registry of OCI layouts. Same code path, no credentials, no rate limits — the right thing to test a
round trip against before pointing anything at a real host.

## One note on hostnames

`docker.io` is the *index* hostname, not the API: `https://docker.io/v2/` returns a web page. The API is at
`registry-1.docker.io`, and vitruvio substitutes it for you. Every command prints the `effective` endpoint, so the
substitution is never invisible — if it looks surprising, that field is the answer.
