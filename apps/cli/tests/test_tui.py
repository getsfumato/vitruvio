"""``vitruvio browse``, the reading commands beside it, and the rendering layer both interfaces share.

Named ``test_tui`` rather than ``test_browse`` because the runtime's own browse tests already claim that
module name, and pytest imports test files by basename with no package around them.

Three things are worth a test here and the rest is not.

**The envelope did not change.** Human output now goes through Rich, and the one property that must survive
that is the one an agent depends on: ``--json`` prints one object and nothing else. A stray renderable printed
into stdout in JSON mode would be invisible in review and fatal in a pipe.

**A refusal beats a broken interface.** ``browse`` in a pipe and ``browse --json`` are both refused, because a
TUI has no output mode and drawing control codes into a file is not a fallback.

**The interface loads a brain and shows what is in it.** Driven through Textual's pilot rather than asserted on
a screenshot: what is pinned is which blocks the panes hold, not how many spaces they were drawn with.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import DataTable, Input, Tree

from vitruvio.cli.main import main
from vitruvio.cli.render import media, theme
from vitruvio.cli.tui import BrainBrowser
from vitruvio.kernel import ExitCode


@pytest.fixture
def brain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A brain holding two canonical blocks with different media types."""
    root = tmp_path / "brain"
    assert main(["--brain", str(root), "--actor", "tester@example.com", "brain", "init"]) == ExitCode.OK

    notes = tmp_path / "apuntes.md"
    notes.write_text("# Series de Fourier\n\nSenos y cosenos.\n", encoding="utf-8")
    picture = tmp_path / "pizarron.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n not really a png")

    for path, media_type in ((notes, "text/markdown"), (picture, "image/png")):
        assert main(["--brain", str(root), "source", "register", str(path), "--media-type", media_type]) == ExitCode.OK
    return root


def service_for(brain: Path) -> Any:
    """A service over ``brain``, built the way the browse command builds it."""
    from vitruvio.kernel import resolve
    from vitruvio.runtime import BrainService

    return BrainService(resolve(brain=brain))


class TestTheCommand:
    def test_browse_refuses_json_because_it_has_no_output(
        self, brain: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["--brain", str(brain), "--json", "browse"])
        payload = json.loads(capsys.readouterr().out)
        assert code == ExitCode.USAGE
        assert payload["ok"] is False
        assert "inspect blocks" in payload["error"]["hint"], "the refusal has to name what to run instead"

    def test_browse_refuses_a_stdout_that_is_not_a_terminal(self, brain: Path) -> None:
        """capsys has already replaced stdout with something that is not a tty, which is the condition."""
        assert main(["--brain", str(brain), "browse"]) == ExitCode.USAGE

    def test_a_memory_type_that_does_not_exist_is_refused_before_the_interface_opens(self, brain: Path) -> None:
        assert main(["--brain", str(brain), "browse", "--memory-type", "epistemic"]) == ExitCode.USAGE


class TestTheReadingCommands:
    def test_inspect_blocks_lists_a_module_by_what_its_blocks_say(
        self, brain: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["--brain", str(brain), "--json", "inspect", "blocks", "canonical"])
        payload = json.loads(capsys.readouterr().out)
        assert code == ExitCode.OK
        assert payload["command"] == "inspect.blocks"
        assert {row["title"] for row in payload["data"]["rows"]} == {"apuntes.md", "pizarron.png"}

    def test_inspect_blocks_filters_without_ranking(self, brain: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--brain", str(brain), "--json", "inspect", "blocks", "canonical", "--contains", "png"])
        payload = json.loads(capsys.readouterr().out)
        assert code == ExitCode.OK
        assert [row["title"] for row in payload["data"]["rows"]] == ["pizarron.png"]
        assert "score" not in json.dumps(payload["data"]["rows"]), "browsing does not rank, so it has no score"

    def test_inspect_content_exports_the_bytes_a_block_names(
        self, brain: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["--brain", str(brain), "--json", "inspect", "blocks", "canonical", "--contains", "md"])
        row = json.loads(capsys.readouterr().out)["data"]["rows"][0]
        target = tmp_path / "salida.md"
        code = main(["--brain", str(brain), "--json", "inspect", "content", row["blob"], "--out", str(target)])
        payload = json.loads(capsys.readouterr().out)
        assert code == ExitCode.OK
        assert target.read_text(encoding="utf-8").startswith("# Series de Fourier")
        assert payload["data"]["size"] == len(target.read_bytes())

    def test_inspect_links_finds_the_record_that_registered_a_block(
        self, brain: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["--brain", str(brain), "--json", "inspect", "blocks", "canonical", "--contains", "md"])
        row = json.loads(capsys.readouterr().out)["data"]["rows"][0]
        code = main(["--brain", str(brain), "--json", "inspect", "links", row["block_id"]])
        payload = json.loads(capsys.readouterr().out)
        assert code == ExitCode.OK
        assert payload["data"]["records"][0]["record"]["record_type"] == "registration"


class TestTheEnvelopeSurvivedRich:
    def test_json_mode_still_prints_exactly_one_object(self, brain: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """The whole reason human rendering is kept behind `view=`: a renderable printed here would corrupt the
        stream that an agent parses, and it would look fine to a person reading a terminal."""
        for args in (("brain", "info"), ("brain", "state"), ("inspect", "roots"), ("index", "list")):
            main(["--brain", str(brain), "--json", *args])
            assert json.loads(capsys.readouterr().out)

    def test_human_mode_prints_nothing_when_there_is_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`stack()` of nothing is an empty view, not a blank line: stdout stays empty so a pipe stays clean."""
        main(["brain", "list"])
        captured = capsys.readouterr()
        assert captured.out.strip() == ""
        assert "warning:" in captured.err

    def test_no_colour_survives_the_theme(self, brain: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Rich already drops colour when stdout is not a terminal; --no-color has to hold when it is one."""
        main(["--brain", str(brain), "--no-color", "brain", "info"])
        assert "\x1b[" not in capsys.readouterr().out


class TestTheRenderLayer:
    def test_a_digest_is_abbreviated_to_something_recognisable(self) -> None:
        assert theme.short("sha256:" + "a" * 64) == "sha256:aaaaaaaaaa"
        assert theme.short(None) == "-"

    def test_a_memory_type_keeps_one_colour_everywhere(self) -> None:
        """The CLI and the TUI read from this table, so a module cannot be blue in one and cyan in the other."""
        assert theme.kind("canonical").style == "canonical"
        assert theme.MEMORY_STYLES["canonical"] == "canonical"

    def test_every_style_a_renderer_names_exists_in_the_theme(self) -> None:
        """A missing style is not a cosmetic problem: Rich raises `MissingStyle` while *measuring* a table, so
        the command that used it fails rather than printing something plain."""
        for name in ("label", "value", "muted", "digest", "count", "ok", "bad", "warn", "score", "flag"):
            assert name in theme.THEME.styles

    def test_text_is_rendered_as_itself_and_markdown_as_markdown(self) -> None:
        from rich.markdown import Markdown

        assert isinstance(media.text(b"# Titulo", "text/markdown"), Markdown)
        assert not isinstance(media.text(b"plano", "text/plain"), Markdown)

    def test_a_media_type_a_terminal_cannot_show_says_what_to_do_instead(self) -> None:
        note = media.unsupported("video/mp4", 4096)
        assert "cannot play" in note.plain
        assert "inspect content" in note.plain

    def test_an_image_is_drawn_as_half_blocks_when_pillow_is_installed(self) -> None:
        """Skipped rather than mocked: what is being checked is that real bytes become cells, and a fake
        Pillow would check that the mock was called."""
        Image = pytest.importorskip("PIL.Image", reason="vitruvio[vision] is not installed")

        import io

        picture = Image.new("RGB", (4, 4), "red")
        buffer = io.BytesIO()
        picture.save(buffer, format="PNG")
        drawing = media.image(buffer.getvalue(), width=4)
        assert "▀" in drawing.plain  # type: ignore[union-attr]

    def test_an_image_without_pillow_names_the_extra_rather_than_failing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real = builtins.__import__

        def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "PIL":
                raise ImportError(name)
            return real(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)
        note = media.image(b"not read", width=10)
        assert "vitruvio[vision]" in note.plain  # type: ignore[union-attr]


class TestOpeningInTheDesktop:
    """A terminal draws a thumbnail; the desktop opens the page. This is the path that answers "I need to actually
    read this PDF", so what it hands the file to matters."""

    def test_the_platform_handler_is_used_rather_than_a_web_browser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """It began as `webbrowser.open`, which on macOS opens a file:// URI in Chrome. For a PDF that is not what
        anyone meant, and for a video or a spreadsheet the browser is the wrong application entirely."""
        from vitruvio.cli.render import desktop

        monkeypatch.setattr(desktop.sys, "platform", "darwin")
        monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "open" else None)
        assert desktop.opener() == ("/usr/bin/open",)

        monkeypatch.setattr(desktop.sys, "platform", "linux")
        monkeypatch.setattr(desktop.shutil, "which", lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None)
        assert desktop.opener() == ("/usr/bin/xdg-open",)

    def test_a_machine_with_no_opener_says_so_instead_of_pretending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Over SSH with no display there is nothing to open into, and silence would read as a viewer that failed
        to appear."""
        from vitruvio.cli.render import desktop

        monkeypatch.setattr(desktop.sys, "platform", "linux")
        monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
        with pytest.raises(desktop.NoOpenerError, match="xdg-utils"):
            desktop.open_path(Path("/tmp/whatever"))

    def test_the_file_is_named_after_the_origin_the_block_recorded(self) -> None:
        """A viewer whose title bar says `content.pdf` has thrown away the one piece of context the reader had."""
        from vitruvio.cli.render import desktop

        assert desktop.scratch("apuntes/clase-3.pdf", "sha256:abc").name == "clase-3.pdf"
        assert desktop.scratch(None, "sha256:abc").name == "sha256-abc"

    def test_a_block_with_no_origin_is_still_named_for_its_media_type(self) -> None:
        """A handler is chosen by *suffix*, so this is the difference between opening a PDF and opening a text
        editor full of `%PDF-1.7`.

        Reported from a brain that was pulled rather than authored: nobody had recorded an origin for any of its
        blocks, so every one of them fell back to a bare content address with no extension at all. The block always
        knows its media type -- that is part of its identity -- and the fallback used to throw it away.
        """
        from vitruvio.cli.render import desktop

        assert desktop.scratch(None, "sha256:abc", "application/pdf").name == "sha256-abc.pdf"
        assert desktop.scratch(None, "sha256:abc", "text/markdown").name == "sha256-abc.md"
        assert desktop.scratch(None, "sha256:abc", "text/plain; charset=utf-8").name == "sha256-abc.txt"
        # An origin that carries its own suffix is left alone, and one that does not is completed.
        assert desktop.scratch("apuntes.pdf", "sha256:abc", "application/pdf").name == "apuntes.pdf"
        assert desktop.scratch("apuntes", "sha256:abc", "application/pdf").name == "apuntes.pdf"
        # Bytes nobody described stay undressed rather than being given a format they may not have.
        assert desktop.extension_for(None) == ""
        assert desktop.scratch(None, "sha256:abc").name == "sha256-abc"

    def test_two_blocks_sharing_an_origin_do_not_overwrite_each_other(self) -> None:
        """A brain holding two editions of the same paper is the ordinary case, not the odd one."""
        from vitruvio.cli.render import desktop

        first = desktop.scratch("paper.pdf", "sha256:aaa")
        second = desktop.scratch("paper.pdf", "sha256:bbb")
        assert first != second
        assert first.name == second.name == "paper.pdf"

    def test_inspect_content_open_writes_the_bytes_and_hands_them_over(
        self, brain: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The launch is intercepted rather than performed: a test that opened Preview would be a test that put a
        window over whoever ran it."""
        from vitruvio.cli.render import desktop

        opened: list[Path] = []

        def record(path: Path) -> str:
            opened.append(path)
            return "open"

        monkeypatch.setattr(desktop, "open_path", record)

        main(["--brain", str(brain), "--json", "inspect", "blocks", "canonical", "--contains", "md"])
        row = json.loads(capsys.readouterr().out)["data"]["rows"][0]
        target = tmp_path / "abierto.md"
        code = main(
            ["--brain", str(brain), "--json", "inspect", "content", row["blob"], "--open", "--out", str(target)]
        )
        payload = json.loads(capsys.readouterr().out)

        assert code == ExitCode.OK
        assert payload["data"]["opened"] is True
        assert opened == [target]
        assert target.read_text(encoding="utf-8").startswith("# Series de Fourier")


class TestTheInterface:
    async def test_it_opens_on_canonical_and_lists_what_is_there(self, brain: Path) -> None:
        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            assert {row["title"] for row in app.rows} == {"apuntes.md", "pizarron.png"}
            assert app.query_one("#blocks", DataTable).row_count == 2
            assert app.selected is not None, "a row is selected on arrival, so the preview is never blank"

    async def test_the_tree_lists_every_module_including_the_ones_not_installed(self, brain: Path) -> None:
        """A module absent from this brain is a fact about this brain. Hiding it would make a selectively
        pulled brain indistinguishable from a smaller one."""
        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            labels = [str(node.label) for node in app.query_one("#modules", Tree).root.children]
            assert len(labels) == 5
            assert any("canonical" in label and "2" in label for label in labels)
            assert any("procedural" in label and "-" in label for label in labels)

    async def test_the_header_says_which_brain_and_why_it_was_chosen(self, brain: Path) -> None:
        """Four layers can select a brain and only `--brain` is visible in what was typed, so a bare `browse`
        opens *something*. "Which brain is this?" was a real question from a real session, and the path alone did
        not answer it -- every second brain on a machine is called `brain`."""
        app = BrainBrowser(service_for(brain), brain=str(brain), origin="state", name=None)
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            assert "by state" in app.sub_title
            assert brain.parent.name in app.sub_title, "the parent directory is what makes `brain` identifying"

    async def test_a_named_project_brain_is_shown_by_its_name(self, brain: Path) -> None:
        app = BrainBrowser(service_for(brain), brain=str(brain), origin="file", name="algebra")
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            assert "algebra by file" in app.sub_title

    async def test_identify_reports_the_whole_path_the_header_had_to_shorten(self, brain: Path) -> None:
        app = BrainBrowser(service_for(brain), brain=str(brain), origin="state")
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            await pilot.press("i")
            await _settle(pilot)
            said = "\n".join(note.message for note in app._notifications)
            assert str(brain) in said
            assert "selected by state" in said
            assert "canonical" in said, "which modules are installed is part of which brain this is"

    async def test_the_cursor_starts_in_the_blocks_and_not_in_the_sidebar(self, brain: Path) -> None:
        """Browsing starts by reading what is in the module you opened on, so the arrow keys have to walk the
        evidence from the first keystroke. Focusing the sidebar instead put the cursor in a list of five leaves
        where the arrows appeared to do nothing at all -- which is how this was first reported."""
        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            assert _focus(app) == "blocks"

    async def test_the_panes_can_be_walked_into_and_back_out_of(self, brain: Path) -> None:
        """A pane you can enter and not leave is the trap both of these keys exist to close."""
        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            await pilot.press("left")
            await pilot.pause()
            assert _focus(app) == "modules"
            await pilot.press("right")
            await pilot.pause()
            assert _focus(app) == "blocks"
            await pilot.press("m")
            await pilot.pause()
            assert _focus(app) == "modules"

    async def test_moving_the_sidebar_cursor_opens_that_module(self, brain: Path) -> None:
        """Highlighting a module says unambiguously what the reader wants to see. Requiring enter as well left the
        middle pane showing the previous module while the highlight said otherwise."""
        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            await pilot.press("left")
            await pilot.pause()
            assert app.kind == "canonical", "the sidebar lands on the module that is open, not on the root"
            await pilot.press("down")  # onto episodic, which this brain does not have
            await _settle(pilot)
            assert app.kind == "episodic"
            assert app.rows == []

    async def test_enter_on_a_module_moves_into_its_blocks(self, brain: Path) -> None:
        """Enter means "show me this one", and the answer is in the next pane."""
        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            await pilot.press("m")
            await pilot.pause()
            await pilot.press("enter")
            await _settle(pilot)
            assert _focus(app) == "blocks"

    async def test_bytes_that_are_not_what_the_block_claims_do_not_take_the_interface_down(self, brain: Path) -> None:
        """The fixture's `pizarron.png` is not a PNG, which is the case that ended a real session: a decoder
        exception inside a worker makes Textual re-raise `WorkerFailed`, and the interface is simply gone."""
        pytest.importorskip("PIL.Image", reason="without Pillow nothing tries to decode")

        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            app.query_one("#filter", Input).value = "pizarron"
            await _settle(pilot)
            assert app.is_running, "the application survived the undecodable blob"
            assert "do not decode" in _pane(app, "preview")

    async def test_the_filter_narrows_the_rows(self, brain: Path) -> None:
        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            app.query_one("#filter", Input).value = "png"
            await _settle(pilot)
            assert [row["title"] for row in app.rows] == ["pizarron.png"]

    async def test_the_detail_panes_hold_the_selected_block(self, brain: Path) -> None:
        """All four tabs are loaded together, so switching to one is never a wait."""
        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            app.query_one("#filter", Input).value = "apuntes"
            await _settle(pilot)
            rendered = _pane(app, "payload")
            assert "text/markdown" in rendered
            assert "registration" in _pane(app, "links")
            assert "verified" in _pane(app, "proof")

    async def test_a_module_with_nothing_in_it_says_so_rather_than_showing_an_empty_table(self, brain: Path) -> None:
        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            app.kind = "procedural"
            app.load_rows()
            await _settle(pilot)
            assert app.rows == []
            assert app.selected is None

    async def test_search_opens_a_screen_of_its_own(self, brain: Path) -> None:
        """Separate from the filter box on purpose: one is the planner's answer and the other is not."""
        from vitruvio.cli.tui.screens import SearchScreen

        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, SearchScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, SearchScreen), "escape returns to the brain without choosing"

    async def test_the_project_is_named_even_when_its_brain_has_no_name(self, brain: Path) -> None:
        """A single-brain project declares its brain in `[brain].path` and never names it. Requiring both dropped
        the project from the header of exactly the brain whose own label -- `brain` -- identifies nothing."""
        app = BrainBrowser(service_for(brain), brain=str(brain), origin="file", name=None, project="solito")
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            assert "solito/brain by file" in app.sub_title

    async def test_a_named_project_brain_is_shown_by_project_and_name(self, brain: Path) -> None:
        """Two projects holding a `metrica-a` each is the normal case now, and the header has to tell them apart."""
        app = BrainBrowser(service_for(brain), brain=str(brain), origin="flag", name="metrica-a", project="eticompass")
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            assert "eticompass/metrica-a by flag" in app.sub_title

    async def test_exporting_writes_the_bytes_into_the_working_directory(self, brain: Path, tmp_path: Path) -> None:
        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            app.query_one("#filter", Input).value = "apuntes"
            await _settle(pilot)
            await pilot.press("e")
            await _settle(pilot)
        assert (tmp_path / "apuntes.md").exists(), "the fixture chdir'd here, and export writes to the cwd"

    async def test_o_hands_over_a_file_the_desktop_can_actually_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A brain whose blocks record no origin -- a pulled one -- still has to open a PDF as a PDF.

        The launch is intercepted rather than performed: a test that opened Preview would put a window over
        whoever ran it. What is asserted is the *suffix of the file handed over*, because that is what every
        desktop handler dispatches on.
        """
        from vitruvio.cli.render import desktop

        root = tmp_path / "sin-origen"
        assert main(["--brain", str(root), "--actor", "t@e.st", "brain", "init"]) == ExitCode.OK
        paper = tmp_path / "paper.pdf"
        paper.write_bytes(b"%PDF-1.7\n" + b"0" * 64)
        # `--origin ""` is not available, so the block is registered normally and the row's origin is dropped
        # below -- which is exactly the shape a pulled brain's rows have.
        assert (
            main(["--brain", str(root), "source", "register", str(paper), "--media-type", "application/pdf"])
            == ExitCode.OK
        )

        handed: list[Path] = []

        def record(path: Path) -> str:
            handed.append(path)
            return "open"

        monkeypatch.setattr(desktop, "open_path", record)

        app = BrainBrowser(service_for(root), brain=str(root))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            assert app.selected is not None
            app.selected["origin"] = None
            await pilot.press("o")
            await _settle(pilot)

        assert handed, "the bytes were written and something was asked to open them"
        assert handed[0].suffix == ".pdf", f"{handed[0].name} would open in a text editor"
        assert handed[0].read_bytes().startswith(b"%PDF")


class TestChoosingWhatToLookAt:
    """The picker: projects on the left, their brains on the right, and retargeting without restarting.

    The interface used to be about whichever brain the command line resolved, and changing that meant quitting.
    Once somebody keeps a project per subject or per client, reading across two of them is the normal case.
    """

    @pytest.fixture
    def projects(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
        """Two registered projects, each holding two brains, one of which has a block in it."""
        roots = []
        for name, brains in (("facultad", ("algebra", "analisis-ii")), ("eticompass", ("metrica-a", "metrica-b"))):
            root = tmp_path / name
            root.mkdir()
            config = str(root / "vitruvio.toml")
            assert main(["--config", config, "--actor", "t@e.st", "project", "init", name]) == ExitCode.OK
            for brain in brains:
                assert main(["--config", config, "--actor", "t@e.st", "project", "add", brain]) == ExitCode.OK
            roots.append(root)

        notes = tmp_path / "apuntes.md"
        notes.write_text("# Fourier\n", encoding="utf-8")
        assert (
            main(
                [
                    "--project",
                    "facultad",
                    "--brain",
                    "algebra",
                    "--actor",
                    "t@e.st",
                    "source",
                    "register",
                    str(notes),
                ]
            )
            == ExitCode.OK
        )
        return roots[0], roots[1]

    def test_the_catalogue_lists_every_registered_project_and_its_brains(self, projects: tuple[Path, Path]) -> None:
        from vitruvio.cli.tui.selection import catalogue

        entries = {entry["project"]: entry for entry in catalogue()}
        assert set(entries) == {"facultad", "eticompass"}
        assert [brain["name"] for brain in entries["eticompass"]["brains"]] == ["metrica-a", "metrica-b"]
        assert all(brain["present"] for brain in entries["facultad"]["brains"])

    def test_the_session_s_own_project_is_not_offered_twice(self, projects: tuple[Path, Path]) -> None:
        """The registry stores resolved paths and the session's file arrives as the command line spelled it. On a
        machine whose temporary or home directory is a symlink -- macOS, every container -- that is two spellings
        of one path, and the project was listed under both."""
        from vitruvio.cli.tui.selection import catalogue

        facultad, _ = projects
        entries = catalogue(facultad / "vitruvio.toml")
        assert [entry["project"] for entry in entries].count("facultad") == 1
        assert [entry["current"] for entry in entries].count(True) == 1

    def test_a_project_is_found_through_a_brain_this_machine_remembers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The migration path, and it needs no command.

        The registry is newer than the projects on the machine it shipped to, so a picker fed only by the registry
        was empty for the person it was built for -- while vitruvio's own history held the brain, and the project
        was one walk-up above it. An unregistered project is offered and *marked*, because `--project` cannot
        reach it yet and finding that out from a CLI refusal is worse than reading it here.
        """
        from vitruvio.cli.tui.selection import catalogue
        from vitruvio.kernel import forget_project, note_brain

        root = tmp_path / "ethicompass"
        root.mkdir()
        config = str(root / "vitruvio.toml")
        assert main(["--config", config, "--actor", "t@e.st", "project", "init", "ethicompass"]) == ExitCode.OK
        assert main(["--config", config, "--actor", "t@e.st", "project", "add", "common"]) == ExitCode.OK
        # As if the project predated the registry: the layout is on disk and remembered, the name is not recorded.
        note_brain(root / "brains" / "common")
        assert forget_project("ethicompass") is True

        found = {entry["project"]: entry for entry in catalogue()}
        assert "ethicompass" in found, "a remembered brain is a project vitruvio can still find"
        assert found["ethicompass"]["registered"] is False
        assert [brain["name"] for brain in found["ethicompass"]["brains"]] == ["common"]

    def test_a_brain_project_add_created_is_remembered_without_becoming_a_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`project add` used not to note the layout it created, so a project whose brains were all made that way
        was invisible to the discovery above. Noted, not selected: adding a brain must not silently change which
        brain anything resolves to."""
        from vitruvio.kernel import load_project, read_state, selected_brain

        root = tmp_path / "facultad"
        root.mkdir()
        config = str(root / "vitruvio.toml")
        assert main(["--config", config, "--actor", "t@e.st", "project", "init", "facultad"]) == ExitCode.OK
        assert main(["--config", config, "--actor", "t@e.st", "project", "add", "analisis-numerico"]) == ExitCode.OK

        remembered = read_state().get("known", [])
        assert str((root / "brains" / "analisis-numerico").resolve()) in remembered
        assert selected_brain(load_project(Path(config))) is None, "noted, not chosen"
        assert "current" not in read_state(), "and not the machine-wide pointer either"

    async def test_p_opens_the_picker_and_escape_keeps_the_brain_you_have(self, brain: Path) -> None:
        from vitruvio.cli.tui.screens import SelectionScreen

        app = BrainBrowser(service_for(brain), brain=str(brain))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            await pilot.press("p")
            await _settle(pilot)
            assert isinstance(app.screen, SelectionScreen)
            await pilot.press("escape")
            await _settle(pilot)
            # `__class__` rather than `isinstance`: the assertion above narrowed the attribute's type, so a
            # negated isinstance after it is a contradiction to a type checker and everything below it is dead.
            assert app.screen.__class__ is not SelectionScreen
            assert app.service is not None, "declining to choose leaves the brain that was already open"

    async def test_choosing_a_brain_in_another_project_retargets_the_interface(
        self, projects: tuple[Path, Path]
    ) -> None:
        """The whole point: one session, several projects. What is on screen afterwards is the *other* brain."""
        from vitruvio.cli.tui.screens import SelectionScreen

        facultad, eticompass = projects
        app = BrainBrowser(
            service_for(facultad / "brains" / "algebra"),
            brain=str(facultad / "brains" / "algebra"),
            origin="flag",
            name="algebra",
            project="facultad",
            config_file=str(facultad / "vitruvio.toml"),
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            assert app.rows, "the fixture registered a block into algebra"

            await pilot.press("p")
            await _settle(pilot)
            screen = app.screen
            assert isinstance(screen, SelectionScreen)

            # Straight at the screen's own tables rather than by counting keypresses: what is under test is that a
            # chosen row becomes the open brain, not how many arrow presses that project happens to be from the top.
            projects_table = screen.query_one("#projects", DataTable)
            index = next(position for position, entry in enumerate(screen.entries) if entry["project"] == "eticompass")
            projects_table.move_cursor(row=index)
            await _settle(pilot)
            screen.query_one("#brains", DataTable).focus()
            await pilot.press("enter")
            await _settle(pilot)

            assert app.project == "eticompass"
            assert app.brain_name == "metrica-a"
            assert app.brain == str((eticompass / "brains" / "metrica-a").resolve())
            assert "eticompass/metrica-a" in app.sub_title
            assert app.rows == [], "the new brain is empty, and the previous brain's rows are gone"

    async def test_the_picker_marks_the_brain_that_is_already_open(self, projects: tuple[Path, Path]) -> None:
        """Half of what a reader opens this screen to find out is where they *are*, and the cursor cannot say it:
        it moves the moment they start looking around."""
        from vitruvio.cli.tui.screens import SelectionScreen

        facultad, _ = projects
        open_layout = facultad / "brains" / "analisis-ii"
        app = BrainBrowser(
            service_for(open_layout),
            brain=str(open_layout),
            origin="flag",
            name="analisis-ii",
            project="facultad",
            config_file=str(facultad / "vitruvio.toml"),
        )
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            await pilot.press("p")
            await _settle(pilot)
            screen = app.screen
            assert isinstance(screen, SelectionScreen)

            marked = [brain["name"] for entry in screen.entries for brain in entry["brains"] if screen._is_open(brain)]
            assert marked == ["analisis-ii"], "exactly the open brain, in exactly one project"

    async def test_browse_with_no_brain_selected_opens_the_picker_instead_of_failing(
        self, projects: tuple[Path, Path]
    ) -> None:
        """`browse` is interactive by definition, so "which brain did you mean" has a list for an answer.

        Every other command still refuses, because a non-interactive caller cannot answer a question.
        """
        from vitruvio.cli.tui.screens import SelectionScreen

        facultad, _ = projects
        app = BrainBrowser(None, brain="", config_file=str(facultad / "vitruvio.toml"))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            assert isinstance(app.screen, SelectionScreen)
            assert "no brain open" in app.sub_title

    async def test_the_picker_starts_on_the_project_the_session_is_in(self, projects: tuple[Path, Path]) -> None:
        """Landing elsewhere would make the highlight disagree with the header, and the highlight fills the
        brain pane -- so the disagreement is one arrow key from being real."""
        from vitruvio.cli.tui.screens import SelectionScreen

        _, eticompass = projects
        app = BrainBrowser(None, brain="", config_file=str(eticompass / "vitruvio.toml"))
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            screen = app.screen
            assert isinstance(screen, SelectionScreen)
            highlighted = screen.entries[screen.query_one("#projects", DataTable).cursor_row]
            assert highlighted["project"] == "eticompass"
            brains = screen.query_one("#brains", DataTable)
            assert brains.row_count == 2, "the highlighted project's brains, not the first project's"


async def _settle(pilot: Any, ticks: int = 25) -> None:
    """
    Let the worker threads finish.

    Every read in the interface runs off the event loop, so a test that asserted immediately would assert on a
    pane that had not been filled yet. Polled rather than slept on a fixed duration, because the duration
    depends on how fast the store is.

    Args:
        pilot (Any): Textual's pilot.
        ticks (int): How many short pauses to allow.
    """
    for _ in range(ticks):
        await pilot.pause(0.05)
        if not pilot.app.workers or all(not worker.is_running for worker in pilot.app.workers):
            await pilot.pause(0.05)
            return


def _focus(app: BrainBrowser) -> str:
    """
    Which pane holds the cursor.

    Args:
        app (BrainBrowser): The running app.

    Returns:
        str: The focused widget's id, or ``"(nothing)"`` -- which is itself a failure worth reading in a message.
    """
    return app.focused.id or "(unnamed)" if app.focused is not None else "(nothing)"


def _pane(app: BrainBrowser, name: str) -> str:
    """
    The text a detail pane currently holds.

    Args:
        app (BrainBrowser): The running app.
        name (str): The pane's widget id.

    Returns:
        str: What it says, without styling.
    """
    from rich.console import Console
    from textual.widgets import Static

    widget = app.query_one(f"#{name}", Static)
    console = Console(width=200, no_color=True, theme=theme.THEME)
    with console.capture() as captured:
        console.print(widget.content)
    return captured.get()
