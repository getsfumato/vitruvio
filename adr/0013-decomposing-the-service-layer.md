# ADR-0013: Decomposing The Service Layer, And What Stayed On The Facade

**Decision status:** Accepted. Extends ADR-0003 rather than superseding it.

## Context

ADR-0003 decided one service layer with one method per protocol operation, each returning a plain dictionary, and
named four modules as carrying it: `service`, `assembly`, `wire`, `mapping`. That decision holds. What did not hold
is the shape of `service` itself.

`BrainService` reached 3034 lines and 60 public methods across twelve unrelated domains — lifecycle, registration,
the task pipeline, declared sources, retention, index build and gc, a benchmark harness, embedder probing, project
editing, OCI push and pull, retrieval. The next largest file in the workspace is `ingest/sources.py` at 773, so it
was a 4x outlier, and `git log --follow` shows why: it was born at 1651 lines and every commit that grew it was a
*feature*. It was the only place an operation could land.

Three smaller signs that the structure had stopped describing the code. The `# --- Distribution ---` banner had
zero methods under it — `pack`, `push`, `plan_pull` and `pull` lived under `# --- The project ---`, which at 509
lines was two domains. `tags` sat under `# --- What a pull would replace ---`. And this ADR's own paragraph said
four modules while the package held eleven: `browse`, `distribution`, `registry`, `vouch`, `query_diagnostics` and
`indexset` had already been lifted out one at a time. The decomposition had started; it had not been recorded.

## Decision

**One module per domain under `vitruvio.runtime.ops`, each a class over a shared `BrainSession`, with
`BrainService` kept as a facade that delegates.**

- `BrainSession` (`runtime/session.py`) holds what turned out to be the class's entire state: the resolved
  configuration, and one `Brain` per capability. **An operations class may hold the session and may never hold a
  `Brain`** — `InstallOps.pull` advances the pointer and calls `invalidate()`, and it can only invalidate what it
  can reach. There is a test for it.
- `ops/*.py` are the operations, which open brains. The stateless helpers beside them in `runtime/` do not. The
  names are close enough to need saying: `ops/publish.py` publishes and `runtime/distribution.py` transports;
  `ops/registration.py` registers a block and `runtime/registry.py` talks to a registry.
- **Heavy imports stay inside functions**, enforced by `test_import_cost.py` in a subprocess.
- **`ops/__init__.py` re-exports nothing.** A barrel would make importing one domain import all sixteen.

`BrainService` keeps all 60 operations, which is ADR-0003's contract restated rather than weakened: the CLI, the
TUI and the future MCP server all reach an operation on the service, and none of the 64 CLI call sites changed.

## Consequences

- **The operation catalogue is authoritative.** `operation_catalogue.py` declares each domain, operation and exposure
  mode once; the checked-in facade methods are generated from the operation implementations, and conformance and
  documentation metadata read the same catalogue. Adding an operation without declaring it, or changing a signature
  without regenerating the facade, fails `test_facade.py`.
- What that test buys over mypy is narrow and worth stating exactly. Drop a keyword argument that some caller
  already passes by name and mypy catches it. What mypy cannot catch is the shape that actually shipped in
  `dist push --all`: `_push_all` never had an `anonymous` parameter, so nothing called it with that keyword, every
  call site type-checked, and `--anonymous` silently published with stored credentials.
- **`ruff`'s `PLR0904` remains the ratchet on handwritten classes.** The generated facade is exempt because its
  method count is protocol data rather than absorbed implementation; catalogue completeness is the stronger ratchet
  on that artifact.
- Long docstrings live with the implementation in `ops/`; the delegator carries a one-line summary and a pointer.
  One source of truth, at the cost of `help(BrainService.push)` being a summary rather than the full text.
- `bench` had to be rewired rather than moved: it constructed a second `BrainService`, which would have made
  `ops/benchmarking.py` import the class that imports it. It now builds its own `BrainSession` and drives
  `IndexOps` directly — a cycle avoided, and a more honest shape, since the corpus brain was never the caller's.
- `service.py` went from 3034 lines to 807, and the largest new module is 369. Every commit in the sequence passed
  the full gate on its own, and 365 tests were untouched except six lines of `test_pull.py`, which reached
  `_pull_one` and `_source` directly and now reach them where they live.

## What was rejected

**Namespaced access** — `service.retention.drop(...)` — which removes the ~440 lines of forwarding entirely. It was
rejected because it changes 64 call sites and, more importantly, contradicts ADR-0003's "one method per protocol
operation" without a reason of its own. It also breaks `apps/cli/tests/test_project.py`, which monkeypatches
`BrainService.push` and calls the original positionally. The forwarding is reachable to delete later, if the
operation catalogue that ADR-0003 wants for the MCP server makes the namespaces load-bearing.

**A command object per operation** — `ResolvabilityCommand(session).execute()` — rejected because there is no
per-execution state to justify sixty new classes. What state exists is the session, and it is shared on purpose.

**Typing the 56 `dict[str, Any]` payloads in the same pass.** They are a real problem — `search` and `explain`
alone are read through ~70 distinct string keys across three stacked layers — but mixing a contract change into a
movement makes every commit unreviewable. It is a separate decision, not yet taken.
