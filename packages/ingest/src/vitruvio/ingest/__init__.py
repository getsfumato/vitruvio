"""Sources, normalization pipelines and candidate proposers: the three ways material gets into a brain.

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
* A **source** is neither, and it is worth naming its own rule rather than stretching one of theirs. A source is
  I/O against a world that changes: it may fail, it may answer differently tomorrow, and it may never be trusted
  with an unbounded operation. :mod:`vitruvio.ingest.sources` is shaped by that -- every subprocess has a timeout
  and closed stdin, every path is contained, and a declaration can name a kind but never define a command line.
"""

from __future__ import annotations

from vitruvio.ingest.media import EXTRA_MEDIA_TYPES, FALLBACK_MEDIA_TYPE, media_type_for
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
from vitruvio.ingest.sources import (
    BUILTIN as BUILTIN_SOURCES,
)
from vitruvio.ingest.sources import (
    ENTRY_POINT_GROUP,
    BaseSource,
    DirectorySource,
    FetchResult,
    Item,
    Kind,
    Source,
    kinds,
    resolve_source,
    scaffold,
)
from vitruvio.ingest.sources import (
    describe as describe_sources,
)

__all__ = [
    "BUILTIN",
    "BUILTIN_SOURCES",
    "ENTRY_POINT_GROUP",
    "EXTRA_MEDIA_TYPES",
    "FALLBACK_MEDIA_TYPE",
    "PROPOSERS",
    "TEXT_MEDIA_TYPE",
    "AnthropicProposer",
    "BaseSource",
    "DirectorySource",
    "FetchResult",
    "Item",
    "Kind",
    "HtmlPipeline",
    "JsonPipeline",
    "MarkdownPipeline",
    "OpenAIProposer",
    "PdfTextPipeline",
    "StructureProposer",
    "Source",
    "SvgTextPipeline",
    "TextPipeline",
    "bootstrap",
    "describe",
    "describe_sources",
    "kinds",
    "media_type_for",
    "resolve",
    "resolve_source",
    "scaffold",
    "suggest",
]
