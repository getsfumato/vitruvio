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


class TestSelectionByProjectAndBrain:
    """`--project` and `--brain` together, from outside any project.

    This is the pair the CLI is now expected to be driven by: several agents, several projects, several brains,
    at once. Every test here runs from a directory with no vitruvio.toml above it, because depending on the
    working directory is the thing being replaced.
    """

    @pytest.fixture
    def second(self, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """An `eticompass` project holding a brain whose name also exists in `facultad`."""
        root = tmp_path / "eticompass"
        root.mkdir()
        monkeypatch.chdir(root)
        # `--config` explicitly: the `project` fixture put a vitruvio.toml in tmp_path, and the walk-up would find
        # *that* one from a subdirectory of it -- correctly, since a project inside a project is one project.
        config = str(root / "vitruvio.toml")
        for args in (
            ("project", "init", "eticompass"),
            ("project", "add", "metrica-a"),
            ("project", "add", "algebra"),
        ):
            assert envelope(capsys, "--actor", "a@b.c", "--config", config, *args)[0] == ExitCode.OK
        return root

    def test_init_registers_the_project_so_the_flag_works_immediately(
        self, capsys: pytest.CaptureFixture[str], project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        code, payload = envelope(capsys, "--project", "facultad", "--brain", "algebra", "brain", "state")
        assert code == ExitCode.OK
        assert payload["data"]["brain"] == str(project / "brains" / "algebra")

    def test_two_projects_holding_the_same_brain_name_stay_apart(
        self,
        capsys: pytest.CaptureFixture[str],
        project: Path,
        second: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The failure this whole change is about: `--brain algebra` means two different brains, and which one
        it means is the project's business rather than the working directory's."""
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        _, first = envelope(capsys, "--project", "facultad", "--brain", "algebra", "brain", "state")
        _, other = envelope(capsys, "--project", "eticompass", "--brain", "algebra", "brain", "state")
        assert first["data"]["brain"] == str(project / "brains" / "algebra")
        assert other["data"]["brain"] == str(second / "brains" / "algebra")

    def test_project_list_names_every_project_and_its_brains(
        self, capsys: pytest.CaptureFixture[str], project: Path, second: Path
    ) -> None:
        code, payload = envelope(capsys, "project", "list")
        assert code == ExitCode.OK
        listed = {entry["name"]: entry for entry in payload["data"]["projects"]}
        assert set(listed) == {"facultad", "eticompass"}
        assert listed["eticompass"]["brains"] == ["algebra", "metrica-a"]

    def test_an_unregistered_project_is_refused_by_name(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code, payload = envelope(capsys, "--project", "telepatia", "brain", "state")
        assert code == ExitCode.CONFIG
        assert payload["error"]["code"] == "PROJECT_NOT_KNOWN"
        assert "facultad" in payload["error"]["message"], "the refusal lists what is registered"

    def test_register_makes_a_cloned_project_addressable(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A project that arrived by `git clone` was never `init`ed on this machine, so this is the path for it."""
        clone = tmp_path / "clone"
        clone.mkdir()
        (clone / "vitruvio.toml").write_text(
            '[project]\nname = "clonado"\n\n[brains.uno]\npath = "./brains/uno"\n', encoding="utf-8"
        )
        monkeypatch.chdir(clone)
        code, payload = envelope(capsys, "project", "register")
        assert code == ExitCode.OK
        assert payload["data"]["name"] == "clonado"

        monkeypatch.chdir(tmp_path)
        code, payload = envelope(capsys, "project", "list")
        assert "clonado" in {entry["name"] for entry in payload["data"]["projects"]}

    def test_forget_leaves_the_files_alone(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        code, _ = envelope(capsys, "project", "forget", "facultad")
        assert code == ExitCode.OK
        assert (project / "vitruvio.toml").is_file()
        assert (project / "brains" / "algebra").is_dir()

    def test_forgetting_something_unknown_is_an_error_rather_than_a_shrug(
        self, capsys: pytest.CaptureFixture[str], project: Path
    ) -> None:
        code, _ = envelope(capsys, "project", "forget", "telepatia")
        assert code != ExitCode.OK


class TestBrainUseIsScopedToItsProject:
    """`brain use` records a default for one project and reaches no other.

    A machine-wide pointer cannot describe two projects open at once, and it failed *silently*: the brain another
    project's `brain use` named answered here too. So the pointer is per project, and a project that declares
    brains asks by name rather than falling back to anything.
    """

    def test_a_choice_applies_to_its_own_project(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        assert envelope(capsys, "brain", "use", "algebra")[0] == ExitCode.OK
        code, payload = envelope(capsys, "brain", "state")
        assert code == ExitCode.OK
        assert payload["data"]["brain"] == str(project / "brains" / "algebra")
        assert payload["data"]["brain_origin"] == "state"

    def test_it_does_not_answer_for_another_project(
        self, capsys: pytest.CaptureFixture[str], project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert envelope(capsys, "brain", "use", "algebra")[0] == ExitCode.OK

        other = tmp_path / "eticompass"
        other.mkdir()
        monkeypatch.chdir(other)
        config = str(other / "vitruvio.toml")
        for args in (
            ("project", "init", "eticompass"),
            ("project", "add", "metrica-a"),
            ("project", "add", "metrica-b"),
        ):
            assert envelope(capsys, "--actor", "a@b.c", "--config", config, *args)[0] == ExitCode.OK

        code, payload = envelope(capsys, "--project", "eticompass", "brain", "state")
        assert code == ExitCode.CONFIG
        assert payload["error"]["code"] == "NO_BRAIN"
        assert "metrica-a" in (payload["error"]["hint"] or "")

    def test_brain_list_marks_the_project_s_own_choice(self, capsys: pytest.CaptureFixture[str], project: Path) -> None:
        envelope(capsys, "brain", "use", "analisis-ii")
        _, payload = envelope(capsys, "brain", "list")
        chosen = [item["name"] for item in payload["data"]["members"] if item["selected"]]
        assert chosen == ["analisis-ii"]


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

    def test_anonymous_reaches_every_push(
        self, capsys: pytest.CaptureFixture[str], project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--all` forwarded six of push's seven options and dropped `anonymous`, so the flag did nothing.

        Silent in the worst direction: the user asked for no credentials and got whatever was in the keyring.
        Asserted on the call, because `--local` needs no credentials and so no observable outcome differs.
        """
        from vitruvio.runtime import BrainService

        seen: list[bool] = []
        original = BrainService.push

        def spy(self: BrainService, reference: str | None = None, **kwargs: object) -> dict[str, Any]:
            seen.append(bool(kwargs.get("anonymous")))
            return original(self, reference, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(BrainService, "push", spy)
        code, _ = envelope(capsys, "dist", "push", "--all", "--anonymous", "--local", str(project / "registry"))
        assert code == ExitCode.OK
        assert seen, "no brain was pushed, so the flag was never exercised"
        assert all(seen), "every brain in an --all run must be pushed with the flag the user passed"

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

    def test_pull_exposes_the_sdk_option_to_ignore_vector_indices(
        self, capsys: pytest.CaptureFixture[str], project: Path, tmp_path: Path
    ) -> None:
        registry = project / "registry"
        assert envelope(capsys, "dist", "push", "--all", "--local", str(registry))[0] == ExitCode.OK

        consumer = tmp_path / "consumer-without-vectors"
        assert envelope(capsys, "brain", "init", str(consumer), "--actor", "c@d.e")[0] == ExitCode.OK
        code, payload = envelope(
            capsys,
            "--brain",
            str(consumer),
            "dist",
            "pull",
            "docker.io/alex/facultad-algebra",
            "--ignore-vector-indices",
            "--local",
            str(registry),
        )

        assert code == ExitCode.OK
        assert "ignored_vector_indices" in payload["data"]


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
