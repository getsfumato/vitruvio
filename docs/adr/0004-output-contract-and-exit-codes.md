# ADR-0004: The Output Contract And Exit Codes

**Decision status:** Accepted, implemented in M0/M1.

## Context

An agent will drive this CLI through a skill. That makes machine-readable output a hard requirement rather than
a nicety, and it makes the *shape* of that output part of the interface: whatever is driving vitruvio has to be
able to tell success from failure, and a failure it should retry from one it should not, without special-casing
forty commands.

## Decision

**stdout is the result; stderr is everything else.** Progress, warnings and notes never touch stdout, so
`vitruvio search q --json | jq` works unconditionally.

**`--json` emits exactly one object, with a top level that never varies:**

```json
{ "vitruvio": "0.1.0", "command": "query.search", "ok": true,
  "data": {}, "warnings": [], "error": null }
```

`command` is a stable dotted operation name. `error` carries `code`, `kind`, `message` and `hint`. `warnings` is
present on success too, because a degraded answer that looks identical to a clean one is the failure mode this
whole design is trying to prevent -- and in JSON mode a warning goes into the envelope rather than to stderr,
where a machine would not see it.

**Exit codes are a contract: append-only, never reassigned.**

| code | meaning | retry? |
|---|---|---|
| 0 | success | -- |
| 1 | a bug in vitruvio | no |
| 2 | usage error | rephrase |
| 3 | no brain selected, or config invalid | fix config |
| 4 | not found | no |
| 5 | protocol violation: verification, membership, integrity | no |
| 6 | refused by retention policy | no |
| 7 | candidates rejected by validation | repair and retry |
| 8 | push would not be a fast-forward | pull, re-commit |
| 9 | registry unreachable or refused | yes |
| 10 | cascade requires human review | ask a human |

The distinction that matters: 2 means "you asked wrong", 5 and 6 mean "the protocol says no, do not retry", 7
means "your input was bad, fix it and come back".

cyclopts exits 1 on a usage error by default. Since 1 here means "a bug in vitruvio", usage errors are caught
and remapped to 2 -- cyclopts has already rendered the message, which is better than anything worth
reimplementing, so nothing is printed twice.

**The renderer never joins matches into a sentence.** The brain returns evidence and the caller writes the
answer. A CLI that summarised for you would be a CLI that had quietly become the model, and the summary would
carry none of the verification the bundle exists to provide. `Match.score` likewise stays a *string*: it is a
decimal string in the protocol, and parsing it to reformat it would invent precision the protocol deliberately
does not carry.

## Consequences

- Tests assert on the parsed envelope, never on human text. Human rendering is expected to churn.
- Adding a command means picking a dotted name and a capability; the envelope and the exit codes come for free.
- Ten exit codes is more than most tools have. Each one exists because it answers "should I retry, and with
  what changed" differently, which is the only question an automated caller actually has.
