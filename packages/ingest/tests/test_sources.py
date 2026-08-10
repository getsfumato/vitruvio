"""Sources: the containment rules, the subprocess bounds, and plugin loading.

No network and no real subprocess anywhere here. Every test in this file is about a refusal, because a source's
whole risk profile is what it does when the world is uncooperative or the declaration is wrong -- and two of these
refusals (a FIFO, a symlink out of the root) protect against failures that are silent rather than loud.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from vitruvio.ingest import sources
from vitruvio.ingest.sources import BaseSource, DirectorySource, Item, kinds, resolve_source, scaffold
from vitruvio.kernel import ConfigError, SourceError, SourceSpec, SourceUnavailableError

if TYPE_CHECKING:
    from collections.abc import Sequence


def make(
    tmp_path: Path,
    *,
    name: str = "papers",
    kind: str = "directory",
    **fields: Any,
) -> DirectorySource:
    """A directory source rooted at a real directory."""
    root = tmp_path / "src"
    root.mkdir(exist_ok=True)
    spec = SourceSpec(kind=kind, path=str(root), **fields)
    return DirectorySource(name=name, spec=spec, root=root, cwd=tmp_path)


class TestContainment:
    def test_a_file_inside_the_root_is_accepted(self, tmp_path: Path) -> None:
        source = make(tmp_path)
        target = tmp_path / "src" / "paper.pdf"
        target.write_bytes(b"%PDF-1.7")
        assert source.contain(target) == target.resolve()

    def test_a_path_outside_the_root_is_refused(self, tmp_path: Path) -> None:
        """The failure this prevents is silent: the block is content-addressed and Merkle-committed before anyone
        reads the pull report."""
        source = make(tmp_path)
        outside = tmp_path / "secret.pem"
        outside.write_text("-----BEGIN PRIVATE KEY-----")
        with pytest.raises(SourceError, match="outside"):
            source.contain(outside)

    def test_a_symlink_is_refused_even_when_its_target_is_inside(self, tmp_path: Path) -> None:
        """Not only an escape hatch: a link inside the root registers the same bytes twice under two origins."""
        source = make(tmp_path)
        real = tmp_path / "src" / "paper.pdf"
        real.write_bytes(b"%PDF-1.7")
        link = tmp_path / "src" / "alias.pdf"
        link.symlink_to(real)
        with pytest.raises(SourceError, match="symlink"):
            source.contain(link)

    def test_a_symlink_escaping_the_root_is_refused_twice_over(self, tmp_path: Path) -> None:
        source = make(tmp_path)
        outside = tmp_path / "secret.pem"
        outside.write_text("-----BEGIN PRIVATE KEY-----")
        link = tmp_path / "src" / "innocent.pdf"
        link.symlink_to(outside)
        with pytest.raises(SourceError, match="symlink"):
            source.contain(link)
        with pytest.raises(SourceError, match="outside"):
            source.contain(link, allow_symlinks=True)

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
    def test_a_fifo_is_refused(self, tmp_path: Path) -> None:
        """The one that is not theoretical: `read_bytes()` on a FIFO blocks forever, with nothing on screen, and a
        glob will hand you one without comment."""
        source = make(tmp_path)
        fifo = tmp_path / "src" / "pipe"
        os.mkfifo(fifo)
        with pytest.raises(SourceError, match="not a regular file"):
            source.contain(fifo)

    def test_a_missing_file_says_so_rather_than_saying_it_is_not_a_file(self, tmp_path: Path) -> None:
        source = make(tmp_path)
        with pytest.raises(SourceError, match="does not exist"):
            source.contain(tmp_path / "src" / "absent.pdf")

    def test_an_oversized_file_is_refused_before_it_is_read(self, tmp_path: Path) -> None:
        """Checked against `st_size`, not after a read: that is the difference between a refusal and an OOM kill."""
        source = make(tmp_path, max_bytes=8)
        target = tmp_path / "src" / "big.bin"
        target.write_bytes(b"0" * 64)
        with pytest.raises(SourceError, match="over the declared max_bytes"):
            source.contain(target)


class TestSubprocessBounds:
    def test_a_command_runs_with_closed_stdin_under_a_timeout_and_never_through_a_shell(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three bounds in one assertion because they fail the same way: a source that hangs with nothing on
        screen. Closed stdin is the one that is easy to forget and the most expensive to debug."""
        captured: dict[str, Any] = {}

        def record(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            captured["argv"] = list(argv)
            captured.update(kwargs)
            return subprocess.CompletedProcess(list(argv), 0, b"out", b"")

        monkeypatch.setattr(sources.subprocess, "run", record)
        source = make(tmp_path, timeout=42)
        assert source.run(["aulasvirtuales", "list"]) == b"out"

        assert captured["stdin"] is subprocess.DEVNULL
        assert captured["timeout"] == 42
        assert "shell" not in captured, "shell=True must appear nowhere in this module"
        assert captured["argv"] == ["aulasvirtuales", "list"]

    def test_the_environment_strips_vitruvio_variables(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A source shelling back into vitruvio while inheriting VITRUVIO_BRAIN writes into the brain that is
        pulling it, which is a loop whose blocks are hard to tell from legitimate ones afterwards."""
        monkeypatch.setenv("VITRUVIO_BRAIN", "/somewhere/else")
        monkeypatch.setenv("PATH_MARKER", "kept")
        environment = make(tmp_path).environment()
        assert "VITRUVIO_BRAIN" not in environment
        assert environment["PATH_MARKER"] == "kept"

    def test_a_missing_program_names_it(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def missing(argv: Sequence[str], **kwargs: Any) -> None:
            raise FileNotFoundError(argv[0])

        monkeypatch.setattr(sources.subprocess, "run", missing)
        with pytest.raises(SourceError, match="aulasvirtuales"):
            make(tmp_path).run(["aulasvirtuales", "list"])

    def test_a_timeout_suggests_the_two_things_that_cause_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def slow(argv: Sequence[str], **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd=list(argv), timeout=1.0)

        monkeypatch.setattr(sources.subprocess, "run", slow)
        with pytest.raises(SourceError, match="without finishing") as raised:
            make(tmp_path).run(["slow-tool"])
        assert raised.value.hint is not None
        assert "waiting for input" in raised.value.hint, "the two causes are a slow tool and a blocked prompt"

    def test_a_failure_quotes_the_tail_of_stderr(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An exit code alone sends the reader to run the command by hand to find out what it said."""

        def failing(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(list(argv), 2, b"", b"error: no session, run `login` first")

        monkeypatch.setattr(sources.subprocess, "run", failing)
        with pytest.raises(SourceError, match="run `login` first"):
            make(tmp_path).run(["aulasvirtuales", "list"])

    def test_stdout_stays_bytes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Decoding here would corrupt every PDF that came through it, which is why `run_text` is a second method
        rather than a flag on this one."""

        def binary(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            assert kwargs.get("text") in (None, False)
            return subprocess.CompletedProcess(list(argv), 0, b"\x89PNG\r\n", b"")

        monkeypatch.setattr(sources.subprocess, "run", binary)
        source = make(tmp_path)
        assert source.run(["tool"]) == b"\x89PNG\r\n"
        assert source.run_text(["tool"]).startswith("�PNG")


class TestDirectorySource:
    def test_it_lists_matching_files_in_a_stable_order(self, tmp_path: Path) -> None:
        """Stable because `--limit 10` that takes a different ten files each run is not a limit."""
        source = make(tmp_path, options={"glob": "*.pdf"})
        for name in ("c.pdf", "a.pdf", "b.pdf", "notes.txt"):
            (tmp_path / "src" / name).write_bytes(b"x")
        assert [item.title for item in source.list()] == ["a.pdf", "b.pdf", "c.pdf"]

    def test_each_item_carries_a_stable_origin_and_a_guessed_media_type(self, tmp_path: Path) -> None:
        source = make(tmp_path)
        target = tmp_path / "src" / "notes.md"
        target.write_text("# hola")
        item = source.list()[0]
        assert item.origin == target.as_uri()
        assert item.media_type == "text/markdown"
        assert item.size == len("# hola")

    def test_a_declared_media_type_wins_over_the_extension(self, tmp_path: Path) -> None:
        """An extension is a claim; a declaration is a decision. And the media type is part of block identity, so
        getting it right at registration is cheaper than getting it right later."""
        source = make(tmp_path, media_type="application/x-latex")
        (tmp_path / "src" / "paper.tex").write_text("\\documentclass{article}")
        assert source.list()[0].media_type == "application/x-latex"

    def test_dotfiles_are_skipped_unless_asked_for(self, tmp_path: Path) -> None:
        source = make(tmp_path)
        (tmp_path / "src" / ".DS_Store").write_bytes(b"junk")
        (tmp_path / "src" / "paper.pdf").write_bytes(b"%PDF")
        assert [item.title for item in source.list()] == ["paper.pdf"]

        including = make(tmp_path, options={"hidden": True})
        assert len(including.list()) == 2

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
    def test_a_fifo_in_the_directory_does_not_stop_the_other_files(self, tmp_path: Path) -> None:
        """Skipped rather than raised: one odd entry must not cost the other forty files their registration."""
        source = make(tmp_path)
        os.mkfifo(tmp_path / "src" / "pipe")
        (tmp_path / "src" / "paper.pdf").write_bytes(b"%PDF")
        assert [item.title for item in source.list()] == ["paper.pdf"]

    def test_fetching_reads_through_the_containment_check(self, tmp_path: Path) -> None:
        source = make(tmp_path)
        (tmp_path / "src" / "paper.pdf").write_bytes(b"%PDF-1.7")
        assert source.fetch(source.list()[0]) == b"%PDF-1.7"

        outside = tmp_path / "secret.pem"
        outside.write_text("key")
        with pytest.raises(SourceError, match="outside"):
            source.fetch(Item(id=str(outside), origin=outside.as_uri()))

    def test_a_missing_directory_is_reported_rather_than_raised(self, tmp_path: Path) -> None:
        """`source status` has to be able to show a folder that has not been created yet without failing."""
        root = tmp_path / "not-yet"
        source = DirectorySource(name="papers", spec=SourceSpec(kind="directory", path=str(root)), root=root)
        assert source.available is False
        assert source.unavailable_because() is not None
        assert "does not exist" in str(source.unavailable_because())
        with pytest.raises(SourceError, match="not a directory"):
            source.list()

    def test_a_directory_source_without_a_path_is_a_configuration_error(self) -> None:
        with pytest.raises(ConfigError, match="no `path`"):
            DirectorySource(name="papers", spec=SourceSpec(kind="directory"), root=None)

    def test_an_unknown_option_is_refused_rather_than_ignored(self, tmp_path: Path) -> None:
        """A typo'd `glob` that silently matched everything would register a directory's worth of material nobody
        asked for, and every block of it is content-addressed and committed."""
        with pytest.raises(ConfigError, match="glob"):
            make(tmp_path, options={"globb": "*.pdf"})


class TestKinds:
    def test_the_builtin_kind_is_resolvable(self, tmp_path: Path) -> None:
        source = resolve_source("papers", SourceSpec(kind="directory", path=str(tmp_path)), root=tmp_path)
        assert isinstance(source, DirectorySource)
        assert kinds()["directory"].provenance == "built-in"

    def test_an_unknown_kind_lists_what_is_available(self) -> None:
        with pytest.raises(SourceUnavailableError, match="available: directory"):
            resolve_source("aula", SourceSpec(kind="aulasvirtuales"))

    def test_an_unknown_kind_is_a_configuration_error_and_not_an_internal_one(self) -> None:
        """The precedent this deliberately breaks with: EmbedderUnavailableError is a bare Exception, so a missing
        extra falls through the mapping table and reports an uninstalled dependency as a bug in vitruvio."""
        from vitruvio.kernel import ExitCode

        assert SourceUnavailableError("x").exit_code == ExitCode.CONFIG

    def test_a_scaffolded_plugin_loads_and_resolves_by_its_kind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole "write your own" path, end to end: scaffold, edit, declare, resolve."""
        directory = tmp_path / "plugins"
        directory.mkdir()
        body = scaffold("aulasvirtuales").replace(
            'raise NotImplementedError("list the items this source offers")',
            'return [Item(id="4821", origin="aula://77/4821")]',
        )
        (directory / "aulasvirtuales.py").write_text(body, encoding="utf-8")
        monkeypatch.setattr(sources, "plugin_dir", lambda: directory)

        found = kinds()
        assert found["aulasvirtuales"].provenance == f"plugin:{directory / 'aulasvirtuales.py'}"
        assert found["aulasvirtuales"].implementation.__name__ == "AulasvirtualesSource"

        source = resolve_source("algebra-aula", SourceSpec(kind="aulasvirtuales"))
        assert [item.origin for item in source.list()] == ["aula://77/4821"]

    def test_a_plugin_overrides_a_builtin_of_the_same_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Precedence with a reason: the machine's owner is the one who put the file there."""
        directory = tmp_path / "plugins"
        directory.mkdir()
        (directory / "mine.py").write_text(
            "from vitruvio.ingest.sources import BaseSource\n\n\nclass Mine(BaseSource):\n    KIND = 'directory'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(sources, "plugin_dir", lambda: directory)
        assert kinds()["directory"].implementation.__name__ == "Mine"

    def test_a_plugin_that_raises_on_import_names_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not a traceback through importlib: the reader's next action is to edit that file, and a traceback buries
        which one it was."""
        directory = tmp_path / "plugins"
        directory.mkdir()
        broken = directory / "broken.py"
        broken.write_text("raise RuntimeError('no session')\n", encoding="utf-8")
        monkeypatch.setattr(sources, "plugin_dir", lambda: directory)
        with pytest.raises(SourceUnavailableError, match=r"broken\.py"):
            kinds()

    def test_a_subclass_without_a_kind_is_not_a_kind(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """How a plugin shares a base class between two of its own kinds without registering the intermediate."""
        directory = tmp_path / "plugins"
        directory.mkdir()
        (directory / "shared.py").write_text(
            "from vitruvio.ingest.sources import BaseSource\n\n\nclass Half(BaseSource):\n    pass\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(sources, "plugin_dir", lambda: directory)
        assert set(kinds()) == {"directory"}

    def test_an_underscored_file_is_not_loaded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Somewhere to put a helper module that is imported by a plugin rather than being one."""
        directory = tmp_path / "plugins"
        directory.mkdir()
        (directory / "_helpers.py").write_text("raise RuntimeError('never imported')\n", encoding="utf-8")
        monkeypatch.setattr(sources, "plugin_dir", lambda: directory)
        assert set(kinds()) == {"directory"}

    def test_a_missing_plugin_directory_is_not_an_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sources, "plugin_dir", lambda: tmp_path / "absent")
        assert set(kinds()) == {"directory"}


class TestScaffold:
    def test_it_names_a_class_after_the_kind(self) -> None:
        assert "class AulasVirtualesSource(BaseSource):" in scaffold("aulas-virtuales")
        assert 'KIND = "aulas-virtuales"' in scaffold("aulas-virtuales")

    def test_it_is_valid_python(self) -> None:
        compile(scaffold("youtube"), "scaffold", "exec")

    def test_every_name_it_annotates_with_is_imported(self) -> None:
        """This one caught a real bug rather than guarding a hypothetical. A rename put `Sequence[Item]` into the
        starter without its import, and `from __future__ import annotations` means Python never evaluates an
        annotation -- so the file imported cleanly and the only thing that complained was a plugin author's editor,
        much later."""
        import ast

        tree = ast.parse(scaffold("youtube"))
        imported = {
            alias.asname or alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in node.names
        }
        annotated = [
            node.annotation
            for node in ast.walk(tree)
            if isinstance(node, ast.arg | ast.AnnAssign) and node.annotation is not None
        ]
        annotated += [node.returns for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.returns]
        used = {
            name.id
            for annotation in annotated
            for name in ast.walk(annotation)
            if isinstance(name, ast.Name) and name.id[:1].isupper()
        }
        assert used <= imported, f"the starter annotates with names it does not import: {sorted(used - imported)}"

    def test_it_teaches_the_two_things_a_plugin_author_gets_wrong(self) -> None:
        """`origin` has to be stable, and a local read has to go through `contain`. Both are in the starter because
        the cost of discovering them the hard way is a directory's worth of duplicate or unsafe blocks."""
        body = scaffold("youtube")
        assert "stable across runs" in body
        assert "self.contain(" in body


class TestBaseSource:
    def test_the_protocol_is_satisfied_by_the_builtin(self, tmp_path: Path) -> None:
        assert isinstance(make(tmp_path), sources.Source)

    def test_the_base_refuses_to_pretend_it_can_list_or_fetch(self, tmp_path: Path) -> None:
        base = BaseSource(name="bare", spec=SourceSpec(kind="none"), root=tmp_path)
        with pytest.raises(NotImplementedError):
            base.list()
        with pytest.raises(NotImplementedError):
            base.fetch(Item(id="x", origin="x"))

    def test_it_reports_itself_by_name_and_kind(self, tmp_path: Path) -> None:
        assert "papers" in repr(make(tmp_path))
        assert "directory" in repr(make(tmp_path))
