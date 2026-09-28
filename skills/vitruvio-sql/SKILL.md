---
name: vitruvio-sql
description: Use Vitruvio SQL to count, filter, group, and join brain knowledge or registered tabular data, including project-wide compound sql queries; use when a question needs a structured answer instead of a relevance-ranked search.
allowed-tools: Bash(vitruvio:*), Read
---

# Querying a brain with SQL

Use SQL for questions such as “how many?”, “grouped by what?”, “which blocks satisfy these conditions?” and joins
between memory modules or registered data files. Use `search` when the user wants the most relevant knowledge: a
search bundle is ranked and limited, so its rows cannot support a complete count.

Always pass `--json`, inspect `ok` and `error.code`, and read `warnings` even when the command succeeds. If a project
contains multiple brains, identify the intended project and brain before querying. Prefix a single-brain command
with `--project <PROJECT> --brain <BRAIN>` in a named project; prefix a compound command with `--project <PROJECT>`.
SQL is read-only and only accepts one query rooted in `SELECT` (a `WITH` query is allowed); it cannot run writes or
read arbitrary local files.

## Explore the schema

Install the optional engine when SQL is unavailable:

```bash
pip install 'vitruvio[sql]'
```

Ask Vitruvio for the live schema instead of guessing column names or types:

```bash
vitruvio sql --schema --json
```

Each installed memory module is a table named `semantic`, `episodic`, `procedural`, `canonical` or `provenance`.
`blocks` is the common-column union. A registered CSV, TSV or Parquet block is queryable through `data."filename"`,
and `datasets` lists available files without opening their contents.

## Write structural queries

Use literals for filters and explicit list operations for nested values. `UNNEST` produces one row per list item, so a
group over tags can count more tag occurrences than source blocks:

```bash
vitruvio sql "SELECT subject, count(*) FROM semantic WHERE kind = 'fact' GROUP BY subject" --json
vitruvio sql "SELECT tag, count(*) FROM episodic, UNNEST(tags) AS u(tag) GROUP BY tag" --json
vitruvio sql 'SELECT region, sum(amount) FROM data."ventas.csv" GROUP BY region' --json
```

Text comparisons preserve stored case. Use `lower(subject)` or `ILIKE` when the case is unknown. Timestamps are UTC.
Use `list_contains(list_column, value)` to test membership without expanding the list.

For a relationship across modules, join by the block ids stored in the relationship field:

```bash
vitruvio sql "SELECT p.label FROM procedural p, UNNEST(p.steps) AS s(step), UNNEST(step.uses) AS u(used)
              JOIN semantic c ON c.id = used WHERE c.label = 'Fourier series'" --json
```

Use `--file QUERY.sql` for longer queries, `--explain` to inspect how table names are resolved and how DuckDB will run
the query, `--limit` when returning rows, and `--verify` to prove block ids present in an `id` column.

## Keep the result honest

- Quote `verified_against` roots when reporting counts or rows, so the result is tied to the brain composition read.
- Check `hidden`, `not_installed` and `truncated`. Hidden rows are superseded or demoted; a missing module is an
  empty table, not evidence that the knowledge is absent; `truncated: true` means some returned rows were omitted.
- Use `resolvable` when counting only blocks whose content is readable. Unresolvable blocks still belong to the
  verified module root and are included by `count(*)` unless filtered out.
- A registered dataset is named in SQL, never by a filesystem path. Check `datasets` in the result to see which
  dataset id was resolved, especially when filenames are ambiguous.
- SQL over stored fields is exact for the accessible modules. `about(id, 'ethics', 0.35)` and
  `similarity(id, 'ethics')` use vector indices; such results have `exact: false`. Inspect `similarity()` before choosing
  an `about()` threshold, and report the scored model and any unscored modules from `approximate`.
- When `exact` is false for another reason, or any warning is present, explain that limitation along with the
  result. Never turn an unavailable or unscored module into a zero count.

## Query several brains

Use `vitruvio compound sql` only when the question is a count or join across brains declared by the same project.
Select them with `--brains name-a,name-b` or use `--all`:

```bash
vitruvio --project PROJECT compound sql "SELECT brain, count(*) FROM semantic WHERE kind = 'fact' GROUP BY brain" --all --json
vitruvio --project PROJECT compound sql 'SELECT a.label FROM algebra.semantic a JOIN "analisis-ii".semantic b ON b.id = a.id' \
  --brains algebra,analisis-ii --json
```

A bare table such as `semantic` spans selected brains and gains a leading `brain` column. Qualify a table as
`brain_name.semantic` to read one brain; quote names with punctuation. Roots and hidden counts are keyed by
`brain.module`. A shared block occurs once per brain, so use `count(DISTINCT id)` to count identities once. Dataset
tables use the same qualifier, for example `shop.data."ventas.csv"`.

For per-brain relevance search and help choosing brains in a project, use `vitruvio-compound`. For the full SQL
reference, see the [Counting with SQL guide](https://github.com/getsfumato/vitruvio/blob/main/docs/brain/sql.mdx).
