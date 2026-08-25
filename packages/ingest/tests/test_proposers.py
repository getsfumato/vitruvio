"""The proposers: what they propose, and -- more importantly -- what they refuse to.

The API proposers are tested without a network. Their interesting behaviour is not the HTTP call, it is the request
they build and the answers they discard, and both are inspectable without one.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from boltzmann.blocks.memory_type import MemoryType
from boltzmann.identity.digest import BlockId
from boltzmann.ingest.task import ProcessingTask, TaskOperation

from vitruvio.ingest import AnthropicProposer, OpenAIProposer, StructureProposer, resolve
from vitruvio.kernel import VitruvioError

SOURCE = BlockId.of(b"a document")

DOCUMENT = b"""# Serie de Fourier

Descompone una funcion periodica en senos y cosenos.

# Ortogonalidad

Las funciones seno y coseno forman un sistema ortogonal.

```bash
# esto es un comentario de shell
echo hola
```

# Sin cuerpo
"""


def a_task(*allowed: MemoryType) -> ProcessingTask:
    """A task over ``SOURCE``, allowing the given memory types."""
    return ProcessingTask(
        operation=TaskOperation.EXTRACT_KNOWLEDGE,
        source=SOURCE,
        allowed_memory_types=sorted(allowed or (MemoryType.SEMANTIC,)),
        task_id="batch-01",
    )


class TestStructureProposer:
    def test_one_candidate_per_section_with_content(self) -> None:
        """ "Sin cuerpo" has a heading and nothing under it, so it is not a section -- proposing it would assert a
        title as if it were a fact."""
        result = StructureProposer()(a_task(), DOCUMENT)
        assert [candidate.payload["label"] for candidate in result.candidates] == [
            "Serie de Fourier",
            "Ortogonalidad",
        ]

    def test_a_comment_inside_a_code_fence_is_not_a_heading(self) -> None:
        """`# echo hola` looks exactly like an H1. Reading it as one produces a block asserting a shell comment."""
        labels = [item.payload["label"] for item in StructureProposer()(a_task(), DOCUMENT).candidates]
        assert "esto es un comentario de shell" not in labels

    def test_every_candidate_cites_the_source(self) -> None:
        """A derived block with no evidence has no root to audit against, and the source is always citable."""
        for candidate in StructureProposer()(a_task(), DOCUMENT).candidates:
            assert candidate.evidence == [SOURCE]

    def test_the_locator_names_a_line_range(self) -> None:
        """Which is what makes a citation point into the document rather than at it."""
        first = StructureProposer()(a_task(), DOCUMENT).candidates[0]
        assert first.locator is not None
        assert first.locator.startswith("lines:")

    def test_confidence_is_a_string(self) -> None:
        """The protocol forbids floats in anything it hashes, and a proposer is where that rule gets broken."""
        for candidate in StructureProposer()(a_task(), DOCUMENT).candidates:
            assert isinstance(candidate.confidence, str)

    def test_the_producer_is_a_pipeline_not_a_model(self) -> None:
        """No model ran. The distinction is what lets "drop everything model X derived" leave these alone."""
        result = StructureProposer()(a_task(), DOCUMENT)
        assert result.producer is not None
        assert result.producer.kind.value == "pipeline"

    def test_it_proposes_nothing_when_semantic_is_not_allowed(self) -> None:
        """Rather than proposing the wrong type. Deciding a section is a procedure is a judgment a regex cannot make,
        and one that guessed would be a model with none of a model's ability."""
        assert len(StructureProposer()(a_task(MemoryType.PROCEDURAL), DOCUMENT).candidates) == 0

    def test_the_subject_is_carried_when_given(self) -> None:
        """It is what makes a later subject filter select this document rather than everything."""
        result = StructureProposer(subject="fourier")(a_task(), DOCUMENT)
        assert all(candidate.payload["subject"] == "fourier" for candidate in result.candidates)

    def test_statements_are_extractive(self) -> None:
        """Every claim is text that was in the source, which is what makes this proposer unable to invent."""
        text = DOCUMENT.decode()
        for candidate in StructureProposer()(a_task(), DOCUMENT).candidates:
            assert candidate.payload["statement"] in text

    def test_it_is_deterministic(self) -> None:
        first = StructureProposer()(a_task(), DOCUMENT)
        second = StructureProposer()(a_task(), DOCUMENT)
        assert first == second


class TestApiProposers:
    def test_the_anthropic_request_forces_the_task_schema_as_a_tool(self) -> None:
        """A tool rather than a text response, so a malformed candidate set is the provider's error rather than a
        parse failure here."""
        request = AnthropicProposer(api_key="test").build(a_task(), "text")
        assert request["tool_choice"] == {"type": "tool", "name": "propose_candidates"}
        assert request["tools"][0]["input_schema"]["$id"] == "boltzmann.candidates/v1"

    def test_the_openai_request_asks_for_the_schema_as_structured_output(self) -> None:
        request = OpenAIProposer(api_key="test").build(a_task(), "text")
        assert request["response_format"]["type"] == "json_schema"
        structured = request["response_format"]["json_schema"]
        assert structured["strict"] is True
        schema = structured["schema"]
        assert set(schema["properties"]) == {"candidates"}, "producer identity is supplied locally, never by the model"

        def assert_strict(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    assert_strict(item)
                return
            if not isinstance(node, dict):
                return
            assert "oneOf" not in node
            assert "default" not in node
            if isinstance(node.get("properties"), dict):
                assert node["additionalProperties"] is False
                assert node["required"] == list(node["properties"])
            for value in node.values():
                assert_strict(value)

        assert_strict(schema)
        locator = schema["properties"]["candidates"]["items"]["properties"]["locator"]
        assert {option.get("type") for option in locator["anyOf"]} >= {"string", "null"}

        multi_request = OpenAIProposer(api_key="test").build(
            a_task(MemoryType.EPISODIC, MemoryType.PROCEDURAL, MemoryType.SEMANTIC), "text"
        )
        multi_schema = multi_request["response_format"]["json_schema"]["schema"]
        variants = multi_schema["properties"]["candidates"]["items"]
        assert len(variants["anyOf"]) == 3
        assert_strict(multi_schema)

    def test_the_prompt_forbids_floats_and_empty_evidence(self) -> None:
        """Stated as prohibitions on purpose: a model asked nicely to avoid floats emits floats."""
        prompt = AnthropicProposer(api_key="test").prompt(a_task(), "text")
        assert "Never empty, never invented" in prompt
        assert str(SOURCE) in prompt
        assert "No numbers anywhere in a payload" in prompt

    def test_a_proposal_that_cites_nothing_is_dropped(self) -> None:
        """Dropped here rather than rejected two steps later. There is no case where a proposer legitimately
        cannot cite the source."""
        task = a_task()
        proposer = AnthropicProposer(api_key="test")
        assert proposer._citable({"evidence": [str(SOURCE)]}, task) is True
        assert proposer._citable({"evidence": []}, task) is False
        assert proposer._citable({"evidence": ["sha256:" + "0" * 64]}, task) is False

    def test_a_missing_key_says_which_variable_to_set(self) -> None:
        with pytest.raises(VitruvioError) as caught:
            AnthropicProposer()._key()
        # The variable name is in the hint, which is where the next action belongs -- the message says what is wrong.
        assert "ANTHROPIC_API_KEY" in (caught.value.hint or "")

    def test_a_key_from_the_environment_is_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VITRUVIO_ANTHROPIC_API_KEY", "from-env")
        assert AnthropicProposer()._key() == "from-env"

    def test_an_openai_refusal_is_an_actionable_vitruvio_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        response = httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"refusal": "I cannot extract this document."}}
                ]
            },
            request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
        )
        monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)

        with pytest.raises(VitruvioError, match="refused to propose") as caught:
            OpenAIProposer(api_key="test")(a_task(), DOCUMENT)
        assert "revise the source" in (caught.value.hint or "")

    def test_an_invalid_openai_candidate_is_translated_to_vitruvio_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        invalid = {
            "candidates": [
                {
                    "memory_type": "telepathy",
                    "payload": {"kind": "fact", "label": "x", "statement": "x"},
                    "evidence": [str(SOURCE)],
                    "locator": None,
                    "confidence": None,
                }
            ]
        }
        response = httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(invalid), "refusal": None}}
                ]
            },
            request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
        )
        monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)

        with pytest.raises(VitruvioError, match="failed local candidate validation"):
            OpenAIProposer(api_key="test")(a_task(), DOCUMENT)

    def test_an_openai_response_without_structured_content_is_not_an_empty_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        response = httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {}}]},
            request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
        )
        monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)

        with pytest.raises(VitruvioError, match="no structured candidate content"):
            OpenAIProposer(api_key="test")(a_task(), DOCUMENT)


class TestResolve:
    def test_a_model_may_follow_a_colon(self) -> None:
        assert resolve("anthropic:claude-opus-5").model == "claude-opus-5"

    def test_the_structure_proposer_takes_no_model(self) -> None:
        """It is not one, so a colon suffix is ignored rather than passed to a constructor that would reject it."""
        assert isinstance(resolve("structure:whatever"), StructureProposer)

    def test_an_unknown_name_lists_the_options(self) -> None:
        with pytest.raises(VitruvioError) as caught:
            resolve("gemini")
        assert "gemini" in caught.value.message
        assert "structure" in (caught.value.hint or "")
