# 8. Ingest: the agent-as-proposer loop

The protocol's rule is *the model proposes; the protocol governs what is stored*. In the SDK that is a type: a
`Candidate` has no `block_id` and no typed payload, so there is no method on the interface that could write to a Merkle
DAG.

```bash
vitruvio source register paper.pdf --media-type application/pdf --normalize-with pdf-text
vitruvio task define <BLOCK_ID> --allowed semantic --allowed procedural --task-id batch-01 --json | jq .data > task.json
vitruvio task schema --task task.json --json | jq .data > schema.json
#   ... the model writes candidates.json against that schema ...
vitruvio task validate candidates.json --task task.json
vitruvio task commit candidates.json --task task.json
```

Five steps, deliberately not one. Steps 2 and 4 are the two places a model can be *corrected* rather than left to
guess: it sees the exact required shape before writing, and the per-candidate verdict before committing.

## The four rules a proposal fails on

1. **`evidence` is never empty**, and it cites the task's source. A derived block with no evidence has no root to audit
   against. There is no case where a proposer legitimately cannot cite: the source block is always there.
2. **No numbers in a payload.** These documents get hashed, and a float does not hash reproducibly. `confidence` is a
   decimal string: `"0.85"`, never `0.85`. This is the most common rejection by a wide margin.
3. **`locator` says where in the source it came from.** A citation pointing at a whole document is barely one.
4. **Do not restate the document.** An extraction that is the document again has added nothing and made ranking worse.

Exit 7 means the proposal was wrong; retrying it unchanged fails identically. A `duplicate` rejection is not a defect —
it means the brain already holds that block, and it does not block a commit, which is what makes the
repair-one-and-resubmit loop work.

## Verdicts

`validated` earns a commit. `rejected` names a code to repair. `contradicted` conflicts with knowledge already held —
surface both block ids to the user rather than silently picking a side. `pending_review` means the protocol cannot
decide alone: **stop and ask a person**, because there is nothing wrong to repair.

## The shortcut

```bash
vitruvio ingest run doc.md --dry-run
vitruvio ingest run doc.md --subject fourier
vitruvio ingest run paper.pdf --proposer anthropic:claude-sonnet-5
```

The default proposer, `structure`, uses **no model**: it reads Markdown headings and proposes one semantic block per
section, extractively. Every statement it emits is text that was in the source, so it cannot invent — which makes it
both a real answer for structured documents and the right thing to build the rest of the pipeline against.

It also tracks code fences, because `# do the thing` inside a fenced block looks exactly like an H1, and a proposer
that reads it as one produces a semantic block asserting a shell comment.

`--dry-run` first, always, against a new kind of document.

## Re-deriving

```bash
vitruvio task rederive <DERIVED_BLOCK_ID>
```

For "a better model should revisit this". It records the supersession rather than leaving two competing interpretations
of one piece of evidence installed, and the old block stays auditable.

## Next

[9. Retention](09-retention.md)
