"""The `project` group, and publishing a project's brains to their own repositories.

Every push here goes to a filesystem registry: the derivation of a repository is what is under test, not whether a
network works, and a suite that needed credentials would be a suite nobody runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vitruvio.cli.main import main
from vitruvio.kernel import ExitCode

DOCUMENT = """# Espacio vectorial

Un espacio vectorial sobre un cuerpo K es un conjunto con suma y producto por escalar.

# Base y dimension

Una base es un conjunto linealmente independiente que genera todo el espacio.
"""


def envelope(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the CLI in JSON mode and parse the single object it printed."""
    code = main(["--json", *args])
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def project(capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `facultad` project with two subjects, one of which holds knowledge."""
    monkeypatch.chdir(tmp_path)
    code, _ = envelope(capsys, "--actor", "a@b.c", "project", "init", "facultad", "--namespace", "docker.io/alex")
    assert code == ExitCode.OK
    assert envelope(capsys, "--actor", "a@b.c", "project", "add", "algebra")[0] == ExitCode.OK
    assert envelope(capsys, "--actor", "a@b.c", "project", "add", "analisis-ii")[0] == ExitCode.OK

    document = tmp_path / "algebra.md"
    document.write_text(DOCUMENT, encoding="utf-8")
    code, _ = envelope(capsys, "--actor", "a@b.c", "--brain", "algebra", "ingest", "run", str(document))
    assert code == ExitCode.OK
    return tmp_path


class TestInit:
    def test_init_writes_a_project(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        code, payload = envelope(capsys, "project", "init", "facultad", "--namespace", "docker.io/alex")
        assert code == ExitCode.OK
        assert Path(payload["data"]["config_file"]) == tmp_path / "vitruvio.toml"
        assert "facultad" in (tmp_path / "vitruvio.toml").read_text(encoding="utf-8")

    def test_a_name_that_cannot_be_a_repository_is_refused_before_anything_is_written(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rejected name must not leave a half-made project behind."""
        monkeypatch.chdir(tmp_path)
        code, _ = envelope(capsys, "project", "init", "Facultad 2026")
        assert code != ExitCode.OK
        assert not (tmp_path / "vitruvio.toml").exists()

    def test_init_refuses_to_rename_an_existing_project(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code, payload = envelope(capsys, "project", "init", "otra")
        assert code != ExitCode.OK
        assert "facultad" in payload["error"]["message"]


class TestAddAndShow:
    def test_add_creates_the_layout_and_registers_it(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, payload = envelope(capsys, "--actor", "a@b.c", "project", "add", "fisica-i")
        assert code == ExitCode.OK
        assert payload["data"]["created"] is True
        assert (project / "brains" / "fisica-i" / "oci-layout").is_file()

    def test_show_reports_where_each_brain_publishes(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        """The repository column is the whole destination, and is worth reading before a first push."""
        code, payload = envelope(capsys, "project", "show")
        assert code == ExitCode.OK
        repositories = {item["name"]: item["repository"] for item in payload["data"]["brains"]}
        assert repositories == {
            "algebra": "docker.io/alex/facultad-algebra",
            "analisis-ii": "docker.io/alex/facultad-analisis-ii",
        }

    def test_show_works_on_a_project_with_no_brains(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The state a new project is in, and the one `show` most needs to be able to report."""
        monkeypatch.chdir(tmp_path)
        envelope(capsys, "project", "init", "vacio")
        code, payload = envelope(capsys, "project", "show")
        assert code == ExitCode.OK
        assert payload["data"]["brains"] == []
        assert any("no brains" in warning for warning in payload["warnings"])

    def test_a_duplicate_name_is_refused(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, _ = envelope(capsys, "--actor", "a@b.c", "project", "add", "algebra")
        assert code != ExitCode.OK

    def test_remove_unregisters_without_deleting(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        """ "Remove it from this project" and "destroy it" are different requests, and a brain may be the only
        copy of what it holds."""
        code, payload = envelope(capsys, "project", "remove", "analisis-ii")
        assert code == ExitCode.OK
        assert Path(payload["data"]["path"]).is_dir(), "the layout must survive"

        _, shown = envelope(capsys, "project", "show")
        assert [item["name"] for item in shown["data"]["brains"]] == ["algebra"]


class TestSelectionByName:
    def test_a_brain_is_selected_by_its_project_name(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, payload = envelope(capsys, "--brain", "algebra", "brain", "state")
        assert code == ExitCode.OK
        assert payload["data"]["brain"] == str(project / "brains" / "algebra")

    def test_the_wrong_brain_is_not_written_to(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        """The fixture ingested into `algebra` only, so `analisis-ii` must still be empty -- which is the check
        that a name resolved to the brain it names."""
        _, algebra = envelope(capsys, "--brain", "algebra", "brain", "state")
        _, analisis = envelope(capsys, "--brain", "analisis-ii", "brain", "state")
        assert algebra["data"]["block_count"] > 0
        assert analisis["data"]["block_count"] == 0

    def test_brain_list_shows_the_project_members(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, payload = envelope(capsys, "brain", "list")
        assert code == ExitCode.OK
        assert [item["name"] for item in payload["data"]["members"]] == ["algebra", "analisis-ii"]
        assert payload["data"]["project"] == "facultad"


class TestPushAll:
    def test_each_brain_goes_to_its_own_repository(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, payload = envelope(capsys, "dist", "push", "--all", "--local", str(project / "registry"))
        assert code == ExitCode.OK
        published = {item["brain"]: item.get("reference") for item in payload["data"]["brains"] if not item["skipped"]}
        assert published == {"algebra": "docker.io/alex/facultad-algebra"}

    def test_an_empty_brain_is_skipped_rather_than_failed(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        """A project where one subject has not been started yet is the ordinary state, and letting it come back
        as a failure would make `--all` exit non-zero on a perfectly healthy project."""
        code, payload = envelope(capsys, "dist", "push", "--all", "--local", str(project / "registry"))
        assert code == ExitCode.OK
        assert payload["data"]["skipped"] == 1
        skipped = [item["brain"] for item in payload["data"]["brains"] if item["skipped"]]
        assert skipped == ["analisis-ii"]

    def test_all_refuses_a_reference_because_it_names_one_repository(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code, payload = envelope(
            capsys, "dist", "push", "--all", "docker.io/alex/one", "--local", str(project / "registry")
        )
        assert code != ExitCode.OK
        assert "derives its own" in (payload["error"]["hint"] or "")

    def test_a_published_brain_can_be_pulled_back(
        self, capsys: pytest.CaptureFixture[str], project: Path, tmp_path: Path
    ) -> None:
        """The round trip is what proves the derived repository was a real destination and not just a string."""
        registry = project / "registry"
        assert envelope(capsys, "dist", "push", "--all", "--local", str(registry))[0] == ExitCode.OK

        consumer = tmp_path / "consumer"
        assert envelope(capsys, "brain", "init", str(consumer), "--actor", "c@d.e")[0] == ExitCode.OK
        code, _ = envelope(
            capsys,
            "--brain",
            str(consumer),
            "dist",
            "pull",
            "docker.io/alex/facultad-algebra",
            "--local",
            str(registry),
        )
        assert code == ExitCode.OK

        code, verified = envelope(capsys, "--brain", str(consumer), "brain", "verify")
        assert code == ExitCode.OK
        assert verified["data"]["verified"] is True


class TestSingleBrainStillWorks:
    def test_a_project_of_one_brain_needs_no_name(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The single-brain form predates projects and must keep working untouched."""
        monkeypatch.chdir(tmp_path)
        code, _ = envelope(capsys, "brain", "init", str(tmp_path / "solo"), "--actor", "a@b.c")
        assert code == ExitCode.OK

        code, payload = envelope(capsys, "brain", "state")
        assert code == ExitCode.OK
        assert payload["data"]["brain"] == str(tmp_path / "solo")


class TestPublishProhibition:
    """`publish = false`, which exists to stop one silent mistake rather than to be a permission.

    Pulling a brain gives you a writable working copy -- nothing in the protocol distinguishes a brain you authored
    from one you installed -- so a stray `dist push` publishes a fork of somebody else's brain under this project's
    repository, and the two lineages then diverge with nobody informed.
    """

    def test_a_push_is_refused_with_the_policy_exit_code(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        """Exit 6, not 2 or 3: the request was well formed and the configuration is valid, and retrying it unchanged
        cannot ever work. That is what a 6 tells a caller."""
        assert envelope(capsys, "config", "set", "brains.algebra.publish", "false")[0] == ExitCode.OK

        code = main(["--json", "--brain", "algebra", "dist", "push", "--local", str(project / "registry")])
        payload = json.loads(capsys.readouterr().out)
        assert code == ExitCode.POLICY
        assert payload["error"]["code"] == "PUBLISH_FORBIDDEN"

    def test_nothing_is_written_to_the_registry(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        """Refused before it packs, so the refusal leaves no half-published artifact and no evidence of the attempt
        in a registry somebody else reads."""
        envelope(capsys, "config", "set", "brains.algebra.publish", "false")
        registry = project / "registry"

        main(["--json", "--brain", "algebra", "dist", "push", "--local", str(registry)])
        capsys.readouterr()
        assert not registry.exists() or not any(registry.iterdir())

    def test_push_all_skips_it_instead_of_failing_the_whole_run(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        """A project holding one upstream brain is the normal shape for a team, and `--all` must not exit non-zero
        on it forever."""
        envelope(capsys, "config", "set", "brains.algebra.publish", "false")

        code, payload = envelope(capsys, "dist", "push", "--all", "--local", str(project / "registry"))
        assert code == ExitCode.OK
        skipped = [item["brain"] for item in payload["data"]["brains"] if item["skipped"]]
        assert sorted(skipped) == ["algebra", "analisis-ii"]
        assert payload["data"]["published"] == 0

    def test_project_show_says_so(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        """A prohibition nobody can see is one somebody works around by accident: without this, the repository
        column reads as a statement that a push goes there."""
        envelope(capsys, "config", "set", "brains.algebra.publish", "false")
        code, payload = envelope(capsys, "project", "show")
        assert code == ExitCode.OK
        rows = {item["name"]: item["publish"] for item in payload["data"]["brains"]}
        assert rows == {"algebra": False, "analisis-ii": True}

    def test_add_can_declare_it_up_front(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, payload = envelope(capsys, "project", "add", "compartido", "--no-publish")
        assert code == ExitCode.OK
        assert payload["data"]["publish"] is False

        from vitruvio.kernel import load_project

        assert load_project(project / "vitruvio.toml").brains["compartido"].publish is False

    def test_pulling_into_it_still_works(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        """The prohibition is on publishing, not on the brain. An upstream brain that could not be *updated* would
        be useless, which is the failure mode of getting this too broad."""
        envelope(capsys, "dist", "push", "--all", "--local", str(project / "registry"))
        envelope(capsys, "config", "set", "brains.algebra.publish", "false")

        code, _ = envelope(
            capsys,
            "--brain",
            "algebra",
            "dist",
            "plan-pull",
            "docker.io/alex/facultad-algebra",
            "--local",
            str(project / "registry"),
        )
        assert code == ExitCode.OK
