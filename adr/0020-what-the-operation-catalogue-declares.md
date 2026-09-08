# ADR-0020: What the operation catalogue declares, and why every fact is checked

## Context

ADR-0013 made `operation_catalogue.py` authoritative for the facade, and it worked: adding an operation without
declaring it fails `test_facade.py`. But what it declared was only that an operation *exists* — module, class,
property, name, and whether the facade forwards it. Six fields, ninety-four names.

That is enough to generate forwarding and nothing else. An interface that is not the CLI has five more questions
to answer before it can offer an operation, and every answer lived in the implementation:

- **May this caller run it?** Capability is already an authorization decision — `assembly.py` hands a *writing*
  actor only at `WRITE` — but forty of the ninety-four operations name no capability in their body at all.
  `pull_source` picks between INSPECT and WRITE at runtime from `dry_run`; `doctor` reaches its brain through a
  private helper; `add_brain`, `bench` and the compound operations open a brain that is not this session's.
- **Does it change anything?** Nothing said. `session.write()` is a good proxy and wrong at the edges: `pack`
  holds the write brain and only reads, while `init` and `add_brain` create a layout without ever entering it.
- **Can the result go in an envelope?** `content` returns `bytes`. Nothing distinguished it from `search`.
- **Does it name a location on this host?** Six operations take a required `Path`. Nothing distinguished them
  either.
- **Which of `push` and `push_async` is the operation?** Both were declared, as unrelated sibling strings. The
  fact that the coroutine is the canonical one lived in a private helper's docstring, `ops/remote.py`. A loop
  over the catalogue would have offered twelve distribution tools where there are six.

Meanwhile `documentation_metadata()`, whose own docstring said "for documentation and future protocol adapters",
had zero consumers — while `ARCHITECTURE.md` kept a table of the same facts by hand, which had drifted: it named
`keys` for `auth_keys`, omitted `fetch`, and had no row for reconciliation at all.

## Decision

**An operation is a record, not a name.** `operations` becomes a tuple of `Operation`, declaring `capability`,
`mutates`, `result`, `remote`, `network`, `heavy` and `async_name` beside the name. `cost` is derived from the
last three rather than declared, so it cannot disagree with them.

**A sync/async pair is one operation.** The coroutine is named by the operation it belongs to.
`facade_operations()` still yields both methods, so the generated facade is byte-identical and `test_facade`'s
completeness check is unchanged; `protocol_operations()` yields the operation once, and omits anything declared
`Remote.LOCAL`.

**Every fact is checked against the code.** `test_operation_catalogue.py` walks each implementation — following
private helpers and the async twin, because that is where the capability usually is — and asserts that nothing in
the body asks for more than the declaration allows. The checks are one-directional on purpose: over-declaring is
permitted, because `compound_search` opens RETRIEVE on somebody else's session and `pull_source` decides at
runtime, and under-declaring is the direction that would let an interface offer a write to a caller allowed only
to read.

**`Capability` moves to its own leaf module.** The catalogue must be readable by the generator and by
documentation, and `assembly.py` imports the SDK to build brains. Declaring a fact must not cost what performing
it costs.

**The `ARCHITECTURE.md` table is generated.** The same hook that writes the facade writes it, and a test asserts
the committed file contains what the catalogue renders.

## Consequences

- All twenty-one domain entries were rewritten in one commit. `test_facade.py` asserts exact set equality between
  a class's public methods and its declaration, so there was no incremental path — and the change is mechanical.
- `mutates` is true for an operation that only *holds* the write brain, `pack` and `plan_drop` among them. That is
  deliberate rather than imprecise: holding it engages the retention policy and the validation gate and takes the
  session's writer slot, and those are what a caller is being allowed to do.
- The generator no longer hard-codes its import block. It reads each annotation's origin from the module the
  signature was copied from, so a domain can introduce a result type — which is what #58 does next — without
  editing the generator. The regenerated facade is byte-identical to the previous one, which is how the change
  was checked.
- A class in `ops/` that is not catalogued can no longer expose a public method silently. `test_facade` compares
  per catalogued domain, so an absent class was invisible to it; `FetchOps` passed only because every one of its
  methods is private.
- Nothing consumes `protocol_operations()` yet. It is the inventory an interface elsewhere would build from, and
  the point of this record is that building one no longer requires reading twenty-one modules first.

## What was rejected

**Deriving the facts instead of declaring them.** It is the obvious alternative and it does not work: the survey
that produced this table found capability undiscoverable for forty operations, runtime-dependent for one, and one
call away for eleven more. A declaration can be checked against the code. An inference cannot be checked against
anything.

**Leaving the sync and async names independent.** Cheaper, and it pushes the pairing into every interface that
reads the catalogue — which is the same argument ADR-0013 made about the fan-out in `dist push --all`.
