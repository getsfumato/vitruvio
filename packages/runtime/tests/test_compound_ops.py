"""Which brains a compound consults, and which it refuses.

Fake layouts only: `_members` builds a session per brain and opens none of them, so ``oci-layout`` and
``index.json`` -- all ``is_layout`` looks for -- are enough to exercise the selection rules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vitruvio.kernel import ExitCode, UsageError, VitruvioError, resolve
from vitruvio.runtime.ops.compound import CompoundOps
from vitruvio.runtime.ops.retrieval import RetrievalOps
from vitruvio.runtime.session import BrainSession

PROJECT = """
[project]
name = "facultad"

[brains.algebra]
path = "./brains/algebra"

[brains.analisis-ii]
path = "./brains/analisis-ii"

[brains.fisica-i]
path = "./brains/fisica-i"
"""


def make_brain(directory: Path, name: str) -> Path:
    """A minimal OCI layout, which is all `is_layout` looks for."""
    path = directory / "brains" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "oci-layout").write_text('{"imageLayoutVersion": "1.0.0"}', encoding="utf-8")
    (path / "index.json").write_text('{"schemaVersion": 2, "manifests": []}', encoding="utf-8")
    return path


@pytest.fixture
def ops(tmp_path: Path) -> CompoundOps:
    """A three-brain project of which two brains exist on disk."""
    config_file = tmp_path / "vitruvio.toml"
    config_file.write_text(PROJECT, encoding="utf-8")
    make_brain(tmp_path, "algebra")
    make_brain(tmp_path, "analisis-ii")
    return CompoundOps(BrainSession(resolve(config=config_file, require_brain=False)))


class TestSelection:
    def test_named_brains_are_consulted_in_the_order_given(self, ops: CompoundOps) -> None:
        members, skipped = ops._members(["analisis-ii", "algebra"], False)
        assert [name for name, _ in members] == ["analisis-ii", "algebra"]
        assert skipped == []

    def test_each_member_is_its_own_brain_under_the_shared_configuration(self, ops: CompoundOps) -> None:
        members, _ = ops._members(["algebra", "analisis-ii"], False)
        configs = [retrieval.config for _, retrieval in members]
        assert [config.brain_name for config in configs] == ["algebra", "analisis-ii"]
        assert configs[0].brain != configs[1].brain
        assert configs[0].config_file == configs[1].config_file == ops.config.config_file

    def test_a_repeated_name_is_consulted_once(self, ops: CompoundOps) -> None:
        members, _ = ops._members(["algebra", "analisis-ii", "algebra"], False)
        assert [name for name, _ in members] == ["algebra", "analisis-ii"]

    def test_all_brains_skips_a_declared_brain_with_no_layout_and_says_so(self, ops: CompoundOps) -> None:
        members, skipped = ops._members(None, True)
        assert [name for name, _ in members] == ["algebra", "analisis-ii"]
        assert [item["brain"] for item in skipped] == ["fisica-i"]
        assert "no layout" in skipped[0]["reason"]

    def test_a_comma_is_not_split_here(self, ops: CompoundOps) -> None:
        """Splitting is the command line's convenience; the operation takes names as given."""
        with pytest.raises(UsageError):
            ops._members(["algebra,analisis-ii"], False)


class TestRefusals:
    def test_a_name_the_project_does_not_declare_is_a_usage_error_naming_the_known_ones(self, ops: CompoundOps) -> None:
        with pytest.raises(UsageError) as caught:
            ops._members(["algebra", "quimica"], False)
        assert caught.value.exit_code == ExitCode.USAGE
        assert "quimica" in caught.value.message
        assert "algebra, analisis-ii, fisica-i" in str(caught.value.hint)

    def test_a_path_is_refused_because_a_compound_composes_this_projects_brains_only(
        self, ops: CompoundOps, tmp_path: Path
    ) -> None:
        with pytest.raises(UsageError) as caught:
            ops._members([str(tmp_path / "brains" / "algebra"), "analisis-ii"], False)
        assert "this project only" in str(caught.value.hint)

    def test_one_brain_is_not_a_compound(self, ops: CompoundOps) -> None:
        with pytest.raises(UsageError) as caught:
            ops._members(["algebra"], False)
        assert "at least two" in caught.value.message
        assert "vitruvio search" in str(caught.value.hint)

    def test_all_brains_resolving_to_one_is_refused_too(self, tmp_path: Path) -> None:
        config_file = tmp_path / "vitruvio.toml"
        config_file.write_text(PROJECT, encoding="utf-8")
        make_brain(tmp_path, "algebra")
        ops = CompoundOps(BrainSession(resolve(config=config_file, require_brain=False)))
        with pytest.raises(UsageError) as caught:
            ops._members(None, True)
        assert "1 would be consulted" in caught.value.message

    def test_neither_a_list_nor_all_is_a_usage_error(self, ops: CompoundOps) -> None:
        with pytest.raises(UsageError):
            ops._members(None, False)
        with pytest.raises(UsageError):
            ops._members([], False)

    def test_a_list_and_all_together_contradict_each_other(self, ops: CompoundOps) -> None:
        with pytest.raises(UsageError):
            ops._members(["algebra", "analisis-ii"], True)


class TestFailuresNameTheBrain:
    def test_a_members_error_keeps_its_class_and_code_and_gains_the_brains_name(self) -> None:
        """`mapping.translate` sets `code` and `exit_code` on the instance; rebuilding the error would lose them."""

        def failing() -> dict[str, object]:
            error = VitruvioError("the store refused", hint="do nothing")
            error.code = "PROTOCOL_VIOLATION"
            error.exit_code = ExitCode.PROTOCOL
            raise error

        with pytest.raises(VitruvioError) as caught:
            CompoundOps._consult("algebra", failing)
        assert caught.value.message == "algebra: the store refused"
        assert str(caught.value) == "algebra: the store refused"
        assert caught.value.code == "PROTOCOL_VIOLATION"
        assert caught.value.exit_code == ExitCode.PROTOCOL
        assert caught.value.hint == "do nothing"


class TestFiltersReachEveryMember:
    """The public signature takes any iterable and hands the same object to every brain. A generator consumed by the
    first would reach the second empty, and nothing would say so."""

    def test_a_generator_filter_is_materialised_once_for_search(
        self, ops: CompoundOps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[object, object, object]] = []

        def search(self: RetrievalOps, text: str, **options: object) -> dict[str, object]:
            seen.append((options["memory_types"], options["tags"], options["evidence"]))
            return {"matches": [], "verified_against": {}, "truncated": False, "all_verified": True}

        monkeypatch.setattr(RetrievalOps, "search", search)
        ops.compound_search(
            "x",
            brains=["algebra", "analisis-ii"],
            memory_types=(kind for kind in ["semantic"]),
            tags=iter(["fourier"]),
            evidence=iter(["sha256:abc"]),
        )
        assert seen == [(("semantic",), ("fourier",), ("sha256:abc",))] * 2

    def test_a_generator_filter_is_materialised_once_for_explain(
        self, ops: CompoundOps, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[object, object]] = []

        def explain(self: RetrievalOps, text: str, **options: object) -> dict[str, object]:
            seen.append((options["memory_types"], options["tags"]))
            return {"chosen": {}, "considered": [], "degradations": []}

        monkeypatch.setattr(RetrievalOps, "explain", explain)
        ops.compound_explain("x", brains=["algebra", "analisis-ii"], memory_types=iter(["semantic"]), tags=iter(["t"]))
        assert seen == [(("semantic",), ("t",))] * 2

    def test_none_stays_none(self, ops: CompoundOps, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[object] = []

        def search(self: RetrievalOps, text: str, **options: object) -> dict[str, object]:
            seen.append(options["memory_types"])
            return {"matches": [], "verified_against": {}, "truncated": False, "all_verified": True}

        monkeypatch.setattr(RetrievalOps, "search", search)
        ops.compound_search("x", brains=["algebra", "analisis-ii"])
        assert seen == [None, None]
