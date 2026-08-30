---
name: vitruvio-ingest
description: Propose knowledge into a Boltzmann brain as an agent, through the task lifecycle. Use when asked to ingest, extract, or add knowledge from a document into a brain, when writing a candidate set, when a validation gate rejected candidates, or when re-deriving knowledge with a better model.
allowed-tools: Bash(vitruvio:*), Read, Write, Glob, Grep
---

# Proposing knowledge

This is the loop the protocol was designed around: **you propose, the protocol governs what is stored.** You never
write to a Merkle DAG. You produce candidates; a validation gate accepts or rejects each one; only then does
anything become a block with an identity.

## The loop

```bash
# 1. the evidence goes in first, unchanged
vitruvio source register paper.pdf --media-type application/pdf --json

# 2. what is being asked, over which block, and which memory types are permitted
vitruvio task define <BLOCK_ID> --allowed semantic --allowed procedural --task-id batch-01 --json \
  | jq .data > task.json

# 3. the exact shape the answer must have
vitruvio task schema --task task.json --json | jq .data > schema.json

# 4. YOU write candidates.json here, satisfying that schema

# 5. the gate's verdict, per candidate, committing nothing
vitruvio task validate candidates.json --task task.json --json

# 6. commit — refused entirely if anything was rejected for a fixable reason
vitruvio task commit candidates.json --task task.json --json
```

Steps 3 and 5 are why this is not one command: they are the two places you can be corrected instead of guessing.

Save `jq .data`, not the whole envelope — though `--task` accepts either, because saving the envelope is the
obvious mistake and it is trivially detectable.

## The four rules a candidate set fails on

1. **`evidence` is never empty**, and it cites the task's `source`. A derived block with no evidence has no root
   to audit against. There is never a case where you legitimately cannot cite: the source block is always there.
2. **No numbers anywhere in a payload.** These documents get hashed, and a float does not hash reproducibly across
   machines. `confidence` is a decimal *string*: `"0.85"`, never `0.85`. This is the single most common rejection.
3. **`locator` says where in the source the claim came from** — `"lines:40-58"`, `"[page 3]"`, a timestamp. A
   citation that points at a whole document is barely a citation.
4. **Do not restate the document.** Propose what a later reader would want to *retrieve*. An extraction that is
   the document again has added nothing and made the brain worse at ranking.

Propose nothing rather than guessing. An empty candidate list is a valid, honest answer.

## When the datum is bytes, not a sentence

Any proposable type — semantic, episodic, procedural — may name its own datum as bytes instead of inlining it: a
rendered diagram, a lecture recording, a worked example. The payload carries a `content` reference rather than the
bytes themselves, because a payload is JSON that is canonically hashed on every access, and a binary does not
belong inside one. The schema from step 3 already offers the field on every type; absent means inline, which is
the ordinary case.

```bash
# the bytes go into the store first; what comes back is the reference, exactly as the payload needs it
vitruvio source put figure.png --media-type image/png --json
```

```json
{"memory_type": "semantic", "evidence": ["<SOURCE_BLOCK_ID>"], "locator": "[page 3]",
 "payload": {"kind": "concept", "label": "pendulum phase portrait",
             "statement": "the undamped pendulum traces closed orbits around the stable equilibrium",
             "content": {"blob": "sha256:...", "media_type": "image/png", "size": 48123}}}
```

Three rules on top of the four above:

1. **Copy the reference verbatim.** `blob`, `media_type` and `size` are all hashed into the `block_id`, and the
   gate checks the last two against the store; the rejection is `content-mismatch`. It exists because a consumer
   reads exactly those fields to decide whether to fetch bytes it does not yet hold.
2. **The text fields stay required, and are not captions.** `label` and `statement` (semantic), `summary`
   (episodic), `goal` and `steps` (procedural) are what a text query reaches — content is invisible to search.
   When the datum is binary, the statement is what the block claims *about* the bytes; a block whose text were
   empty would be installed, provable against the root, and unreachable.
3. **Content is not evidence.** `evidence` still cites the canonical source, exactly as rule 1 above demands.
   Content is the block's own datum: nothing else may cite it, and it leaves the store with its block. Material
   that other blocks will cite goes through `vitruvio source register`, as it always did.

`vitruvio inspect content <DIGEST>` reads the bytes back; `--open` hands them to the desktop. In
`vitruvio browse`, a block that names content previews its text first and the bytes beneath, and `o` opens them.

## Reading the verdict

Each result carries a `status`:

- `validated` — earns a commit. This is the only one that does.
- `rejected` — read the issue `code`. `duplicate` means the brain already holds it, which is fine and does not
  block a commit. Everything else is a repair: fix the payload and re-validate.
- `pending_review` — **stop.** The protocol is saying it cannot decide this alone. Ask a person; do not repair and
  retry, because there is nothing wrong to repair.
- `contradicted` — well-formed but in conflict with knowledge already held. Surface the conflict to the user with
  both block ids; do not silently pick a side.

Exit 7 means the proposal was wrong. Retrying it unchanged will fail identically.

Re-submitting a whole set after repairing one member is the expected workflow: the ones already committed come back
as duplicates, are reported as `already_held`, and do not block the commit.

## The shortcut, and when not to take it

```bash
vitruvio ingest run doc.md --dry-run --json     # register, propose, validate, commit nothing
vitruvio ingest run doc.md --subject "fourier" --json
```

`ingest run` uses an in-process proposer. The default, `structure`, uses **no model**: it reads Markdown headings
and proposes one semantic block per section, extractively. That is often the right answer for a well-structured
document, and it cannot invent, because every statement it emits is text that was in the source.

Always `--dry-run` first against a new kind of document. It is how you find out the media type was guessed wrong,
or that a document has no extractable structure at all.

`--proposer anthropic` / `--proposer openai` call a model with the task's schema as structured output. They need
the `[api]` extra and a key.

## Normalized views

`--normalize-with NAME` produces a deterministic, content-addressed text view of a source, and that view is what a
proposer reads. `vitruvio ingest pipelines --json` lists what is available. Two things worth knowing:

- The view is *evidence*, so the same input and pipeline version must produce identical bytes anywhere. That is why
  the pipelines are conservative and why the version is recorded in provenance.
- **Raster images have no pipeline**, deliberately: a re-encode is not reproducible across library versions. SVG
  does, because it is text and its labels are the signal.

## When the evidence arrives on its own

A **source** is a declaration of where material comes from, in `vitruvio.toml`. `pull` acquires from it and registers
into canonical memory; interpretation is still this skill's loop, unchanged.

```bash
vitruvio --project p --brain b source status --json  # this brain's declarations and availability
vitruvio source pull papers --dry-run --json    # what it would take, fetching nothing
vitruvio source pull papers --json
vitruvio source pull aula --option course_id=77 --dry-run --json  # override a default for this pull only
vitruvio source pull --all --json               # every source declared by the selected brain
```

The declaration lives under `[brain.sources.<name>]` or `[brains.<brain>.sources.<name>]`; project-level
`[sources]` and a source-level `brain` field are invalid. The same installed kind and source name may be declared
under several brains with different persistent options. Repeatable `--option key=value` values are merged over the
selected brain's declaration for that invocation and do not rewrite `vitruvio.toml`. They are deliberately
unavailable with `--all`: overrides are kind-specific, so one set cannot safely apply to heterogeneous sources. A
kind must include identity-changing options in every item's `origin` (course plus resource, not resource alone), or
origin dedup could skip an item from the wrong parameter set.
If a remote listing omits the MIME type or real filename, the source should return
`FetchResult(data, media_type=..., title=...)` from `fetch` after inspecting the downloaded native file. Do not put
`application/octet-stream` on the `Item` merely to make the type non-null: Vitruvio deliberately re-fetches a
generic registration once so fetched metadata can correct it, then resumes cheap origin skips after a specific MIME
type is held.

Read three things in the result:

- **`counts`** — `registered`, `skipped`, `duplicate`, `failed`. `skipped` is the normal outcome of a repeated pull
  and means the origin was already registered; it is not a problem to fix.
- **`items[].outcome`** per item, with a `reason` when it is `skipped` or `failed`. Per-item failures do not stop the
  rest, so a pull can succeed overall while one file did not arrive.
- **exit 11** means a whole *source* was unreachable — a tool missing, a timeout, a directory gone. Worth retrying
  later. Exit 3 means the declaration is wrong and retrying changes nothing until a file is edited.

**A pull cannot restore redacted bytes, and must not be used to try.** A digest that `vitruvio retain redact`
tombstoned is refused with `outcome: skipped` and a reason naming the redaction, and `--refetch` does not override
it. If a user asks you to bring back redacted content, say that the refusal is deliberate and that undoing a
redaction is a manual `vitruvio source register` — do not look for a flag that defeats it.

Do not run `source add` on a user's behalf without being asked to. A `directory` source pointed at the wrong folder
turns everything in it into content-addressed canonical evidence, and `dist push` would publish it.

## Re-deriving

```bash
vitruvio task rederive <DERIVED_BLOCK_ID> --json
```

The operation for "a better model should revisit this". It records the supersession rather than leaving two
competing interpretations of one piece of evidence installed, and the old block stays auditable.
