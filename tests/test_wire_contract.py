"""The wire contract itself: what it records, and what it is allowed not to know.

The per-call checking lives in ``wire_contract`` and fails the test that produced the drift. What is left to
assert here is the part no single call can: that the recorded file covers the operation surface, and that the
list of operations nothing exercised is a list somebody chose rather than one that grew.
"""

from __future__ import annotations

import json

import pytest

import wire_contract
from vitruvio.runtime.operation_catalogue import OPERATION_CATALOGUE, Remote, ResultKind


@pytest.fixture(autouse=True)
def _not_while_recording(request: pytest.FixtureRequest) -> None:
    """The file these read is written at session end, so under `--record-shapes` they see the previous run."""
    if request.config.getoption("--record-shapes"):
        pytest.skip("the recording is written after the session, so there is nothing yet to check it against")


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
        recorded = set(json.loads(wire_contract.GOLDEN.read_text(encoding="utf-8")))
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

        # What is left needs something the fast suite has not got: a registry daemon for the first two, and a
        # governed brain with two authorities for the rest. Both are exercised elsewhere. Four operations that
        # had no excuse -- catalog_show, index_stats, index_gc, test_embedder -- were found by this assertion
        # and now have tests instead of a line here.
        allowed = {"registry_check", "pack", "auth_rotate", "auth_revoke", "auth_countersign", "auth_plan_rotation"}
        assert unobserved & exposed <= allowed


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
