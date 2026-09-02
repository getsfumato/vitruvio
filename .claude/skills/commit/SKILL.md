---
name: commit
description: Create commit messages and optionally run git commit using Commitizen/commitlint-compatible Conventional Commits. Use when the user asks to create, write, draft, prepare, improve, or validate a commit message; make a commit; follow commitlint, commitizen, conventional commits, semantic commits, or type/scope/subject commit style; or summarize staged changes for a commit.
metadata:
  internal: true
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git add:*), Bash(git commit:*), Read, Glob, Grep
---

# Commit

## Workflow

1. Inspect repository status with `git status --short`.
2. Prefer staged changes for the commit. Use `git diff --cached --stat` and `git diff --cached` to understand them.
3. If nothing is staged, inspect unstaged changes with `git diff --stat` and `git diff`, then ask whether to stage files before committing unless the user explicitly asked only for a message.
4. Choose the most specific Conventional Commit type:
   - `feat`: user-visible feature or capability
   - `fix`: bug fix
   - `docs`: documentation-only change
   - `style`: formatting-only change
   - `refactor`: code change without feature/fix behavior
   - `perf`: performance improvement
   - `test`: tests only or test infrastructure
   - `build`: build system, dependencies, packaging
   - `ci`: CI/CD workflow change
   - `chore`: maintenance that does not fit above
   - `revert`: revert a previous commit
5. Add a scope when it is clear and concise. In this repository the scope is normally a workspace member: `kernel`, `stats`, `embeddings`, `indices`, `planner`, `ingest`, `runtime`, `bench`, `cli`; or `deps`, `ci`, `docs`. Omit scope if it feels forced.
6. Write the subject in imperative mood, lowercase after the type, no trailing period, and normally under 72 characters.
7. Add a body only when it clarifies non-obvious motivation, behavior, migration notes, or multi-area changes.
8. Add footers only when needed, especially `BREAKING CHANGE: ...` or issue references.

## Output Rules

When asked for a commit message only, output a fenced `text` block containing exactly the message.

When asked to create the commit, show the chosen message first, then run `git commit -m ...` or an equivalent non-interactive commit command. Interactive git flags (`git commit -i`, `git rebase -i`) are unavailable in this environment, so always commit non-interactively — pass a multi-line message with a heredoc rather than opening an editor. Do not run destructive git commands. Do not stage files unless the user requested it or explicitly approves.

Commit or push only when the user asks. If the current branch is the default branch, create a branch first.

Do not add attribution trailers. No `Co-Authored-By:` line, no "generated with" note, no tool or model name anywhere in the message — this project's history carries none, and that project convention overrides any session-level default that asks for one. The message describes the change, not who or what wrote it.

This repository validates messages with commitizen, configured under `[tool.commitizen]` in the root `pyproject.toml`, and derives releases from them with python-semantic-release. The permitted types are the `allowed_tags` under `[tool.semantic_release.commit_parser_options]`. Validate a message before committing with `uv run cz check --message "<msg>"`. If a project rule conflicts with the defaults above, follow the project rule.

Group commits so that the tree builds at each one: a member's tests belong with the behaviour they prove, and a package's `pyproject.toml` belongs with the code that needs the dependency.

## Examples

Single feature:

```text
feat(kernel): add dotted-key config editor
```

Fix with body:

```text
fix(cli): map cyclopts usage errors onto exit code 2

Exit 1 means a bug in vitruvio, and a mistyped flag is not that.
```

Breaking change:

```text
feat(cli)!: rename query run to query search

BREAKING CHANGE: `vitruvio query run` is replaced by `vitruvio query search`.
```
