---
name: vitruvio-query
description: Search a Boltzmann brain and read the result honestly. Use when retrieving knowledge from a brain, when a search returns too much or too little, when tempted to list a module's blocks instead of searching it, when a score or a ranking needs interpreting, or when asked why the planner chose the plan it did.
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

## Search first

`search` is the read path of a brain. The planner chooses among the derived indices — BM25 postings, the vector
index, the relation graph, facet bitmaps, ordered ranges, hash lookups — fuses their results, discards anything
that fails verification, and returns a ranked, cited bundle. Nothing else in the CLI ranks, and nothing else
consults an index on your behalf. Every question about what a brain knows starts here, including "is there
anything on X": a listing cannot tell you what is relevant, and reading a module block by block is exactly the
work the indices exist to make unnecessary.

`inspect blocks` has a place — the last rung of the ladder under *When a search disappoints*, never the first.

## Filters bound; hints suggest

`--memory-type`, `--subject`, `--since`/`--until` and `--tag` **restrict what is eligible**. They are how you stop
"qué pasó en mayo" from competing with "definí serie de Fourier": the first is episodic, the second semantic, and
without the filter both compete in one ranking.

`--mode` (`auto`, `exact`, `lexical`, `semantic`, `associative`) is a *hint*. It narrows the space of admissible
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

Escalate in this order. Each rung is cheap, and jumping to the last one throws away the ranking you came for.

**1. Read the bundle you have.** `truncated: true` means candidates that passed every filter were cut: raise
`--limit`. A filter restricts eligibility, so an empty result under `--since`, `--tag`, `--subject` or `--class`
says nothing about the brain without it — drop the filter and search again. Rephrase in the vocabulary the sources
use: the language they were written in, the exact term, a synonym. Try `--memory-type` if the kind of memory is
obvious and was not stated.

**2. Ask the planner why.** Run `vitruvio query explain "TEXT" --json` and read three fields:

- `indices_available` vs `indices_consulted`. The most common complaint is "why did it not use the vector
  index", and the answer is one of exactly four: it is absent, it is stale, its model tag does not match, or it
  cost more than the alternative. All four are visible here.
- `statistics` — a module reported `stale` means selectivity estimates were pessimistic and the plan cache was
  bypassed. `vitruvio index build` fixes it.
- `considered` — every rejected plan with its reason. "only 1 scored generator with 3 available" is the
  single-authority rule refusing a plan, not a bug.

`explain --analyze` adds measured rows per node beside the estimates, and `estimation_error` summarises the gap. A
large divergence on one operator is the honest way to find out the cost model is wrong about *this* brain. There is
no command that refits it: the constants are measured defaults, and `[planner]` in `vitruvio.toml` overrides them by
hand.

**3. Repair the indices and search again.** `vitruvio index list --json` says what is registered per module and
whether it is usable; `vitruvio index build --json` rebuilds what is absent, stale or built for another model. Then
repeat the search. Do not fall through to reading because the first search ran against a brain with no usable
index — that is a search that never happened.

**4. Read the module.** Only now, and only as described in the next section.

## Reading a module is the last resort

`inspect blocks` lists a module in its own order, one row per block. It consults no retrieval index, ranks nothing,
and returns no score column because nothing was scored. Reach for it in exactly two situations: the ladder above is
exhausted and retrieval has demonstrably failed, or the user asks literally for an inventory — "what files are
registered", "show me everything in canonical". A question about *content* is never one of those two, however it
is phrased.

```bash
vitruvio inspect blocks canonical --json          # every block, in the module's own order
vitruvio inspect blocks semantic --contains fourier --limit 50 --json
vitruvio catalog --json                           # canonical sources in their portable class hierarchy
```

`--contains` filters rows that were already read — it names no index and cannot rank — so never present its rows
as relevance, and say plainly that what you are quoting was read rather than retrieved. `inspect content DIGEST
--out FILE` gets the bytes a canonical block names, and `inspect links BLOCK_ID` gets the provenance records about
a block.

Catalog navigation is also not retrieval: `catalog --json` is the structured inventory of canonical sources by
scheme/class and includes unclassified evidence. Its use in retrieval is to pick a `--class` that bounds a search.
Its creator verification fields are historical signature evidence, not a relevance or truth score.

## A small brain legitimately scans

Below roughly 500 blocks, an exhaustive scan usually beats any index — embedding a query costs about 4.5 ms, which
at 200 blocks is more than reading all of them. `explain` showing `SeqScan` on a small brain is the cost model
being right, not the planner giving up.

## Several brains at once

When the question spans two or more brains of one project, `vitruvio compound search "TEXT" --brains a --brains b --json`
asks each on its own and composes the bundles — grouped per brain by default, because each brain's scores are
normalised to its own best match and do not compare, or merged by rank with `--fuse`. Every field above keeps its
meaning per match; each match also carries `brains[]`, saying which brain returned it and at what rank. The
`vitruvio-compound` skill covers choosing the project and the brains, and reading the composed result.
