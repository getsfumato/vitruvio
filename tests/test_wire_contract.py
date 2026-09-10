"""The wire contract itself: what it records, and what it is allowed not to know.

The per-call checking lives in ``wire_contract`` and fails the test that produced the drift. What is left to
assert here is the part no single call can: that the recorded file covers the operation surface, and that the
list of operations nothing exercised is a list somebody chose rather than one that grew.
"""

from __future__ import annotations

import json
import re

import pytest

import wire_contract
from vitruvio.runtime.operation_catalogue import OPERATION_CATALOGUE, Remote, ResultKind


@pytest.fixture(autouse=True)
def _not_while_recording(request: pytest.FixtureRequest) -> None:
    """The file these read is written at session end, so under `--record-shapes` they see the previous run."""
    if request.config.getoption("--record-shapes"):
        pytest.skip("the recording is written after the session, so there is nothing yet to check it against")


def _recorded() -> dict[str, dict[str, object]]:
    contract: dict[str, dict[str, object]] = json.loads(wire_contract.GOLDEN.read_text(encoding="utf-8"))
    return contract


def _serializable() -> set[str]:
    return {
        operation.name
        for domain in OPERATION_CATALOGUE
        for operation in domain.operations
        if operation.result in (ResultKind.JSON, ResultKind.TYPED)
    }


class TestCoverage:
    def test_every_serializable_operation_is_recorded_or_declared_unobserved(self) -> None:
        """The second list is the point: "we have no contract for `auth_rotate`" is a tracked line rather than
        an absence nobody counted."""
        recorded = set(_recorded())
        unobserved = set(json.loads(wire_contract.UNOBSERVED.read_text(encoding="utf-8")))

        assert recorded | unobserved == _serializable()
        assert not recorded & unobserved

    def test_nothing_offered_elsewhere_is_left_without_a_contract(self) -> None:
        """A host-local operation may go unrecorded; one an interface elsewhere could call may not, because it
        is the one whose field names another implementation would be reading."""
        unobserved = set(json.loads(wire_contract.UNOBSERVED.read_text(encoding="utf-8")))
        exposed = {
            operation.name
            for domain in OPERATION_CATALOGUE
            for operation in domain.operations
            if operation.remote is Remote.EXPOSED and operation.result in (ResultKind.JSON, ResultKind.TYPED)
        }

        # Empty today. Asserted rather than deleted because this is how the operations no test had ever
        # called -- catalog_show, index_stats, index_gc, test_embedder, then pack and the auth rotations -- were
        # found, and how the next one will be.
        assert not unobserved & exposed

    def test_no_recorded_field_name_is_really_a_value(self) -> None:
        """The guard on `DYNAMIC_KEYS`: a map that is not declared there gets its keys pinned as schema, and a
        contract that names one brain's digests fails against another brain with the same shape."""
        data = re.compile(r"(sha256:|blake3:|[0-9a-f]{16,}|\d{4}-\d{2}-\d{2}T)")
        pinned = {
            f"{operation}.{path}"
            for operation, entry in _recorded().items()
            for path in entry["paths"]
            if any(data.search(segment) for segment in path.split("."))
        }

        assert pinned == set(), f"declare the parent field in wire_contract.DYNAMIC_KEYS: {sorted(pinned)}"


class TestWhatItRefuses:
    """Recorded as a contract means all three of renamed, retyped and *removed*."""

    def test_a_field_that_disappears_is_refused(self) -> None:
        """The one the subset check could never catch: an empty result observes no path nothing has recorded."""
        with pytest.raises(AssertionError, match="no longer returns"):
            wire_contract._check("state", wire_contract.shape({}))

    def test_a_field_recorded_as_sometimes_absent_may_be_absent(self) -> None:
        state = _recorded()["state"]
        optional = next(iter(state["optional"]))
        whole = {path: set(tokens) for path, tokens in state["paths"].items()}

        wire_contract._check("state", {path: tokens for path, tokens in whole.items() if path != optional})

    def test_a_contract_taken_over_one_digest_accepts_another(self) -> None:
        """`state.resolutions` is keyed by the block being resolved, so two brains agree on the schema and on
        nothing else. Pinning the key would make the contract a fact about the fixture."""
        first = wire_contract.shape({"state": {"resolutions": {"sha256:" + "a" * 64: {"prefer": "ours"}}}})
        second = wire_contract.shape({"state": {"resolutions": {"sha256:" + "b" * 64: {"prefer": "theirs"}}}})

        assert first == second
        assert "state.resolutions.{}.prefer" in first


class TestTheShapeItself:
    def test_a_path_carries_the_json_type_rather_than_the_value(self) -> None:
        assert wire_contract.shape({"digest": "sha256:aa", "count": 3}) == {
            ".": {"object"},
            "digest": {"str"},
            "count": {"int"},
        }

    def test_a_list_collapses_to_one_path_so_length_is_not_the_contract(self) -> None:
        observed = wire_contract.shape({"rows": [{"id": "a"}, {"id": "b"}]})

        assert observed["rows"] == {"array"}
        assert observed["rows[].id"] == {"str"}

    def test_a_field_that_is_sometimes_absent_merges_rather_than_conflicts(self) -> None:
        """Which is why the recording is a union over the whole suite and the check is a subset test."""
        first = wire_contract.shape({"snapshot": None})
        wire_contract.shape({"snapshot": "sha256:aa"}, into=first)

        assert first["snapshot"] == {"null", "str"}

    def test_a_bool_is_not_an_int(self) -> None:
        """It is one in Python and is not one in JSON, and `ok: true` becoming `ok: 1` is exactly the silent
        change this exists to catch."""
        assert wire_contract.shape({"ok": True})["ok"] == {"bool"}
