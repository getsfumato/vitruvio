---
name: vitruvio-query
description: Search a Boltzmann brain and read the result honestly. Use when retrieving knowledge from a brain, when a search returns too much or too little, when a score or a ranking needs interpreting, or when asked why the planner chose the plan it did.
allowed-tools: Bash(vitruvio:*), Read
---

# Querying a brain

```bash
vitruvio search "descomponer una funcion periodica en senos" --json
vitruvio query search "TEXT" --memory-type semantic --limit 10 --json
vitruvio query explain "TEXT" --json          # which plan, why, and what it rejected
vitruvio query resolve <BLOCK_ID> --json      # one block, in full
vitruvio query prove <BLOCK_ID> --memory-type semantic --json
```

## Filters bound; hints suggest

`--memory-type`, `--subject`, `--since`/`--until` and `--tag` **restrict what is eligible**. They are how you stop
"qué pasó en mayo" from competing with "definí serie de Fourier": the first is episodic, the second semantic, and
without the filter both compete in one ranking.

`--mode` (`lexical`, `semantic`, `associative`, `hybrid`, `auto`) is a *hint*. It narrows the space of admissible
plans; it never picks one. In particular `--mode semantic` still admits a term scan, because no index in this
protocol is authoritative and a hint must not be usable to make one so.

## Reading a bundle without over-claiming

A search returns an **evidence bundle**. Before citing anything from it:

- **`score` is agreement between retrieval strategies. It is not a probability, not a confidence, and not a
  relevance guarantee.** It is a string in the JSON. Do not parse it to a float and do not reformat it.
- **`truncated: true` means there may be more.** vitruvio sets it whenever a candidate that passed every filter
  was discarded — *including* one cut by a generator's `k`. A vector probe with `k=40` over 500 masked vectors is
  truncated even if it returned 12 matches, because 460 were never looked at. If you are about to say "the brain
  contains no X", check this field first.
- **`superseded_by` present means something replaced it.** Cite the successor, or say explicitly that you are
  quoting a superseded claim.
- **`resolvable: false` means redacted or not installed, not corrupt.** The block is still a verifiable member.
  You can cite that it exists; you cannot quote its content.
- **`verified`** is true for everything returned: verification failures are *discarded*, never returned with a
  flag, and the discard shows up as a `Degradation` in the explanation. If a bundle looks short and
  `degradations` mentions verification, that is corruption and it is exit-5 territory.

## When a search disappoints

Run `vitruvio query explain "TEXT" --json` and read three fields:

- `indices_available` vs `indices_consulted`. The most common complaint is "why did it not use the vector
  index", and the answer is one of exactly four: it is absent, it is stale, its model tag does not match, or it
  cost more than the alternative. All four are visible here.
- `statistics` — a module reported `stale` means selectivity estimates were pessimistic and the plan cache was
  bypassed. `vitruvio index build` fixes it.
- `considered` — every rejected plan with its reason. "only 1 scored generator with 3 available" is the
  single-authority rule refusing a plan, not a bug.

`explain --analyze` adds measured rows per node beside the estimates. A large est/act divergence on one operator
is the honest way to find out the cost model is wrong about *this* brain; `vitruvio calibrate --from-samples`
refits it.

## A small brain legitimately scans

Below roughly 500 blocks, an exhaustive scan usually beats any index — embedding a query costs about 4.5 ms, which
at 200 blocks is more than reading all of them. `explain` showing `SeqScan` on a small brain is the cost model
being right, not the planner giving up.
