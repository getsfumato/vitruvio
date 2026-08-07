# 12. Benchmarking and calibration

```bash
vitruvio bench --tier 1000
vitruvio calibrate
vitruvio calibrate --from-samples
vitruvio inspect doctor
```

## The corpus is synthetic and has ground truth

`vitruvio.bench` builds deterministic brains at N ∈ {10², 10³, 10⁴, 10⁵} across six subjects with distinct
vocabularies, and it knows which blocks answer which query. That is what makes recall a measurement rather than an
impression.

The minimum size matters: **below roughly 512 blocks the exhaustive plan wins legitimately**, so a small corpus never
exercises an index at all. A benchmark that never touches the thing it is benchmarking is the failure this size floor
exists to prevent.

## What it reports

recall@{1,5,10}, nDCG@10, and p50/p95/p99 for six configurations: `scan`, lexical-only, vector-only,
`planner(λ=0)`, `planner(default)` and `planner(floor=1.0)`.

The `λ=0` column exists to demonstrate the recall/cost trade-off empirically rather than assert it rhetorically: with
recall weighted at zero the planner picks the cheapest admissible plan, and the recall column shows what that costs.

CI gates the N=10³ tier on two conditions:

- recall@10 at least as good as `scan`;
- p95 within 3× of `scan`.

The second is deliberately loose. A real planner **is allowed** to be slower on a tiny brain, and pretending otherwise
is what pushes it toward bad plans at the sizes that matter.

## Calibration

The cost model's constants are measured defaults, and `vitruvio calibrate` re-measures them on the machine in front of
you. `calibrate --from-samples` refits them by least squares from `.vitruvio/estimation.jsonl`, which
`query explain --analyze` appends to.

So the model improves on each brain it runs against rather than staying frozen at whatever the author's laptop
measured. A large estimate/actual divergence on one operator in `--analyze` is the honest way to find out the model is
wrong about *this* brain.

## `inspect doctor`

One command that answers "is anything about this brain going to disappoint me": stale indices, missing modules,
model-tag mismatches, absent embedders, tombstoned blocks, an unreachable registry. Run it after a pull and before a
publish.

## Determinism, because retrieval bugs hide in iteration order

Three habits carry most of the weight in the test suite:

- `fake:deterministic` derives vectors from sha256 — bit-exact, no dependencies — so "the vector index must find this
  synonym" is an assertion rather than a hope.
- The suite runs under `PYTHONHASHSEED ∈ {0, 1, 42}` and compares the explanation JSON byte for byte. That catches the
  most insidious class of bug here: set iteration order leaking into a plan.
- The five structural indices have byte-exact golden dumps. The vector index does not, and cannot — it asserts
  round-trip equality of *results*.

## The end

That is the whole guide. The [ADRs](../adr/README.md) carry the decisions and what they cost.
