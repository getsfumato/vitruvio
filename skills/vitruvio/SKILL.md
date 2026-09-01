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

6. **Use canonical actor identities for writes.** They are lowercase addresses (`alex@example.org`) or namespaced
   names (`openai/codex`), and are refused rather than normalized because they enter block identities. When another
   model or service assisted the configured actor, pass `--assisted-by` explicitly; assistance is provenance, not
   authorship inferred after the fact.

7. **Keep integrity and authenticity separate.** `brain verify` checks hashes and roots. `auth status` additionally
   evaluates SSH signatures, trust-root authority and the consumer's pin. Intact unsigned data is not corrupt, and it
   is not authenticated either.

## Choose governance before creating a brain

Treat both `brain init` and the destination of `brain migrate` as an irreversible trust choice. Governance belongs
in genesis and cannot be added to that same brain later. If the user has not chosen, stop before either command and
ask, in the user's language, whether the new brain should be **governed** or **ungoverned**. Offer exactly those two
protocol choices, keeping the words `governed` and `ungoverned` visible. Do not rename them as personal, shared,
public, or similar categories; do not recommend or select a default; and do not create anything until the user
answers.

Explain the choices as:

- **ungoverned** — integrity, provenance and retention still apply, but the head has no trust root and remains
  unsigned; or
- **governed** — a trust root names the public keys whose signatures consumers may accept.

Never let the CLI defaults make this decision silently: `brain init` defaults to ungoverned, while migration
defaults to governed.

For an ungoverned brain, confirm that unsigned distribution is acceptable, then initialize without `--governed` or
migrate with the explicit `--no-governed`:

```bash
vitruvio --json --actor ACTOR brain init PATH
vitruvio --json --brain OLD --actor ACTOR brain migrate --to NEW --no-governed
```

For a governed brain, do not choose authorities on the user's behalf. Ask for all of these before creating it:

1. Each authority's canonical two-field Ed25519 public key (`ssh-ed25519 BASE64`), never a private key.
2. The canonical actor `subject` that key represents.
3. The scopes that key receives: `ingest`, `commit`, `drop:canonical`, `redact`, `govern`, or `propose`.
4. `govern_quorum`, and which authorized fingerprints currently loaded in `ssh-agent` will sign the genesis.

Explain the permission boundary while asking: scopes authorize a signature in the eyes of a verifier; they do not
grant filesystem access or stop somebody modifying a local copy. OS permissions control local writes, retention
policy controls allowed removal operations, registry credentials control publication, and the trust root controls
which resulting snapshots consumers call authorized.

Use `--governed --sign-with FINGERPRINT` only when the user explicitly wants every supplied key associated with the
configured actor and granted every scope. For distinct subjects or least-privilege scopes, author a reviewed
`TrustRoot` TOML/JSON document from the answers and pass `--trust-root`; do not take the all-scope shortcut merely
because it is shorter. A trust root must include at least one active `govern` holder, and its quorum must be reachable.

After creation, report the independent results of both checks:

```bash
vitruvio --json --brain PATH brain verify
vitruvio --json --brain PATH auth status
```

## Audit authorship after pulling a brain

After every successful `dist pull`, run `brain verify` and `auth status` against that exact brain. If
`data.trust_root` from `auth status` is non-null, the installed brain is governed: always run `auth attribution` before
reporting the pull complete. Do this even when the pull itself reports `data.authenticity: authorized`, because that
verdict authorizes the snapshot signature while attribution checks whether its newly introduced provenance actors
match the signing keys' vouched `subject`s.

For a governed brain, raise an explicit **possible authorship breach** warning if `data.state` from `auth status` is not
`authorized`, or if attribution reports `complete: false` or `fully_vouched: false`. Surface `asserted`, `legacy`,
`evidence_gaps`, and `detail`. The attribution audit reports rather than refuses: an unvouched actor can be legitimate
after a merge, so do not call the brain corrupt from this warning alone, but never call it fully authenticated either.

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
- The catalog, authenticity and legacy-migration operator guides live in the repository documentation; consult the
  generated CLI reference for their complete flags.

## References

- `references/json-envelope.md` — the envelope, field by field.
- `references/exit-codes.md` — the table above with the reasoning.
- `references/evidence-bundle.md` — every field of a search result and what it does not mean.
- `references/cli-reference.md` — every command and flag.
