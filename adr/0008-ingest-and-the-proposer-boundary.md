# ADR-0008: Normalization Determinism, And The Proposer Boundary

**Decision status:** Accepted, implemented in M9.

## Context

Ingest is the only place an external model touches a brain, and the SDK leaves both sides of that boundary as empty
Protocols. The two sides have **opposite** requirements, which is the whole reason they are separate modules rather
than one:

- A `NormalizationPipeline` produces content-addressed *evidence*, so it must be deterministic.
- A `CandidateProposer` produces *proposals*, so it is allowed to be a model — the validation gate is what makes that
  safe.

Collapsing them would put a model's output where a digest is expected, which is exactly the confusion the protocol's
Section 7.1 exists to prevent.

## Decision

### Pipelines are subordinated to byte-reproducibility

Same original, same pipeline version, same bytes — on every machine, or the view's digest differs between clients and
stops being citable. Three consequences, each of which rejects a better-looking option:

- **`html.parser` from the standard library**, not BeautifulSoup or lxml. Both are better parsers and both change
  whitespace and entity handling between releases, which would move a view's digest on a dependency bump.
- **No prose reflowing, no smart quotes, no spell correction.** Those are interpretations, and an interpretation is a
  cited proposal for semantic memory, not a view of canonical evidence.
- **No pipeline for raster images.** A "normalized PNG" is a re-encode, and re-encoding is not reproducible across
  libpng or Pillow versions. Vision embeddings read the original blob — the bytes that were observed are the evidence.
  SVG is the exception, and only because it is text: its labels are the signal and extracting them is a string
  operation.

Two smaller decisions that were found by running it: `fold()` strips *newlines* at the edges rather than calling
`strip()`, because four leading spaces is a Markdown code block and a plain strip silently turns a code sample into a
paragraph. And undecodable bytes are **replaced** rather than refused — a document that is 99% clean UTF-8 with one bad
byte is still evidence worth citing, and replacement is deterministic, which is the property that matters.

`bootstrap()` registers the pipelines from the runtime's assembly rather than as an import side effect. Which pipelines
exist decides whether a recorded view can be *reproduced*, so the set must not depend on which modules happened to be
imported first.

### The deterministic proposer is not a placeholder

`StructureProposer` reads Markdown headings and proposes one semantic block per section, extractively: every statement
it emits is text that was in the source, so it cannot invent. It exists for two reasons, and the second is the more
important one:

1. It is a real answer for a well-structured document.
2. It makes the entire ingest path testable end to end without a network. "This document yields these three blocks" is
   an assertion here; against a model it would be a hope.

It tracks code fences, because `# do the thing` inside a fenced block is indistinguishable from an H1 by pattern, and a
proposer that reads it as one produces a semantic block asserting a shell comment.

What it deliberately does **not** do is classify episodic or procedural memory. Deciding that a section describes a
procedure is a judgment; a regex that guessed would be a model with none of a model's ability.

### API proposers hand the model the task's own schema

`AnthropicProposer` uses a forced tool call whose input schema *is* the candidates schema; `OpenAIProposer` uses
`response_format: json_schema`. Enforcing the shape at the provider means a malformed candidate set is a provider error
rather than a parse failure three layers in.

`httpx` directly rather than the provider SDKs: two more dependency trees to satisfy one POST each, and both change
their client surface between majors. The request is a dozen legible lines.

The prompt states its rules as **prohibitions** — no floats, evidence never empty, cite a locator — because a model
asked nicely to avoid floats emits floats. And the producer is recorded by vitruvio rather than trusted from the
response: it is what a later "drop everything this model version derived" keys on, so a model must not be able to name
itself something else.

### A malformed candidate document is exit 7, not exit 1

The most consequential fix in this milestone, and it inverted the one distinction the exit-code contract exists to make.
A candidate set with `confidence: 0.85` instead of `"0.85"` is *the caller's document being wrong* — repair and come
back. Letting pydantic's `ValidationError` escape reported it as exit 1, "a bug in vitruvio", so an automated caller had
no way to know the input was fixable. Both entry points now parse through one function that raises
`CandidatesRejectedError` with the issues as `field: problem` lines.

### A duplicate is not a blocking rejection

`commit` is stricter than the SDK's — it refuses everything if anything was rejected — with one exemption. A
**duplicate** means the brain already holds that block, so nothing is lost by proceeding. Refusing on it made the
repair-one-and-resubmit loop, which is exactly how an agent is meant to work, fail permanently after its first partial
success.

## Consequences

- Verified by running the CLI: three sections proposed from a Markdown document, the fenced `#` comment correctly not
  read as a heading, committed with provenance, indexed, searchable. Re-ingesting the unchanged document is a no-op at
  exit 0 with `already_held: 3`.
- `pdf-text` is reported as **unavailable** rather than absent when `[vision]` is not installed, so "why did my PDF not
  get a view" has an answer that names the install.
- A pipeline's version includes what decides its output — `pdf-text` carries the PDFium build — because that is what a
  provenance record needs in order to mean anything later.
