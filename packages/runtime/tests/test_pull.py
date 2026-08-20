"""``source pull``: the three dedup layers, and the one that is a safety property rather than an optimisation.

The test that matters most in this file is `test_a_redacted_blob_is_not_re-fetched...`. Everything else here saves
work; that one prevents a scheduled pull from silently undoing `retain redact`, which is the command whose own
docstring says it is for personal data, credentials and licensed material.

No network and no subprocess: every source used here is a directory on disk, which is exactly the composition the
built-in kind was shipped first for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vitruvio.ingest.sources import FetchResult, Item
from vitruvio.kernel import BrainSpec, ConfigError, ProjectConfig, SourceSpec, UsageError, resolve
from vitruvio.runtime import BrainService
from vitruvio.runtime.assembly import Capability

PROJECT = """
[brain]
path = "./brain"

[actor]
id = "tester@example.com"

[policy]
profile = "conservative"
redactable_media_types = ["text/markdown"]

[brain.sources.papers]
kind = "directory"
path = "./incoming"
options = {{ glob = "*.md" }}
"""


@pytest.fixture
def project(tmp_path: Path) -> BrainService:
    """An initialised brain whose project declares one directory source with two files in it."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "fourier.md").write_text("# Fourier\n\nSenos y cosenos.\n", encoding="utf-8")
    (incoming / "laplace.md").write_text("# Laplace\n\nDe lo diferencial a lo algebraico.\n", encoding="utf-8")

    config_file = tmp_path / "vitruvio.toml"
    config_file.write_text(PROJECT.format(), encoding="utf-8")
    service = BrainService(resolve(brain=tmp_path / "brain", config=config_file, require_layout=False))
    service.init()
    # Re-resolved after init, because `init` writes the file and the copy in memory predates it.
    return BrainService(resolve(brain=tmp_path / "brain", config=config_file))


def outcomes(result: dict[str, Any]) -> dict[str, str]:
    """Item title to outcome, which is what nearly every assertion here is about."""
    return {str(row["title"]): str(row["outcome"]) for row in result["items"]}


class TestPull:
    def test_it_registers_what_a_source_offers(self, project: BrainService) -> None:
        result = project.pull_source("papers")
        assert result["registered"] == 2
        assert set(outcomes(result).values()) == {"registered"}
        assert project.state()["block_count"] > 0

    def test_a_second_pull_skips_by_origin_without_fetching(self, project: BrainService, tmp_path: Path) -> None:
        """Skipped *before* the fetch, which is the difference between a cheap repeated pull and an idempotent one.
        Proven by deleting the file: if anything tried to read it, this would fail rather than skip."""
        project.pull_source("papers")
        (tmp_path / "incoming" / "fourier.md").write_text("# Fourier\n\nSenos y cosenos.\n", encoding="utf-8")

        again = project.pull_source("papers")
        assert again["registered"] == 0
        assert set(outcomes(again).values()) == {"skipped"}
        assert all("origin" in str(row["reason"]) for row in again["items"])

    def test_a_dry_run_mints_no_version(self, project: BrainService) -> None:
        before = project.state()["block_count"]
        result = project.pull_source("papers", dry_run=True)
        assert result["dry_run"] is True
        assert set(outcomes(result).values()) == {"would-fetch"}
        assert project.state()["block_count"] == before

    def test_one_pull_can_override_options_without_rewriting_the_declaration(
        self, project: BrainService, tmp_path: Path
    ) -> None:
        """One kind can serve several invocations while the committed declaration remains a safe default."""
        result = project.pull_source("papers", dry_run=True, option_overrides={"glob": "fourier.md"})

        assert result["listed"] == 1
        assert result["option_overrides"] == ["glob"]
        assert [row["title"] for row in result["items"]] == ["fourier.md"]

        from vitruvio.kernel import load_project

        declared = load_project(tmp_path / "vitruvio.toml").brain.sources["papers"]
        assert declared.options == {"glob": "*.md"}, "a pull override must never become configuration"

    def test_an_unknown_pull_override_is_validated_by_the_kind(self, project: BrainService) -> None:
        with pytest.raises(ConfigError, match="does not know"):
            project.pull_source("papers", dry_run=True, option_overrides={"typo": True})

    def test_a_limit_counts_registrations_and_not_items(self, project: BrainService) -> None:
        """A limit that counted skips would do nothing at all on the second run, which is the run people repeat."""
        result = project.pull_source("papers", limit=1)
        assert result["registered"] == 1
        assert sorted(outcomes(result).values()) == ["not-reached", "registered"]

    def test_identical_bytes_under_a_second_name_are_a_duplicate(self, project: BrainService, tmp_path: Path) -> None:
        """Layer three, which needs no code: the same bytes compute the same block identity. It is the backstop
        for any source that cannot produce a stable origin, at the cost of one wasted fetch."""
        project.pull_source("papers")
        original = (tmp_path / "incoming" / "fourier.md").read_text(encoding="utf-8")
        (tmp_path / "incoming" / "copia.md").write_text(original, encoding="utf-8")

        result = project.pull_source("papers")
        assert outcomes(result)["copia.md"] == "duplicate"
        assert result["registered"] == 0

    def test_one_unreadable_item_leaves_the_rest_registered(self, project: BrainService, tmp_path: Path) -> None:
        """Per-item failures accumulate. One odd entry in a folder of forty must not cost the other thirty-nine
        their registration, and the report has to say which one it was."""
        incoming = tmp_path / "incoming"
        broken = incoming / "vanishes.md"
        broken.write_text("# gone\n", encoding="utf-8")

        source = project.source_ops.fetch._source("papers", project.config.sources["papers"])
        listed = list(source.list())
        broken.unlink()

        rows = [
            project.source_ops.fetch._pull_one(project.brain(), source, source.spec, item, dry_run=False, refetch=False)
            for item in listed
        ]
        by_title = {str(row["title"]): str(row["outcome"]) for row in rows}
        assert by_title["vanishes.md"] == "failed"
        assert by_title["fourier.md"] == "registered"

    def test_changing_the_media_type_reregisters_rather_than_skipping(
        self, project: BrainService, tmp_path: Path
    ) -> None:
        """The correction people actually make. Media type is part of a canonical block's identity, so a silent
        origin skip would make "I fixed the media type" do nothing, and the wrong block would still be wrong."""
        project.pull_source("papers")

        config_file = tmp_path / "vitruvio.toml"
        config_file.write_text(
            PROJECT.format().replace(
                'options = { glob = "*.md" }', 'options = { glob = "*.md" }\nmedia_type = "text/x-markdown"'
            ),
            encoding="utf-8",
        )
        second = BrainService(resolve(brain=tmp_path / "brain", config=config_file))
        result = second.pull_source("papers")
        assert result["registered"] == 2, "a declared media type that differs must produce new blocks"

    def test_declaring_a_pipeline_reregisters_rather_than_skipping(self, project: BrainService, tmp_path: Path) -> None:
        """The other half of the same correction: `normalize_with` is an input to a block's identity too.

        A view is what a proposer reads, so a source that acquired forty PDFs before a pipeline was declared holds
        forty blocks nothing can read. Skipping here would make declaring one do nothing at all."""
        project.pull_source("papers")

        config_file = tmp_path / "vitruvio.toml"
        config_file.write_text(
            PROJECT.format().replace(
                'options = { glob = "*.md" }', 'options = { glob = "*.md" }\nnormalize_with = "markdown"'
            ),
            encoding="utf-8",
        )
        second = BrainService(resolve(brain=tmp_path / "brain", config=config_file))
        result = second.pull_source("papers")
        assert result["registered"] == 2, "a declared pipeline that the held blocks do not have must produce new blocks"

    def test_undeclaring_a_pipeline_reregisters_rather_than_skipping(
        self, project: BrainService, tmp_path: Path
    ) -> None:
        """And it converges: the blocks registered here have no view, so a third pull skips them."""
        config_file = tmp_path / "vitruvio.toml"
        with_pipeline = PROJECT.format().replace(
            'options = { glob = "*.md" }', 'options = { glob = "*.md" }\nnormalize_with = "markdown"'
        )
        config_file.write_text(with_pipeline, encoding="utf-8")
        BrainService(resolve(brain=tmp_path / "brain", config=config_file)).pull_source("papers")

        config_file.write_text(PROJECT.format(), encoding="utf-8")
        second = BrainService(resolve(brain=tmp_path / "brain", config=config_file))
        assert second.pull_source("papers")["registered"] == 2, "removing a pipeline changes identity too"

        third = BrainService(resolve(brain=tmp_path / "brain", config=config_file))
        assert third.pull_source("papers")["registered"] == 0, "and the correction must not repeat every pull"

    def test_a_fetch_can_supply_metadata_the_listing_does_not_know(self, project: BrainService) -> None:
        """Moodle-like listings often omit the filename until a download redirect reveals it."""

        class DeferredMetadataSource:
            fetches = 0

            def fetch(self, item: Item) -> FetchResult:
                self.fetches += 1
                return FetchResult(b"%PDF-1.7\n", media_type="application/pdf", title="teoria.pdf")

        source = DeferredMetadataSource()
        item = Item(id="77", origin="aula://course/77", title="Teoria")
        spec = project.config.sources["papers"]
        brain = project.brain(Capability.WRITE)

        first = project.source_ops.fetch._pull_one(brain, source, spec, item, dry_run=False, refetch=False)
        reopened = BrainService(project.config)
        second = reopened.source_ops.fetch._pull_one(
            reopened.brain(Capability.WRITE), source, spec, item, dry_run=False, refetch=False
        )

        assert first["outcome"] == "registered"
        assert first["media_type"] == "application/pdf"
        assert first["title"] == "teoria.pdf"
        assert second["outcome"] == "skipped"
        assert second["media_type"] == "application/pdf"
        assert source.fetches == 1, "specific fetched metadata restores cheap origin dedup"

    def test_deferred_metadata_corrects_a_generic_registration(self, project: BrainService) -> None:
        """An octet-stream registration must not hide a type a newer fetch implementation can now discover."""

        class CorrectedSource:
            specific = False
            fetches = 0

            def fetch(self, item: Item) -> bytes | FetchResult:
                self.fetches += 1
                if self.specific:
                    return FetchResult(b"%PDF-1.7\n", media_type="application/pdf", title="teoria.pdf")
                return b"%PDF-1.7\n"

        source = CorrectedSource()
        item = Item(id="88", origin="aula://course/88", title="Teoria")
        spec = project.config.sources["papers"]
        brain = project.brain(Capability.WRITE)

        generic = project.source_ops.fetch._pull_one(brain, source, spec, item, dry_run=False, refetch=False)
        source.specific = True
        reopened = BrainService(project.config)
        corrected = reopened.source_ops.fetch._pull_one(
            reopened.brain(Capability.WRITE), source, spec, item, dry_run=False, refetch=False
        )

        assert generic["media_type"] is None
        assert corrected["outcome"] == "registered"
        assert corrected["media_type"] == "application/pdf"
        assert corrected["block"] != generic["block"]
        assert source.fetches == 2


class TestRedactionGuard:
    def test_a_redacted_blob_is_not_refetched_and_not_written_back(self, project: BrainService, tmp_path: Path) -> None:
        """The regression this whole guard exists for.

        `Brain.register` calls `store.put_bytes(data)` *before* its duplicate check, and `has()` answers True for a
        tombstoned digest whose file is gone. So registering redacted bytes re-materialises exactly what a policy
        destroyed and then reports `duplicate=True`, as though nothing had happened. A scheduled `source pull` is
        the machine that would do that, quietly, forever.
        """
        first = project.pull_source("papers")
        block = next(str(row["block"]) for row in first["items"] if row["title"] == "fourier.md")
        project.redact(block, memory_type="canonical", reason="personal data")

        before = project.resolvability()
        result = project.pull_source("papers", refetch=True)

        assert outcomes(result)["fourier.md"] == "skipped"
        reason = next(str(row["reason"]) for row in result["items"] if row["title"] == "fourier.md")
        assert "redacted" in reason
        assert project.resolvability() == before, "a pull must not change what is resolvable"

    def test_the_guard_survives_the_origin_layer_being_bypassed(self, project: BrainService) -> None:
        """`--refetch` is the documented way past layer two, so the guard cannot live there. It is checked against
        the fetched bytes, after every cheaper check has been skipped."""
        first = project.pull_source("papers")
        block = next(str(row["block"]) for row in first["items"] if row["title"] == "laplace.md")
        project.redact(block, memory_type="canonical", reason="licensed material")

        tombstoned = project.resolvability()["counts"]["tombstoned"]
        project.pull_source("papers", refetch=True)
        assert project.resolvability()["counts"]["tombstoned"] == tombstoned


class TestPullAll:
    def test_it_pulls_every_declared_source_and_keeps_going_past_a_failure(
        self, project: BrainService, tmp_path: Path
    ) -> None:
        config_file = tmp_path / "vitruvio.toml"
        config_file.write_text(
            PROJECT.format() + '\n[brain.sources.absent]\nkind = "directory"\npath = "./not-there"\n',
            encoding="utf-8",
        )
        service = BrainService(resolve(brain=tmp_path / "brain", config=config_file))

        result = service.pull_all()
        assert result["ok"] is False, "one source failed"
        assert result["registered"] == 2, "and the other one still registered everything it had"
        failed = next(entry for entry in result["sources"] if not entry["ok"])
        assert failed["source"] == "absent"
        assert failed["code"] == "SOURCE_FAILED"

    def test_with_nothing_declared_it_says_so_as_a_configuration_error(self, service: BrainService) -> None:
        with pytest.raises(ConfigError, match="brain declares no sources"):
            service.pull_all()


class TestRefusals:
    def test_an_undeclared_source_is_a_usage_error_and_not_an_internal_one(self, project: BrainService) -> None:
        """Exit 1 is documented as "always a bug in vitruvio". Reporting a typo'd source name that way sends the
        reader to investigate our code instead of their command line."""
        with pytest.raises(UsageError, match="declares no source"):
            project.pull_source("nonexistent")

    def test_a_brain_cannot_see_another_brains_source(self, tmp_path: Path) -> None:
        config_file = tmp_path / "vitruvio.toml"
        config_file.write_text(
            """
[actor]
id = "tester@example.com"

[brains.algebra]
path = "./brains/algebra"

[brains.algebra.sources.papers]
kind = "directory"
path = "./incoming"

[brains.fisica]
path = "./brains/fisica"
""",
            encoding="utf-8",
        )
        (tmp_path / "incoming").mkdir()
        config = resolve(brain=tmp_path / "brains" / "fisica", config=config_file, require_layout=False)
        assert config.brain_name == "fisica", "a declared brain selected by path keeps its configuration identity"
        service = BrainService(config)
        service.init()
        with pytest.raises(UsageError, match="brain declares no source"):
            service.pull_source("papers")

    def test_the_same_source_name_has_persistent_options_per_brain(self, tmp_path: Path) -> None:
        config_file = tmp_path / "vitruvio.toml"
        config_file.write_text(
            """
[actor]
id = "tester@example.com"

[brains.simulacion]
path = "./brains/simulacion"

[brains.simulacion.sources.aula]
kind = "directory"
path = "./incoming"
options = { glob = "simulacion.md" }

[brains.fisica]
path = "./brains/fisica"

[brains.fisica.sources.aula]
kind = "directory"
path = "./incoming"
options = { glob = "fisica.md" }
""",
            encoding="utf-8",
        )
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        (incoming / "simulacion.md").write_text("# Simulacion\n", encoding="utf-8")
        (incoming / "fisica.md").write_text("# Fisica\n", encoding="utf-8")

        for brain_name in ("simulacion", "fisica"):
            path = tmp_path / "brains" / brain_name
            config = resolve(brain=path, config=config_file, require_layout=False)
            service = BrainService(config)
            service.init()
            result = service.pull_source("aula", dry_run=True)
            assert result["brain"] == brain_name
            assert [row["title"] for row in result["items"]] == [f"{brain_name}.md"]


class TestDeclaration:
    def test_add_writes_the_whole_table_in_one_call(self, project: BrainService, tmp_path: Path) -> None:
        """`update_config` validates the entire document before writing, so writing `kind` first would submit an
        intermediate document missing required fields and be rejected. One call, one validation, one write."""
        result = project.add_source("notes", kind="directory", path="./notes", options={"glob": "*.txt"})
        assert result["name"] == "notes"

        from vitruvio.kernel import load_project

        written = load_project(tmp_path / "vitruvio.toml")
        assert written.brain.sources["notes"].kind == "directory"
        assert written.brain.sources["notes"].options == {"glob": "*.txt"}

    def test_add_warns_when_a_path_leaves_the_project(self, project: BrainService, tmp_path: Path) -> None:
        """A directory source composes with `dist push` into a way to publish something nobody meant to: point one
        at the wrong folder and a private key becomes a content-addressed block in a public repository."""
        result = project.add_source("elsewhere", kind="directory", path=str(tmp_path.parent))
        assert result["warning"] is not None
        assert "outside the project" in result["warning"]

    def test_a_taken_name_is_refused(self, project: BrainService) -> None:
        with pytest.raises(UsageError, match="already declares"):
            project.add_source("papers", kind="directory", path="./incoming")

    def test_add_writes_only_under_the_selected_named_brain(self, tmp_path: Path) -> None:
        config_file = tmp_path / "vitruvio.toml"
        config_file.write_text(
            '[actor]\nid = "t@e.c"\n\n[brains.algebra]\npath = "./brains/algebra"\n\n'
            '[brains.fisica]\npath = "./brains/fisica"\n',
            encoding="utf-8",
        )
        config = resolve(brain=tmp_path / "brains" / "algebra", config=config_file, require_layout=False)
        service = BrainService(config)
        service.init()
        service.add_source("aula", kind="directory", path="./incoming")

        from vitruvio.kernel import load_project

        written = load_project(config_file)
        assert "aula" in written.brains["algebra"].sources
        assert written.brains["fisica"].sources == {}

    def test_remove_undeclares_without_touching_anything_registered(
        self, project: BrainService, tmp_path: Path
    ) -> None:
        project.pull_source("papers")
        before = project.state()["block_count"]
        project.remove_source("papers")

        from vitruvio.kernel import load_project

        assert "papers" not in load_project(tmp_path / "vitruvio.toml").brain.sources
        assert project.state()["block_count"] == before

    def test_removing_something_undeclared_is_a_usage_error(self, project: BrainService) -> None:
        with pytest.raises(UsageError, match="declares no source"):
            project.remove_source("nonexistent")

    def test_status_reports_an_unusable_source_as_a_row_rather_than_raising(self, tmp_path: Path) -> None:
        """One broken declaration must not hide the five that are fine. That is what separates `status` from
        `pull`."""
        config_file = tmp_path / "vitruvio.toml"
        config_file.write_text(
            PROJECT.format() + '\n[brain.sources.absent]\nkind = "directory"\npath = "./not-there"\n',
            encoding="utf-8",
        )
        (tmp_path / "incoming").mkdir(exist_ok=True)
        service = BrainService(resolve(brain=tmp_path / "brain", config=config_file, require_layout=False))
        rows = {str(row["name"]): row for row in service.sources()["sources"]}
        assert rows["papers"]["available"] is True
        assert rows["absent"]["available"] is False
        assert "does not exist" in str(rows["absent"]["reason"])

    def test_status_reports_an_unknown_kind_without_failing(self, tmp_path: Path) -> None:
        config_file = tmp_path / "vitruvio.toml"
        config_file.write_text(
            """
[actor]
id = "tester@example.com"

[brain]
path = "./brain"

[brain.sources.aula]
kind = "aulasvirtuales"
""",
            encoding="utf-8",
        )
        service = BrainService(resolve(brain=tmp_path / "brain", config=config_file, require_layout=False))
        row = service.sources()["sources"][0]
        assert row["available"] is False
        assert "not installed" in str(row["reason"])

    def test_a_directory_source_with_no_path_is_a_configuration_error(self, tmp_path: Path) -> None:
        spec = SourceSpec(kind="directory")
        ProjectConfig(brain=BrainSpec(sources={"papers": spec}))  # the schema allows it; the kind does not
        config_file = tmp_path / "vitruvio.toml"
        config_file.write_text(
            '[actor]\nid = "t@e.c"\n\n[brain]\npath = "./brain"\n\n[brain.sources.papers]\nkind = "directory"\n',
            encoding="utf-8",
        )
        service = BrainService(resolve(brain=tmp_path / "brain", config=config_file, require_layout=False))
        service.init()
        with pytest.raises(ConfigError, match="no `path`"):
            service.pull_source("papers")
