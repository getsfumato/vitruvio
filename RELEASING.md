# Releasing

Nobody cuts a release by hand. `main` is released from its commit messages: merge a `fix:` and a patch goes
out, merge a `feat:` and a minor does, merge `docs:` and nothing happens. This file is for when that is not
what you observe.

## What runs

One workflow, [`.github/workflows/release.yml`](.github/workflows/release.yml), in four jobs.

| Job | What it decides |
|---|---|
| `plan` | whether the commits since the last tag call for a release, and which version |
| `gate` | that the commit deserves a tag: lint, types, layering, skills reference, fast tests |
| `release` | bumps the nine versions, relocks, writes the changelog, tags, builds, publishes |
| `smoke` | that `install.sh` installs the release it just published, on three runners |

The version arithmetic is not in the workflow. It is `[tool.semantic_release]` in `pyproject.toml`, read by
[python-semantic-release](https://python-semantic-release.readthedocs.io/), which is also what makes the
question answerable locally:

```console
uv run semantic-release version --print-tag          # what the next push to main would release
uv run semantic-release version --print-last-released-tag
```

`fix:` and `perf:` take a patch. `feat:` takes a minor. A breaking change takes a minor too, not a major,
while the major is `0` (`major_on_zero = false`). Everything else — `docs`, `style`, `refactor`, `test`,
`build`, `ci`, `chore` — releases nothing. A subject that is not a Conventional Commit at all also releases
nothing, silently, which is the failure mode to suspect first when a merge produced no release: `feet:` parses
as prose, not as a typo.

## One version, nine distributions

Every member is bumped to the same number, and `uv build --all-packages` builds all nine. That is deliberate:
`vitruvio` depends on `vitruvio-kernel` and `vitruvio-runtime`, which pull in five more, and none of them is
independently useful. Per-package versioning at this size buys nothing and costs a release-orchestration
story.

It also means `uv.lock` is part of a release. The lock records each member's version, and CI runs with
`UV_FROZEN=1`, so a bump that left the lock alone would make the *next* push fail on a file nobody touched.
`build_command` runs `uv lock` before the build, and `assets = ["uv.lock"]` puts the result in the release
commit.

## What a release contains

Three assets, not eighteen:

- `vitruvio-v<version>-wheels.tar.gz` — the nine wheels and nine sdists
- `.tar.gz.sha256` — what `install.sh` verifies before extracting
- `install.sh` — so `releases/latest/download/install.sh` always serves the installer that release was
  tested with

The bundle exists because the nine distributions are only installable together, so the unit an installer
needs is the whole set: `install.sh` points `uv tool install --find-links` at the extracted directory and lets
uv resolve within it, while third-party dependencies still come from PyPI.

The release is created as a **draft**. `releases/latest` — which is what `install.sh` resolves — never
resolves to a draft, so the assets are uploaded, counted, and installed from before the release becomes
visible to anyone's installer. A build that cannot be installed stays a draft.

## The nine versions are not pinned to each other

`vitruvio` depends on `vitruvio-kernel`, not on `vitruvio-kernel==0.2.0`. Inside the workspace that is
right — `[tool.uv.sources]` resolves every member locally and the lock pins them together — but outside it,
an *upgrade* over an existing environment satisfies the unpinned requirement with whatever is already
installed. The observable symptom is a new CLI on eight old libraries, which installs cleanly and then
reports the **old** version, because `--version` reads `vitruvio.kernel.__version__`.

`install.sh` passes `--reinstall` so the environment is rebuilt rather than patched, and asserts that
`vitruvio --version` equals the version it set out to install — which is where this was found. The draft
install in the release job does the same.

That covers the installer, and does not cover `pip install --upgrade vitruvio`. Pinning the eight
inter-package dependencies to `==<version>` would close it properly, at the cost of making every member's
dependency line a thing releases must rewrite. It is a real decision, not an oversight, and it is not made
here.

## Nothing goes to PyPI yet

`[tool.semantic_release.publish]` is configured but `semantic-release publish` is not run: there is no PyPI
project and no trusted publisher. When there is, the step goes in the `release` job *after* the release is
published — a GitHub release can be deleted, a PyPI version can be yanked but never deleted and never reused,
so the irreversible half goes last. `install.sh` already falls back to PyPI when a version has no bundle, so
the installer needs no change.

## Forcing a release

Actions → release → *Run workflow* → `force: patch | minor | major`. It releases even when the commits do not
call for one, and it takes the same `gate`. Use it after a transient failure, or when a release should exist
for a reason the commit messages do not carry.

## When it goes wrong

**The push produced no release.** Check the `plan` job summary; it prints the last tag and the next. Then run
`uv run semantic-release version --print-tag` locally. Equal versions mean nothing releasable was merged —
almost always a subject that is not a Conventional Commit, or only `chore`/`docs` commits.

**`plan` said one version and `release` created another.** The `release` job fails on exactly this and says
both numbers. It means a commit landed on `main` between the two jobs. Re-run the workflow; the plan is
recomputed from the new head.

**The release is stuck as a draft.** Either the asset count was wrong or the installer check failed. The job
log says which. The draft is safe to leave: no installer can see it. Fix forward and re-run — the workflow
reuses an existing release for the tag rather than failing on it.

**The push was rejected.** The release commit goes straight to `main`, so branch protection has to let
`github-actions[bot]` bypass whatever it requires — or the workflow needs a PAT in place of `GITHUB_TOKEN`.
The failure is on semantic-release's push step, after the local commit and tag already exist, so treat it as
the case below.

**The tag exists but the workflow did not finish.** Delete the draft release, delete the tag locally and on
the remote, and re-run. Do not move a tag that a published release already points at.

**`smoke` failed after the release went out.** The release is live and `install.sh` is broken for the case
that runner covers. The installer is a file in the repo and an asset on the release, so fixing it and merging
does not need a new version — but the asset on that release stays broken until one is cut, and
`raw.githubusercontent.com/.../main/install.sh` is the URL the README documents for exactly that reason.

## Prereleases

`[tool.semantic_release.branches.develop]` releases from `develop` with a `b` token (`0.2.0b1`). The workflow
does not trigger on `develop` yet: nothing consumes prereleases, and a `releases/latest` that could resolve to
one would be a real hazard. Adding the branch to `on.push` is the whole change when something does.
