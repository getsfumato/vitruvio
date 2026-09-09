# ADR-0022: Results are strictly serializable, and their shapes are recorded rather than described

## Context

ADR-0003 decided that every interface serializes the same dictionaries. ADR-0013 deferred typing them, and issue
#31 converted one vertical slice — reconciliation — leaving the position it left behind: **82 of the 84 facade
operations return an anonymous `dict[str, Any]`**, read through string keys by the CLI, the TUI and eventually
anything else.

Two failures follow from that, and only one of them is about types.

**A value with no JSON form became its `repr()`.** `output.py` serialized with `json.dumps(..., default=str)` at
all three of its exit points. A `Path`, an enum outside `StrEnum`, a `datetime` — anything — produced a
well-formed envelope carrying `PosixPath('/Users/...')` where a caller expected a string, with no error and no
failing test. `git log -S` shows the flag has been there since the CLI's first commit and was never justified by
a specific failure.

**A renamed or retyped field broke nothing until it broke a reader.** Neither mypy nor any test observed the
field names, because there was nothing to observe them against.

The obvious fix for the second — write an example invocation per operation and assert its shape — builds a second
test suite that drifts from the first, and is eighty-four pieces of work before it protects anything.

## Decision

**The envelope refuses what it cannot serialize.** `vitruvio.kernel.serialization.dumps` replaces
`json.dumps(..., default=str)` and raises `WIRE_CONTRACT` naming the path of the offending value —
`data.rows[1].path is PosixPath` — rather than the class name `json` would give. Exit 1, accurately: no caller
can cause it and no caller can fix it. In the kernel and not beside `wire.py`, because it is a property of the
dictionary rather than of the SDK, and the CLI must be able to reach it without importing the runtime.

It also passes `allow_nan=False`, which is the second way a payload is not JSON and the quieter one: Python's
default writes the bare words `NaN`, `Infinity` and `-Infinity`, a strict parser rejects the whole document, and
a statistic that came out non-finite reaches a consumer as an unparseable envelope instead of a named field.
`data.statistics.mean is not finite` is the same trade as above — the path, which `json` never gives.

**The suite that exists is the corpus.** `wire_contract.py` wraps every operation whose result is JSON, on the
class that implements it, and compares the *shape* of each result — a set of dotted paths, each with the JSON
types seen there — against one checked-in file. A path or a type that file does not know fails the test that
produced it, naming the operation and the path. Recording is `pytest -p no:xdist --record-shapes`, one process,
opt-in. Checking needs no merge across xdist workers, which is why it is done per call rather than per session.

Shape and not values, because the values are digests and timestamps. Types as well as paths, because "renamed"
and "retyped" are both drift and only the first shows up as a new path. And **required as well as both**, because
a field that *disappears* shows up as neither: a subset check over what a call returned says nothing about what
it stopped returning, and an empty result would have passed. A path seen in every recorded observation of an
operation is required; one that some run did not produce — an empty list, a null parent, a branch not taken — is
listed under `optional`, which is the reviewable half of the statement.

**A key that is data is not a field.** `state.resolutions` is keyed by the block being resolved and
`snapshot.labels` by whatever somebody labelled a version, so recording those keys pinned one fixture's digests
as though renaming them were a schema change — and made the contract fail for any other brain with the same
shape. `DYNAMIC_KEYS` names the fields whose value is a map rather than an object, their keys record as `{}`, and
a test scans the golden for anything that still looks like a digest, a hash or a timestamp so a new map cannot
enter it unnoticed.

**What went unobserved is a list somebody chose.** `tests/operation_shapes_unobserved.json` names the operations
no test reached. It is asserted to cover the surface together with the recording, and asserted to hold nothing
that an interface elsewhere could call except the six that need a registry daemon or a governed brain with two
authorities. It found four with no excuse — `catalog_show`, `index_stats`, `index_gc`, `test_embedder`, which no
test had ever called — and those got tests rather than a line on the list. The six that remain are tracked in
issue #75: 76 of the 82 exposed operations have a contract, and the other six have a fixture to build rather
than a decision to make.

**Typing continues one domain at a time.** Lifecycle is this one: `StateResult` and the `SnapshotResult` that
`state`, `verify`, `history`, every distribution operation and the diagnosis all embed through `wire.snapshot`.
Plus `abort`, which completes reconciliation. Same idiom as `reconcile_result.py` — `TypedDict` over a payload
that stays a plain dictionary, `cast` where the pydantic dump becomes it — and the same
`get_type_hints(...)["return"] is ...` assertion.

## Consequences

- Removing `default=str` broke nothing: the whole suite passes without it. That is the evidence it was defensive
  rather than load-bearing, and the reason it could go in one commit.
- Typing `state` immediately found two things by type-checking alone: a test indexing `snapshot.labels` without
  checking it can be null, and two `x.get(k) or {}` fallbacks in the TUI and the diagnosis for keys that are
  always present. Neither was reachable before because there was nothing to check against.
- The golden file is 330 KB and will churn on any deliberate payload change. That is the point; it is reviewed as
  part of the diff, like any snapshot test.
- Required-ness is derived from the corpus rather than declared, so a test that exercises a new branch can move a
  field from required to optional and needs a re-record. That is the same cost the rest of the file already has,
  and it buys the only check that catches a deletion.
- The wrapper uses `functools.wraps`. It has to: `test_facade` reads `inspect.signature` through `__wrapped__`
  and `test_reconcile_result` reads `get_type_hints` through `__annotations__`, and a wrapper carrying only
  `__name__` made the typed slice look like it returned `Any`.
- Recording takes about a minute against thirteen seconds for a checked run, because it cannot use xdist. It is
  opt-in and rare.

## What was rejected

**Eighty-four example invocations.** A second suite, drifting from the first, and eighty-four pieces of work
before the first field is protected.

**Merging shapes across xdist workers.** It would let the check run per session instead of per call, at the cost
of writing files behind pytest's back and reconstructing them in the controller. Per-call checking needs none of
it and points at the test that produced the drift, which is more useful than a summary at the end.

**Typing retrieval first**, which the plan for this work said and which this record supersedes. `search` and
`explain` are the worst of the untyped surface — 117 and 140 paths — but `matches[].content` is polymorphic
across memory types, so typing it means modelling every block kind's projection as a discriminated union. That is
a modelling decision and not a mechanical conversion, and mixing it into the commit that removes `default=str`
would make both unreviewable. It is tracked separately; the recorded shape pins its field names in the meantime,
which is the protection that did not exist at all before.
