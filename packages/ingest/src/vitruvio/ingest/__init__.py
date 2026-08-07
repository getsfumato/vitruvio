"""Normalization pipelines and candidate proposers: where an external model touches a brain.

The protocol's boundary is that the LLM proposes and the protocol governs what is stored. Both sides
of that boundary are Protocols the SDK leaves empty: a ``NormalizationPipeline`` turns observed bytes
into a deterministic view, and a ``CandidateProposer`` turns a processing task into typed candidate
blocks. Nothing here writes to a Merkle DAG or an index; only ``Brain.commit`` does.

The two halves have opposite requirements, which is why they are separate modules rather than one:

* A **pipeline** must be deterministic, because its output is content-addressed evidence. Every
  implementation choice in :mod:`vitruvio.ingest.pipelines` is subordinated to that -- including the
  refusal to use a better HTML parser, and the refusal to normalise raster images at all.
* A **proposer** is allowed to be a model, because its output is a *proposal* that the validation gate
  either accepts or rejects. :mod:`vitruvio.ingest.proposers` also ships a deterministic one, which is
  what makes the whole path testable without a network.
"""

from __future__ import annotations

from vitruvio.ingest.pipelines import (
    BUILTIN,
    TEXT_MEDIA_TYPE,
    HtmlPipeline,
    JsonPipeline,
    MarkdownPipeline,
    PdfTextPipeline,
    SvgTextPipeline,
    TextPipeline,
    bootstrap,
    describe,
    suggest,
)
from vitruvio.ingest.proposers import (
    PROPOSERS,
    AnthropicProposer,
    OpenAIProposer,
    StructureProposer,
    resolve,
)

__all__ = [
    "BUILTIN",
    "PROPOSERS",
    "TEXT_MEDIA_TYPE",
    "AnthropicProposer",
    "HtmlPipeline",
    "JsonPipeline",
    "MarkdownPipeline",
    "OpenAIProposer",
    "PdfTextPipeline",
    "StructureProposer",
    "SvgTextPipeline",
    "TextPipeline",
    "bootstrap",
    "describe",
    "resolve",
    "suggest",
]
