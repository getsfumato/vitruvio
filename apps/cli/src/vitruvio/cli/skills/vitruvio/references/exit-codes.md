# Exit codes

Append-only, never reassigned. Each one exists because it answers "should I retry, and with what changed"
differently — which is the only question an automated caller actually has.

| code | name | meaning | retry? |
|---|---|---|---|
| 0 | OK | success | — |
| 1 | INTERNAL | a bug in vitruvio | no — report it |
| 2 | USAGE | you asked wrong | rephrase |
| 3 | CONFIG | no brain selected, or the configuration is invalid | fix config or pass `--brain` |
| 4 | NOT_FOUND | the thing named does not exist | no |
| 5 | PROTOCOL | verification, membership or integrity failure | **no** |
| 6 | POLICY | refused by a policy the brain declares: retention, or `publish = false` | **no** |
| 7 | VALIDATION | candidates rejected | repair and retry |
| 8 | NOT_FAST_FORWARD | the histories diverged | pull, re-commit, push |
| 9 | REGISTRY | registry unreachable or refused | yes |
| 10 | REVIEW | the cascade needs human review | ask a person |
| 11 | SOURCE | a declared source was unreachable or refused | yes |

## The distinctions that matter

**2 vs 7.** Both are "your fault". 2 means the *command* was malformed — a flag that does not exist, a memory type
that is not one. 7 means the command was fine and the *document you supplied* was wrong. Only 7 is worth a repair
loop.

**5 and 6 are terminal.** 5 means a claim about the data failed: a Merkle root did not match, a block is not a
member, a blob does not hash to its digest. 6 means a rule the brain declares forbids what was asked — episodic
memory is append-only, canonical drops need permission, a brain marked `publish = false` is not republished. Retrying either is pointless, and a caller that retries on
5 is retrying against corruption.

**9 vs 11.** Both are "the world was uncooperative", and both are worth retrying. 9 is a registry, which is where a
brain *goes*; 11 is a source, which is where material *comes from*. Neither is 3: a source whose tool is missing or
whose host is down will work later, and a source whose declaration is wrong will not work until a file is edited.

**10 is not an error.** It is the protocol asking for a human, because the cascade exceeded the policy's review
threshold. Answering on the human's behalf defeats the mechanism that produced the exit code.

**1 should never happen.** If you see it, the failure was not anticipated, and that is worth reporting rather than
working around. Note that cyclopts exits 1 on a usage error by default; vitruvio remaps that to 2 precisely so that
1 keeps meaning "a bug".
