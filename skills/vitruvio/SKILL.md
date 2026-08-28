---
name: vitruvio
description: Read and write a Boltzmann brain through the vitruvio CLI. Use when the user asks about a brain, asks to search or retrieve knowledge from one, register a source, ingest a document, publish or install a brain, or mentions vitruvio, boltzmann, semantic/episodic/procedural memory, or evidence bundles.
allowed-tools: Bash(vitruvio:*), Read, Glob, Grep
---

# Vitruvio

A **Boltzmann brain** is portable, verifiable, model-agnostic knowledge: content-addressed typed blocks, a Merkle
DAG per memory module, and provenance for everything derived. `vitruvio` is the runtime that runs one.

The one thing to internalise before anything else: **the brain returns evidence, and you write the prose.** Every
command returns data. There is no `answer` field and there will never be one. Synthesising an answer from the
matches is your job, outside the brain, and the citations you produce have to point at block ids the brain
actually returned.

## Always

1. **Pass `--json`.** Every command emits exactly one envelope on stdout:

   ```json
   { "vitruvio": "0.1.0", "command": "query.search", "ok": true,
     "data": {}, "warnings": [], "error": null }
   ```

   Branch on `ok`, then on `error.code`. stdout is the result; progress and warnings go to stderr, so
   `vitruvio search q --json | jq` works unconditionally.

2. **Read `warnings` even when `ok` is true.** A degraded answer that looks identical to a clean one is the
   failure this whole design exists to prevent. "the vector index will not be published", "stats are stale",
   "the recall floor was lowered" all arrive there.

3. **Run `vitruvio brain state --json` first** in a new session. It tells you which modules are installed, at
   which version, and where the brain came from. A search against a module that is not installed returns
   nothing, correctly, and looks exactly like a search that found nothing.

   If the repository holds a **project**, run `vitruvio project show --json` too: it lists the named brains, and
   `--brain <name>` then selects one. A project is several brains under one configuration — a subject per brain,
   a client per brain — and picking the wrong one is a mistake nothing downstream will report.

   **Say which project and which brain on every command**, as `--project <name> --brain <name>`. That pair
   identifies a brain completely, independently of the working directory and of what any other session is doing,
   which is what lets several agents work on several projects at the same time. `vitruvio project list --json` is
   how to see which names `--project` accepts; a project that was cloned rather than created here needs
   `vitruvio project register` once. Do not rely on a saved default: a project holding several brains refuses to
   guess, and the refusal names them — pass one, rather than running `brain use`, which changes state other
   sessions read.

4. **To see what a brain holds, read it rather than searching it.** `vitruvio inspect blocks <module> --json`
   lists a module in its own order, one row per block, with what each one says — and for canonical evidence, the
   origin it was registered from. That is the answer to "what is in here", which a search cannot give you: a
   search ranks against a query, so anything the query does not reach looks absent.

   `--contains TEXT` filters those rows. It is a substring over rows already read, not retrieval: no index is
   consulted and nothing is ranked. When relevance is what you want, that is `search`.

   `vitruvio inspect content <DIGEST> --out FILE --json` writes the bytes a block names — pass the row's `blob`,
   which is a content address and not a block id. Never draw a PDF or an image into your own output: the terminal
   rendering is a thumbnail bounded by character cells, and what you want is the text. A canonical block carries a
   `normalized_view` when it was registered with `--normalize-with`, and *that* blob is the extracted text.

   `vitruvio inspect links <BLOCK_ID> --json` gives the provenance records naming a block: where it came from, and
   what has been done to it since.

   (`vitruvio browse` opens the same three reads as a terminal interface. Its `s` query workspace also shows the
   selected physical plan and bounded graph/vector/ordered-index visualizations. It is for a person and refuses
   `--json`; suggest it to the user, do not run it.)

   Sources belong to brains: `[brain.sources.<name>]` for a single brain and
   `[brains.<brain>.sources.<name>]` for a named project brain. Always select the brain before `source status`,
   `add`, `remove` or `pull`; the same source name may carry different persistent options in different brains.
   On one named pull, repeat `--option key=value` for an ephemeral override. It is refused with `--all`, which
   means every source of the selected brain and never crosses the project.

5. **Never invent a `block_id`.** Not in a citation, not in an `evidence` list, not to fill a gap. If you need
   one and do not have it, search for it or say you cannot cite.

## The five memory modules

| module | holds | may be dropped? |
|---|---|---|
| `canonical` | the evidence itself: registered sources and their normalized views | only if the policy allows it, and it cascades to everything derived |
| `episodic` | what happened, when | **never** — append-only by protocol; use `supersede` |
| `semantic` | facts, definitions, claims | yes |
| `procedural` | how to do something: goals and ordered steps | yes |
| `provenance` | who derived what, from what, when, and what was removed | written by the protocol, not by you |

Registering a source does **not** assert it is true. Canonical memory asserts *this evidence was incorporated and
preserved*. Every interpretation of it is a separate block that cites it.

## Exit codes

The whole point of these is "may I retry, and with what changed".

| code | meaning | what to do |
|---|---|---|
| 0 | success | — |
| 1 | a bug in vitruvio | report it; do not retry |
| 2 | usage error | rephrase the command |
| 3 | no brain selected, or config invalid | fix config, or pass `--brain` |
| 4 | not found | do not retry |
| 5 | protocol violation: verification, membership, integrity | **do not retry**; this is corruption or a broken claim |
| 6 | refused by retention policy | **do not retry**; the protocol says no |
| 7 | candidates rejected by validation | repair the payloads and retry |
| 8 | push is not a fast-forward | `dist fetch`, reconcile, push again — never `--force`, never `pull` |
| 9 | registry unreachable or refused | retryable |
| 10 | the cascade needs human review | stop and ask a person |
| 11 | a declared source was unreachable or refused | retryable |

The distinction that matters most: **2** means you asked wrong, **5** and **6** mean the protocol refused and
retrying is pointless, **7** means your input was wrong and is fixable.

## Where to go next

- `vitruvio-cli` — the command surface: which group owns a task, and which flag decides the outcome.
- `vitruvio-query` — searching, and how to read an evidence bundle without over-claiming.
- `vitruvio-compound` — one question across several brains of one project: choosing the project and the brains with the user, and reading what two brains agree on.
- `vitruvio-ingest` — the loop where you propose knowledge and the protocol validates it, plus pulling from declared sources. The highest-value one.
- `vitruvio-retention` — removing things, and why there are five different ways.
- `vitruvio-dist` — publishing a brain and installing one.
- `vitruvio-reconcile` — joining a history somebody else advanced, when a push comes back diverged.
- `vitruvio-sync` — local and remote disagree: which of behind, ahead or diverged it is, and the safe way back for each.

## References

- `references/json-envelope.md` — the envelope, field by field.
- `references/exit-codes.md` — the table above with the reasoning.
- `references/evidence-bundle.md` — every field of a search result and what it does not mean.
- `references/cli-reference.md` — every command and flag.
