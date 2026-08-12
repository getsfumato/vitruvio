"""Candidate proposers: where an external model is allowed to speak, and where it is not.

The protocol's sentence is *the LLM proposes; the protocol governs what is stored*, and the SDK turns it into a type:
a ``Candidate`` has no ``block_id`` and no typed payload, so there is no method on the interface that could write to a
Merkle DAG. Everything here produces candidates and nothing here commits.

Two kinds of proposer live in this module, and the distinction is worth stating because it is easy to collapse:

* :class:`StructureProposer` is **deterministic**. It reads a document's own structure -- Markdown headings, mostly --
  and proposes one semantic block per section. No model, no network, no API key. It exists because it is the honest
  baseline: it makes the whole ingest path runnable and testable end to end, and it is often good enough for a
  well-structured document. Its proposals are *extractive*: every statement is text that was in the source.
* :class:`AnthropicProposer` and :class:`OpenAIProposer` call a model. They are behind the ``[api]`` extra, use
  ``httpx`` directly rather than a provider SDK, and hand the model the task's own JSON Schema as structured output --
  which is what turns "propose typed blocks" from a hope into a constraint, because the model cannot express a shape
  the validation gate would reject.

The two share a rule that is not negotiable in either: **evidence is never empty**. A derived block with no evidence
has no root to audit against, so a candidate without it is dropped here rather than rejected two steps later. The
source block is always available to cite, so there is no case where a proposer legitimately cannot.
"""

from __future__ import annotations

import json
import re
from typing import Any

from boltzmann.ingest.proposer import CandidateSet
from boltzmann.ingest.task import ProcessingTask

MAX_STATEMENT = 4000
"""Where a section's statement is cut. Long enough for a dense paragraph, short enough that one runaway section does
not become a block that is really a document."""

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$", re.MULTILINE)
"""ATX headings only. Setext (``===`` underlines) is deliberately not matched: it is ambiguous with a horizontal
rule and with table borders, and a proposer that guesses wrong invents a section that is not there."""

FENCE = re.compile(r"^(?:```|~~~)", re.MULTILINE)


def _sections(text: str) -> list[tuple[str, str, int, int]]:
    """
    Split Markdown into ``(heading, body, start_line, end_line)``, ignoring headings inside code fences.

    The fence tracking is the part that matters: a shell comment (``# do the thing``) inside a fenced block looks
    exactly like an H1, and a proposer that reads it as one produces a semantic block asserting a comment.

    Args:
        text (str): The document.

    Returns:
        list[tuple[str, str, int, int]]: One entry per section, in document order.
    """
    lines = text.split("\n")
    fenced = False
    marks: list[tuple[int, str]] = []
    for number, line in enumerate(lines):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = HEADING.match(line)
        if match:
            marks.append((number, match.group(2).strip()))

    sections: list[tuple[str, str, int, int]] = []
    for position, (number, heading) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        body = "\n".join(lines[number + 1 : end]).strip()
        if body:
            sections.append((heading, body, number + 1, end))
    return sections


class StructureProposer:
    """A deterministic proposer: one semantic block per Markdown section.

    Every statement it emits is text that was in the source, so it cannot invent -- which makes it the right thing to
    build the rest of the ingest path against. A test that asserts "this document yields these three blocks" is an
    assertion here, where against a model it would be a hope.

    What it deliberately does not do: classify episodic or procedural memory. Deciding that a section describes a
    procedure rather than a fact is a judgment, and a regex that guessed would be a model with none of a model's
    ability. Those types stay empty and a real proposer fills them.
    """

    ID = "vitruvio-structure"
    VERSION = "1"

    def __init__(self, subject: str | None = None, max_sections: int = 256) -> None:
        """
        Build the proposer.

        Args:
            subject (str | None): A subject to tag every proposal with, which is what makes ``--memory-type`` and
                subject filters useful afterwards. Usually the document's title.
            max_sections (int): A guard against a pathological document becoming thousands of blocks.
        """
        self.subject = subject
        self.max_sections = max_sections

    def __call__(self, task: ProcessingTask, source: bytes) -> CandidateSet:
        """
        Propose one semantic block per section.

        Args:
            task (ProcessingTask): What the protocol is asking for. Its ``allowed_memory_types`` is respected: asked
                for procedural memory only, this proposes nothing rather than proposing the wrong type.
            source (bytes): The canonical bytes, or their normalized view.

        Returns:
            CandidateSet: The proposals, with a ``pipeline`` producer -- not a ``model`` one, because no model ran.
                That distinction is what lets a later "drop everything model X derived" leave these alone.
        """
        from boltzmann.blocks.memory_type import MemoryType
        from boltzmann.blocks.provenance import Producer, ProducerKind
        from boltzmann.ingest.proposer import Candidate, CandidateSet

        producer = Producer(kind=ProducerKind.PIPELINE, id=self.ID, version=self.VERSION)
        if MemoryType.SEMANTIC not in task.allowed_memory_types:
            return CandidateSet(task_id=task.task_id, producer=producer, candidates=[])

        text = source.decode("utf-8", errors="replace")
        candidates = [
            Candidate(
                memory_type=MemoryType.SEMANTIC,
                payload={
                    "kind": "fact",
                    "label": heading[:200],
                    "statement": body[:MAX_STATEMENT],
                    **({"subject": self.subject} if self.subject else {}),
                },
                # The source block, always. A section cites the document it came from; there is nothing else it
                # could honestly cite, and empty evidence is not an option.
                evidence=[task.source],
                locator=f"lines:{start}-{end}",
                confidence="1.00" if len(body) <= MAX_STATEMENT else "0.90",
            )
            for heading, body, start, end in _sections(text)[: self.max_sections]
        ]
        return CandidateSet(task_id=task.task_id, producer=producer, candidates=candidates)

    def __repr__(self) -> str:
        return f"<StructureProposer {self.ID}/{self.VERSION}>"


class _ApiProposer:
    """Shared plumbing for the two HTTP providers: schema in, candidates out, no provider SDK.

    ``httpx`` directly rather than ``anthropic`` or ``openai``, for the same reason the HTML pipeline uses the
    standard library: two more dependency trees to satisfy one POST each, and both change their client surface
    between majors. The request here is a dozen lines and it is legible.
    """

    ENDPOINT = ""
    ENV = ""
    KIND = "model"

    def __init__(self, model: str, *, api_key: str | None = None, timeout: float = 120.0, max_tokens: int = 8192):
        """
        Build the proposer.

        Args:
            model (str): The model identifier, recorded as the producer id.
            api_key (str | None): The key. Read from the environment when omitted.
            timeout (float): Request timeout in seconds. Generous: a long document is a long generation.
            max_tokens (int): Output cap.
        """
        self.model = model
        self._api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens

    @property
    def available(self) -> bool:
        """Whether this can run: ``httpx`` installed and a key resolvable."""
        try:
            import httpx  # noqa: F401
        except ImportError:
            return False
        return bool(self._key(required=False))

    def _key(self, *, required: bool = True) -> str | None:
        """The API key, from the constructor or the environment."""
        import os

        from vitruvio.kernel import VitruvioError

        key = self._api_key or os.environ.get(f"VITRUVIO_{self.ENV}") or os.environ.get(self.ENV)
        if not key and required:
            raise VitruvioError(
                f"no API key for {type(self).__name__}",
                hint=f"set VITRUVIO_{self.ENV} or {self.ENV}",
            )
        return key

    def prompt(self, task: ProcessingTask, text: str) -> str:
        """
        The instruction handed to the model.

        Written to constrain rather than to encourage. The three rules that matter -- extract, do not invent; cite
        the source; no floats anywhere -- are stated as prohibitions, because a model that is asked nicely to avoid
        floats emits floats.

        Args:
            task (ProcessingTask): The task.
            text (str): The source text.

        Returns:
            str: The prompt.
        """
        allowed = ", ".join(item.value for item in task.allowed_memory_types)
        requirements = "\n".join(f"- {item}" for item in task.requirements)
        return (
            f"Extract knowledge from the document below into typed candidate blocks.\n\n"
            f"Allowed memory types: {allowed}\n"
            f"Requirements:\n{requirements}\n"
            f"{task.instructions or ''}\n\n"
            f"Hard rules:\n"
            f"- Every candidate's `evidence` must contain exactly the source block id: "
            f'["{task.source}"]. Never empty, never invented.\n'
            f'- `confidence` is a decimal *string* such as "0.85". No numbers anywhere in a payload: the protocol '
            f"hashes these documents and a float does not hash reproducibly.\n"
            f'- `locator` says where in the document the claim came from, e.g. "lines:40-58" or "[page 3]".\n'
            f"- Do not restate the document. Propose what a later reader would want to retrieve.\n"
            f"- Propose nothing rather than guessing. An empty candidate list is a valid answer.\n\n"
            f"--- document ---\n{text}"
        )

    def __call__(self, task: ProcessingTask, source: bytes) -> CandidateSet:
        """
        Ask the model, and parse its answer against the task's schema.

        Args:
            task (ProcessingTask): The task.
            source (bytes): The canonical bytes, or their normalized view.

        Returns:
            CandidateSet: The proposals.

        Raises:
            VitruvioError: If the provider refuses, or answers something that is not a candidate set. Both are the
                caller's problem to see: a proposer that swallowed a refusal and returned zero candidates would look
                exactly like a document with nothing in it.
        """
        import httpx
        from boltzmann.blocks.provenance import Producer, ProducerKind
        from boltzmann.ingest.proposer import CandidateSet

        from vitruvio.kernel import VitruvioError

        text = source.decode("utf-8", errors="replace")
        request = self.build(task, text)
        try:
            response = httpx.post(
                self.ENDPOINT,
                headers=self.headers(),
                json=request,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            detail = error.response.text[:400]
            raise VitruvioError(
                f"{type(self).__name__} refused the request: {error.response.status_code} {detail}",
                hint="check the model name and the key's scope",
            ) from error
        except httpx.HTTPError as error:
            raise VitruvioError(f"{type(self).__name__} was unreachable: {error}") from error

        payload = self.extract(response.json())
        try:
            parsed = json.loads(payload) if isinstance(payload, str) else payload
        except json.JSONDecodeError as error:
            raise VitruvioError(
                f"{type(self).__name__} answered with something that is not JSON",
                hint="the task schema was sent as structured output; the model ignored it",
            ) from error

        candidates = parsed.get("candidates", []) if isinstance(parsed, dict) else []
        # The producer is recorded here rather than trusted from the response: it is what a batch invalidation
        # ("drop everything this model version derived") keys on, so a model must not be able to name itself
        # something else.
        return CandidateSet(
            task_id=task.task_id,
            producer=Producer(kind=ProducerKind.MODEL, id=self.model, version=None),
            candidates=[item for item in candidates if self._citable(item, task)],
        )

    @staticmethod
    def _citable(candidate: Any, task: ProcessingTask) -> bool:
        """Whether a proposal cites the source. Dropped here rather than rejected two steps later."""
        return (
            isinstance(candidate, dict)
            and bool(candidate.get("evidence"))
            and str(task.source) in [str(item) for item in candidate.get("evidence", [])]
        )

    def build(self, task: ProcessingTask, text: str) -> dict[str, Any]:
        """The provider-specific request body."""
        raise NotImplementedError

    def headers(self) -> dict[str, str]:
        """The provider-specific headers."""
        raise NotImplementedError

    def extract(self, body: dict[str, Any]) -> Any:
        """The candidate set out of the provider-specific envelope."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.model} available={self.available}>"


class AnthropicProposer(_ApiProposer):
    """Claude, via a tool call whose input schema *is* the task's candidates schema.

    A tool rather than a text response, deliberately: the schema is then enforced by the provider, so a malformed
    candidate set is a provider error rather than a parse failure here. Prefilling a JSON response would get most of
    the way, but "most of the way" is how an invalid payload reaches the validation gate.
    """

    ENDPOINT = "https://api.anthropic.com/v1/messages"
    ENV = "ANTHROPIC_API_KEY"
    TOOL = "propose_candidates"

    def __init__(self, model: str = "claude-sonnet-5", **kwargs: Any) -> None:
        """
        Build the proposer.

        Args:
            model (str): The model. Defaults to the current Sonnet.
            kwargs (Any): Passed to the base.
        """
        super().__init__(model, **kwargs)
        self.schema: dict[str, Any] | None = None

    def headers(self) -> dict[str, str]:
        """Anthropic's key header and API version."""
        return {
            "x-api-key": self._key() or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def build(self, task: ProcessingTask, text: str) -> dict[str, Any]:
        """The messages request, with the candidates schema as a forced tool."""
        from boltzmann.ingest.schema import candidates_schema

        schema = self.schema or candidates_schema(task)
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "tools": [
                {"name": self.TOOL, "description": "Return the proposed candidate blocks.", "input_schema": schema}
            ],
            "tool_choice": {"type": "tool", "name": self.TOOL},
            "messages": [{"role": "user", "content": self.prompt(task, text)}],
        }

    def extract(self, body: dict[str, Any]) -> Any:
        """The tool input from the content blocks."""
        for block in body.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == self.TOOL:
                return block.get("input", {})
        return {}


class OpenAIProposer(_ApiProposer):
    """GPT, via ``response_format: json_schema`` with ``strict``.

    ``strict`` matters: without it the schema is a suggestion, and the failure it prevents -- a payload that is
    plausible and unparseable -- is the one that costs the most to debug.
    """

    ENDPOINT = "https://api.openai.com/v1/chat/completions"
    ENV = "OPENAI_API_KEY"

    def __init__(self, model: str = "gpt-4.1", **kwargs: Any) -> None:
        """
        Build the proposer.

        Args:
            model (str): The model.
            kwargs (Any): Passed to the base.
        """
        super().__init__(model, **kwargs)
        self.schema: dict[str, Any] | None = None

    def headers(self) -> dict[str, str]:
        """Bearer auth."""
        return {"authorization": f"Bearer {self._key() or ''}", "content-type": "application/json"}

    def build(self, task: ProcessingTask, text: str) -> dict[str, Any]:
        """The chat request, with the candidates schema as strict structured output."""
        from boltzmann.ingest.schema import candidates_schema

        schema = self.schema or candidates_schema(task)
        return {
            "model": self.model,
            "max_completion_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "candidates", "schema": schema, "strict": False},
            },
            "messages": [{"role": "user", "content": self.prompt(task, text)}],
        }

    def extract(self, body: dict[str, Any]) -> Any:
        """The message content, which is a JSON string."""
        choices = body.get("choices") or [{}]
        return choices[0].get("message", {}).get("content") or "{}"


PROPOSERS: dict[str, type] = {
    "structure": StructureProposer,
    "anthropic": AnthropicProposer,
    "openai": OpenAIProposer,
}
"""By the name the CLI takes for ``--proposer``."""


def resolve(name: str, **kwargs: Any) -> Any:
    """
    Build a proposer by name.

    Args:
        name (str): ``structure``, ``anthropic`` or ``openai``, optionally with a model after a colon --
            ``anthropic:claude-opus-5``.
        kwargs (Any): Passed to the constructor.

    Returns:
        Any: The proposer.

    Raises:
        VitruvioError: If the name is unknown.
    """
    from vitruvio.kernel import VitruvioError

    key, _, model = name.partition(":")
    try:
        factory = PROPOSERS[key]
    except KeyError:
        raise VitruvioError(
            f"{name!r} is not a proposer",
            hint=f"one of: {', '.join(sorted(PROPOSERS))}, optionally with a model after a colon",
        ) from None
    if model:
        kwargs["model"] = model
    if key == "structure":
        kwargs.pop("model", None)
    return factory(**kwargs)
