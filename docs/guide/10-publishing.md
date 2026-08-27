# 10. Publishing and installing

```bash
vitruvio dist pack --tag v1                  # build locally, push nothing
vitruvio dist push docker.io/you/brain --tag v1
vitruvio dist plan-pull docker.io/you/brain --tag v1
vitruvio dist pull docker.io/you/brain --tag v1
vitruvio dist tags docker.io/you/brain
```

A published brain is an OCI Artifact: a small manifest, one layer per module, and a config blob that is the snapshot
document. Because the modules are separate blobs, a consumer can install one and later update only what changed.

## `pack` first, and read the warnings

The warning that matters: *"the vector index will not be published"*. A brain published without its vector layer cannot
be searched semantically by anyone else, because that is the one index a consumer cannot rebuild. The usual cause is a
stale index — `vitruvio index build`, then pack again.

## `plan-pull` before `pull`, every time

A canonical layer can be gigabytes. "How much is this going to cost" should be answerable without paying it.

A **selective** pull (`--module semantic`) is a legitimate, permanent state. The modules you did not take are *missing*,
not broken, and `inspect resolvability` reports them as such. Run `brain verify` after every pull.

### Installing without incompatible vector indices

The default pull is strict: if a published vector index was built in a different representation space, Vitruvio
refuses to load it rather than return meaningless similarity scores. To install every requested memory module while
omitting only those derived vector layers, make that choice explicit in both the plan and the pull:

```bash
vitruvio dist plan-pull ghcr.io/org/brain --tag v1 --ignore-vector-indices
vitruvio dist pull ghcr.io/org/brain --tag v1 --ignore-vector-indices
vitruvio index build --force
vitruvio brain verify
```

The pull still verifies each module against its published Merkle root. Structural and lexical retrieval remain
available immediately; run the rebuild before relying on vector retrieval. Both distribution commands report
`ignored_vector_indices`, so automation can distinguish this deliberate fallback from an artifact that carried no
vectors at all.

## Two refusals from the protocol

- **Exit 8, not a fast-forward.** Someone pushed since this brain was pulled. Reconcile: `dist fetch` brings their
  history without adopting it, and keeps both sides' work — see [Reconciling](16-reconciling.md). Never `--force`,
  which discards their version, and not `pull`, which discards yours. The check fails *closed* on any error that is
  not a 404, so a registry refusal that looks like an absence cannot quietly disable it.
- **Narrowing refused.** Publishing fewer modules than the last version would make a consumer's selective update
  silently lose one.

## No network: a filesystem registry

```bash
vitruvio dist push demo/brain --tag v1 --local ./registry
vitruvio dist pull demo/brain --tag v1 --local ./registry
```

Same code path, no credentials, no rate limits. The right thing to test a round trip against before pointing anything
at a real host.

## Credentials, and one thing that will surprise you

**A prior `docker login` does not authenticate vitruvio.** This is deliberate. The ORAS client re-reads
`~/.docker/config.json` before each request and, when a `credsStore` is set, runs the credential helper with **no
timeout** — so on macOS a helper that blocks hangs the whole push with no output at all. vitruvio isolates itself from
that store and imports once, under its own five-second timeout:

```bash
vitruvio registry login docker.io --from-docker
vitruvio registry login docker.io --username you --token-stdin < pat.txt
vitruvio registry whoami docker.io
```

Docker Hub needs a **Personal Access Token with Read & Write** scope; the account password does not work. Tokens are
read from stdin or an unechoed prompt, never from a flag — a token on a command line lands in the shell history and the
process list. Never in `vitruvio.toml`, which is committed.

Also: `docker.io` is the *index* hostname, not the API. `https://docker.io/v2/` returns a web page, so the failure shows
up as a JSON parse error far from its cause. The API is `registry-1.docker.io`, and vitruvio substitutes it — every
command prints the `effective` endpoint so the substitution is never invisible.

## Check a registry before the first push

```bash
vitruvio registry check docker.io/you/brain
```

A brain's manifest carries a custom `artifactType` and a custom `config.mediaType`, and registries have historically
disagreed about whether that is allowed. This pushes a probe with exactly that manifest shape and reports four things:
`/v2/` reachable, credentials carry write scope, the config media type accepted, and the `artifactType` preserved
through a round trip.

If a registry refuses, the alternatives are one that accepts it (ghcr.io does) or your own `registry:2`. There is no
compatibility mode: changing the media type would mean what you publish is no longer a Boltzmann brain, and that is a
protocol decision rather than a runtime one.

## Next

[11. Skills](11-skills.md)

## Refusing to publish at all

A brain declaring `publish = false` is refused by `dist push` with exit 6, before it packs anything or reads a
credential — a refusal that happens after a credential lookup has already told a keyring what you were about to do.
`dist push --all` skips it. See [Projects](13-projects.md#a-brain-you-did-not-author) for why an installed brain
wants this.

## What a pull replaces

A pull adopts the published composition and moves the head to it, with **no fast-forward check**. The divergence
guard is on `push`, where overwriting means overwriting somebody else's work; an install installs the other side's
version, which is the point of it.

So anything committed locally since the last pull stops being a member of any module: it no longer verifies into a
root, no longer appears in a search, and a pack no longer carries it.

```console
$ vitruvio dist plan-pull --tag v2.4
transfer    212.4 KiB
discards    approximately 5 blocks committed here since the last pull (they are in sha256:cabe876f9d)

$ vitruvio dist pull --tag v2.4
warning: 5 blocks committed here are no longer in the composition (exact impact); the snapshot that held
         them is still in `brain history`
discarded   5 blocks, exact impact
```

Nothing is destroyed. The blobs stay on disk and the previous snapshot stays in `brain history` — the retention
policy keeps ten of them — so the state is recoverable. But **no command restores it**: going back today means
editing `boltzmann/head.json` by hand.

Which is why a pull is the wrong tool for a divergence, and this used to be the only advice we had. If you have
committed into a brain somebody else publishes, `dist fetch` is the operation you want: it holds both histories
locally and joins them, keeping what each side did. Reach for `pull` to *install* a brain, not to catch up with
one. See [Reconciling](16-reconciling.md).

`plan-pull` compares the locally retained compositions by block identity and marks the result `approximate`, which is
why equal-size replacement cannot look like zero and why it needs no extra registry round trip. `pull` applies the
same set difference to the before and after compositions and marks it `exact`. If either composition cannot be read,
the JSON result says `certainty: "unknown"` and `blocks: null` instead of presenting zero as a fact.
