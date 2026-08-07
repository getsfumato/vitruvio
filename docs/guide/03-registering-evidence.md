# 3. Registering evidence

```bash
vitruvio source register paper.pdf --media-type application/pdf --normalize-with pdf-text
vitruvio source register notes.md --origin "https://example.com/notes" --license CC-BY-4.0
vitruvio source replace paper-v2.pdf --supersedes <BLOCK_ID>
```

## The media type is load-bearing

It is recorded in the block, it travels with the artifact, and it is what **both** normalization dispatch and
projection read. A Markdown file filed as `application/octet-stream` is a file no text pipeline will offer to
normalise and no analyzer will read as prose.

`mimetypes` does not know `.md`, so vitruvio carries a small table of extensions a knowledge brain meets constantly
(`.md`, `.rst`, `.tex`, `.jsonl`, `.yaml`, `.toml`, `.webp`, `.avif`). Declaring `--media-type` is still better: an
extension is a claim, not evidence.

## Normalized views

A **normalized view** is a deterministic, content-addressed rendering of a source, produced by a named and versioned
pipeline, and recorded in provenance. It exists to be *read* — by a proposer, by an analyzer, by a person.

```bash
vitruvio ingest pipelines
```

| pipeline | accepts |
|---|---|
| `text` | text/plain, text/csv, text/tab-separated-values |
| `markdown` | text/markdown |
| `html-text` | text/html, application/xhtml+xml |
| `svg-text` | image/svg+xml |
| `json-canonical` | application/json and the `+json` family |
| `pdf-text` | application/pdf — needs `[vision]` |

Because a view is evidence, one requirement dominates every implementation choice: **the same input and the same
pipeline version must produce identical bytes on every machine.** Which is why:

- The HTML extractor is `html.parser` from the standard library, not BeautifulSoup or lxml. Both are better parsers
  and both change their whitespace and entity handling between releases, which would move a view's digest on a
  dependency bump.
- Nothing reflows prose, fixes spelling or normalises quotes. Those are *interpretations*, and an interpretation
  belongs in semantic memory as a cited proposal.
- **Raster images have no pipeline at all.** A "normalized PNG" is a re-encode, and re-encoding is not reproducible
  across libpng versions. Vision embeddings read the original blob instead — the bytes that were observed are the
  evidence. SVG is the exception only because it is text.

## Re-registering is free

Content is addressed by its hash, so registering the same bytes twice produces one block and reports
`duplicate: true`. This is what makes an ingestion pipeline safe to re-run.

## No in-place edit

There is none, anywhere. A newer edition of a source is a **new block plus a supersession edge**, which is what
`source replace` does. Immutability is what lets a root mean something.

## Next

[4. Indices](04-indices.md)
