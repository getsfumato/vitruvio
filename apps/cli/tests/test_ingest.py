"""The task lifecycle and `ingest run`, driven the way an agent drives them: through the JSON envelope.

The most important assertions here are about **exit codes**, not payloads. An automated caller's whole decision
procedure is "may I retry, and with what changed", and for this group the answer hinges on one distinction: a
malformed proposal is exit 7 (repair it), and everything else is not the proposal's fault.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vitruvio.cli.main import main
from vitruvio.kernel import ExitCode

DOCUMENT = """# Serie de Fourier

Descompone una funcion periodica en una suma de senos y cosenos.

# Ortogonalidad

Las funciones seno y coseno forman un sistema ortogonal en el intervalo.

```bash
# esto es un comentario de shell, no un heading
echo hola
```
"""


def envelope(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the CLI in JSON mode and parse the single object it printed."""
    code = main(["--json", *args])
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def brain(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> Path:
    """An initialised brain."""
    path = tmp_path / "brain"
    code, _ = envelope(capsys, "brain", "init", str(path), "--actor", "tester@example.com")
    assert code == ExitCode.OK
    return path


@pytest.fixture
def document(tmp_path: Path) -> Path:
    """A Markdown document with three headings, one of them inside a code fence."""
    path = tmp_path / "fourier.md"
    path.write_text(DOCUMENT, encoding="utf-8")
    return path


class TestPipelines:
    def test_every_pipeline_is_listed_with_its_availability(
        self, capsys: pytest.CaptureFixture[str], brain: Path
    ) -> None:
        """An unavailable pipeline is listed rather than hidden: "why did my PDF not get a view" needs an answer."""
        code, payload = envelope(capsys, "--brain", str(brain), "ingest", "pipelines")
        assert code == ExitCode.OK
        names = {item["name"] for item in payload["data"]["pipelines"]}
        assert {"text", "markdown", "html-text", "svg-text", "json-canonical", "pdf-text"} <= names


class TestIngestRun:
    def test_a_dry_run_proposes_and_commits_nothing(
        self, capsys: pytest.CaptureFixture[str], brain: Path, document: Path
    ) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "ingest", "run", str(document), "--dry-run")
        assert code == ExitCode.OK
        assert payload["data"]["proposed"] == 2
        assert payload["data"]["committed"] is None

        _, state = envelope(capsys, "--brain", str(brain), "brain", "state")
        assert "semantic" not in state["data"]["installed"]

    def test_the_media_type_selects_the_pipeline(
        self, capsys: pytest.CaptureFixture[str], brain: Path, document: Path
    ) -> None:
        _, payload = envelope(capsys, "--brain", str(brain), "ingest", "run", str(document), "--dry-run")
        assert payload["data"]["pipeline"] == "markdown"

    def test_a_full_run_commits_and_advances_the_snapshot(
        self, capsys: pytest.CaptureFixture[str], brain: Path, document: Path
    ) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "ingest", "run", str(document), "--subject", "fourier")
        assert code == ExitCode.OK
        assert len(payload["data"]["committed"]["committed"]) == 2

        _, state = envelope(capsys, "--brain", str(brain), "brain", "state")
        assert "semantic" in state["data"]["installed"]

    def test_re_ingesting_an_unchanged_document_is_a_no_op_not_a_failure(
        self, capsys: pytest.CaptureFixture[str], brain: Path, document: Path
    ) -> None:
        """Every candidate comes back a duplicate, which means the brain already holds them. Reporting that as a
        rejection would make the ordinary repair-and-resubmit loop fail forever after its first partial success."""
        _, first = envelope(capsys, "--brain", str(brain), "ingest", "run", str(document))
        code, second = envelope(capsys, "--brain", str(brain), "ingest", "run", str(document))
        assert code == ExitCode.OK
        assert second["data"]["already_held"] == 2
        assert second["data"]["committed"]["committed"] == []
        assert second["data"]["committed"]["snapshot"] == first["data"]["committed"]["snapshot"]

    def test_a_document_with_nothing_extractable_warns_rather_than_failing(
        self, capsys: pytest.CaptureFixture[str], brain: Path, tmp_path: Path
    ) -> None:
        """The structure proposer finding nothing is a fact about the document, not an error."""
        plain = tmp_path / "plain.md"
        plain.write_text("no headings here, just prose.\n", encoding="utf-8")
        code, payload = envelope(capsys, "--brain", str(brain), "ingest", "run", str(plain))
        assert code == ExitCode.OK
        assert payload["data"]["proposed"] == 0
        assert any("propose" in warning for warning in payload["warnings"])


class TestTaskLifecycle:
    def _source(self, capsys: pytest.CaptureFixture[str], brain: Path, document: Path) -> str:
        code, payload = envelope(capsys, "--brain", str(brain), "source", "register", str(document))
        assert code == ExitCode.OK
        return str(payload["data"]["block_id"])

    def test_define_names_the_source_and_narrows_the_memory_types(
        self, capsys: pytest.CaptureFixture[str], brain: Path, document: Path
    ) -> None:
        source = self._source(capsys, brain, document)
        code, payload = envelope(capsys, "--brain", str(brain), "task", "define", source, "--allowed", "semantic")
        assert code == ExitCode.OK
        assert payload["data"]["source"] == source
        assert payload["data"]["allowed_memory_types"] == ["semantic"]

    def test_a_task_over_evidence_the_brain_does_not_hold_is_refused(
        self, capsys: pytest.CaptureFixture[str], brain: Path
    ) -> None:
        """Otherwise a model is asked to interpret something nobody can audit."""
        code, payload = envelope(capsys, "--brain", str(brain), "task", "define", "sha256:" + "0" * 64)
        assert code != ExitCode.OK
        assert payload["ok"] is False

    def test_the_schema_is_narrowed_to_the_allowed_types(
        self, capsys: pytest.CaptureFixture[str], brain: Path, document: Path, tmp_path: Path
    ) -> None:
        """A proposal the gate would reject on shape is then not even expressible."""
        source = self._source(capsys, brain, document)
        _, task = envelope(capsys, "--brain", str(brain), "task", "define", source, "--allowed", "semantic")
        task_file = tmp_path / "task.json"
        task_file.write_text(json.dumps(task["data"]), encoding="utf-8")

        code, payload = envelope(capsys, "--brain", str(brain), "task", "schema", "--task", str(task_file))
        assert code == ExitCode.OK
        assert payload["data"]["$id"] == "boltzmann.candidates/v1"

    def test_a_float_confidence_is_exit_seven_with_a_repairable_message(
        self, capsys: pytest.CaptureFixture[str], brain: Path, document: Path, tmp_path: Path
    ) -> None:
        """The distinction the exit-code contract exists to make: the *caller's document* is wrong, which is exit 7,
        not exit 1. Letting pydantic's exception escape reported a bad payload as a bug in vitruvio."""
        source = self._source(capsys, brain, document)
        _, task = envelope(capsys, "--brain", str(brain), "task", "define", source, "--allowed", "semantic")
        task_file = tmp_path / "task.json"
        task_file.write_text(json.dumps(task["data"]), encoding="utf-8")

        candidates = tmp_path / "candidates.json"
        candidates.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "memory_type": "semantic",
                            "payload": {"kind": "fact", "label": "L", "statement": "S"},
                            "evidence": [source],
                            "confidence": 0.9,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        code, payload = envelope(
            capsys, "--brain", str(brain), "task", "validate", str(candidates), "--task", str(task_file)
        )
        assert code == ExitCode.VALIDATION
        assert payload["error"]["code"] == "CANDIDATES_REJECTED"
        assert "hash" in (payload["error"]["hint"] or "")

    def test_a_candidate_that_cites_nothing_is_refused(
        self, capsys: pytest.CaptureFixture[str], brain: Path, document: Path, tmp_path: Path
    ) -> None:
        """A derived block with no evidence has no root to audit against."""
        source = self._source(capsys, brain, document)
        _, task = envelope(capsys, "--brain", str(brain), "task", "define", source)
        task_file = tmp_path / "task.json"
        task_file.write_text(json.dumps(task["data"]), encoding="utf-8")

        candidates = tmp_path / "candidates.json"
        candidates.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "memory_type": "semantic",
                            "payload": {"kind": "fact", "label": "L", "statement": "S"},
                            "evidence": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        code, _ = envelope(capsys, "--brain", str(brain), "task", "validate", str(candidates), "--task", str(task_file))
        assert code == ExitCode.VALIDATION

    def test_a_clean_set_validates_and_commits(
        self, capsys: pytest.CaptureFixture[str], brain: Path, document: Path, tmp_path: Path
    ) -> None:
        source = self._source(capsys, brain, document)
        _, task = envelope(capsys, "--brain", str(brain), "task", "define", source, "--task-id", "batch-01")
        task_file = tmp_path / "task.json"
        task_file.write_text(json.dumps(task["data"]), encoding="utf-8")

        candidates = tmp_path / "candidates.json"
        candidates.write_text(
            json.dumps(
                {
                    "task_id": "batch-01",
                    "producer": {"kind": "model", "id": "hand-written", "version": "1"},
                    "candidates": [
                        {
                            "memory_type": "semantic",
                            "payload": {
                                "kind": "fact",
                                "label": "Teorema de Dirichlet",
                                "statement": "Da condiciones suficientes para la convergencia puntual.",
                            },
                            "evidence": [source],
                            "locator": "lines:16-19",
                            "confidence": "0.85",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        code, report = envelope(
            capsys, "--brain", str(brain), "task", "validate", str(candidates), "--task", str(task_file)
        )
        assert code == ExitCode.OK
        assert report["data"]["is_clean"] is True

        code, committed = envelope(
            capsys, "--brain", str(brain), "task", "commit", str(candidates), "--task", str(task_file)
        )
        assert code == ExitCode.OK
        assert len(committed["data"]["committed"]) == 1
        assert committed["data"]["provenance"], "a derived block without a provenance record is unauditable"

        # And again: the second commit is a no-op rather than a failure.
        code, again = envelope(
            capsys, "--brain", str(brain), "task", "commit", str(candidates), "--task", str(task_file)
        )
        assert code == ExitCode.OK
        assert again["data"]["already_held"] == 1

    def test_a_task_file_that_is_an_envelope_is_accepted(
        self, capsys: pytest.CaptureFixture[str], brain: Path, document: Path, tmp_path: Path
    ) -> None:
        """Saving `--json` output straight to a file is the obvious thing to do and it is trivially detectable, so it
        is detected rather than reported as a schema failure three layers down."""
        source = self._source(capsys, brain, document)
        code, task = envelope(capsys, "--brain", str(brain), "task", "define", source)
        task_file = tmp_path / "task.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")  # the whole envelope, not just `data`

        code, payload = envelope(capsys, "--brain", str(brain), "task", "schema", "--task", str(task_file))
        assert code == ExitCode.OK
        assert payload["data"]["$id"] == "boltzmann.candidates/v1"

    def test_a_missing_task_file_says_what_was_expected(
        self, capsys: pytest.CaptureFixture[str], brain: Path, tmp_path: Path
    ) -> None:
        code, payload = envelope(capsys, "--brain", str(brain), "task", "schema", "--task", str(tmp_path / "nope.json"))
        assert code != ExitCode.OK
        assert "task define" in (payload["error"]["hint"] or "")

    def test_rederivation_records_what_it_replaces(
        self, capsys: pytest.CaptureFixture[str], brain: Path, document: Path
    ) -> None:
        """Otherwise a better model revisiting an old document leaves two competing interpretations installed."""
        _, ingested = envelope(capsys, "--brain", str(brain), "ingest", "run", str(document))
        derived = ingested["data"]["committed"]["committed"][0]
        source = ingested["data"]["registration"]["block_id"]

        code, payload = envelope(capsys, "--brain", str(brain), "task", "define", source, "--replacing", derived)
        assert code == ExitCode.OK
        assert payload["data"]["operation"] == "rederive"
        assert any(derived in item for item in payload["data"]["requirements"])
