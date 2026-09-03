# Contributing

```console
uv sync --all-packages          # the default dev environment: no torch, fast
uv run pre-commit install --hook-type pre-commit --hook-type commit-msg
```

A bare environment is deliberate. Every code path must be exercisable without a 2.5 GB download, which is why the
default text embedder is feature hashing — tagged `hashing/bow` loudly enough that nobody mistakes the result for
semantics.

## The gate

Every one of these runs in CI, and each exists because something silent got through once:

```console
uv run ruff check . && uv run ruff format --check .
uv run mypy packages apps                        # near-strict, on 3.11, 3.12 and 3.13
uv run lint-imports                              # the layering contract
uv run python -m vitruvio.cli.reference --check   # the generated CLI reference is not stale
uv run pytest -m "not slow" -n auto
uv run vitruvio bench --tier 800 --queries 12 --gate
```

Two of them are worth explaining.

**Generated facade.** A pre-commit hook runs when a runtime operation, the operation catalogue or the generator is
staged. It regenerates `_generated_facade.py` and stages that artifact, so the commit already in progress includes
the facade matching the operation change. The hook never creates a second commit; CI's `generate_facade --check`
remains the independent guard for commits made with `--no-verify`.

**`reference --check`.** `skills/vitruvio/references/cli-reference.md` is generated from the cyclopts declarations —
the same declarations that parse the arguments. A stale reference is worse than none: an agent that trusts a flag
which no longer exists spends its next turn recovering from a usage error, and nothing in the output says the
documentation was wrong.

**`bench --gate`.** The claim the planner rests on, checked rather than asserted: recall@10 at least a scan's, and
p95 within 3× of it. The tier is above the few hundred blocks at which an exhaustive scan legitimately wins — below
that it would measure the scan and pass for the wrong reason.

CI also runs `uv sync --all-packages --no-sources`, which proves the released-SDK path rather than assuming it: with
a local path override for `pyboltzmann`, every other job silently tests against a working copy.

## Conventions

- **Commits** go through `uv run cz commit` (conventional commits, and `cz bump` derives the version). The commit
  message carries the *reasoning*, because a diff already shows the change.
- **Docstrings** state why a choice was made, not what the line does. If a decision cost something, name the cost.
- **Tests** are named as the sentence they assert. A test whose name is `test_it_works` is a test nobody will trust
  when it fails.
- **Nothing may promise a command that does not exist.** `apps/cli/tests/test_docs_promises.py` fails the build over
  it, in the guide, in the README and in every skill — that test exists because the documentation once described a
  benchmark command and a calibrate command in a guide, an ADR and a *skill*, and neither had been built. It caught
  this very sentence on the first run, when both were still written as invocations.

## Slow and optional tests

```console
uv run pytest -m slow            # needs a Docker daemon: an ephemeral registry:2
uv run pytest --no-cov -k name   # coverage off, for one test
```

The `slow` tests skip themselves when no daemon is reachable, and they check for a non-empty `ServerVersion` rather
than a zero exit status — `docker info` exits 0 with the daemon stopped, which is how those tests once came to be
attempted against a dead daemon where `docker run` hangs instead of failing.

## Where to write things down

A change to behaviour lands in [`docs/`](docs/). A change to a *decision* lands in a new
[ADR](adr/README.md) — the old file stays, because the point of the series is the reasoning, and deleting a mistake
deletes the reasoning that led to the correction. A change to how an agent should drive vitruvio lands in
[`skills/`](skills/README.md).
