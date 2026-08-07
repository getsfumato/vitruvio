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

4. **Never invent a `block_id`.** Not in a citation, not in an `evidence` list, not to fill a gap. If you need
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
| 8 | push is not a fast-forward | pull, re-commit, push again — never `--force` |
| 9 | registry unreachable or refused | retryable |
| 10 | the cascade needs human review | stop and ask a person |

The distinction that matters most: **2** means you asked wrong, **5** and **6** mean the protocol refused and
retrying is pointless, **7** means your input was wrong and is fixable.

## Where to go next

- `vitruvio-query` — searching, and how to read an evidence bundle without over-claiming.
- `vitruvio-ingest` — the loop where you propose knowledge and the protocol validates it. The highest-value one.
- `vitruvio-retention` — removing things, and why there are five different ways.
- `vitruvio-dist` — publishing a brain and installing one.

## References

- `references/json-envelope.md` — the envelope, field by field.
- `references/exit-codes.md` — the table above with the reasoning.
- `references/evidence-bundle.md` — every field of a search result and what it does not mean.
- `references/cli-reference.md` — every command and flag.
