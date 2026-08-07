# The JSON envelope

Every command with `--json` writes exactly one object to stdout and nothing else. Progress, warnings and logs go to
stderr, so a pipe into `jq` never needs a filter.

```json
{
  "vitruvio": "0.1.0",
  "command": "query.search",
  "ok": true,
  "data": {},
  "warnings": [],
  "error": null
}
```

| field | meaning |
|---|---|
| `vitruvio` | the version that produced this |
| `command` | a stable dotted operation name — `brain.state`, `task.validate`, `dist.push` |
| `ok` | whether the operation succeeded |
| `data` | the result, shaped per command |
| `warnings` | notes that did not prevent success |
| `error` | `null` on success, else `{code, kind, message, hint}` |

The top level never varies, which is what lets a caller branch on `ok` and `error.code` without knowing which of
the forty-odd commands it ran.

## `warnings` is present on success

This is the field to read even when `ok` is true. A degraded answer that looks identical to a clean one is the
failure mode the whole design is arranged to prevent, so degradations are reported rather than smoothed over:

- `the vector index will not be published -- <reason>` — a publish that nobody else can search semantically.
- `stats are stale` — selectivity estimates were pessimistic and the plan cache was bypassed.
- `a selective install leaves the other modules missing` — expected, and permanent until you pull them.
- `no system keyring is available, so the token is stored in plain text at <path>` — act on this one.

In human mode a warning goes to stderr as it happens. In JSON mode it goes into the envelope instead, because a
machine reading stdout would never see stderr.

## `error`

```json
{ "code": "CANDIDATES_REJECTED", "kind": "CandidatesRejectedError",
  "message": "the candidate set does not match boltzmann.candidates/v1 -- candidates.1.confidence: Input should be a valid string",
  "hint": "`confidence` is a decimal *string* ..." }
```

`code` is stable and machine-readable; branch on it. `hint` is the next action when one exists — it is usually the
most useful field in the object, and it is where a flag or an environment variable name will be named.

## Two things that are strings on purpose

- **`score`** in an evidence bundle. It is a decimal string in the protocol. Parsing it to a float to reformat it
  invents precision the protocol deliberately does not carry.
- **`confidence`** in a candidate. These documents are hashed, and a float does not hash reproducibly across
  machines.
