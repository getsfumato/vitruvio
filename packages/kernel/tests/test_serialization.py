"""What the envelope refuses, and where it says the trouble is.

The point of removing ``default=str`` was that a value with no JSON form used to become its ``repr()`` -- a
well-formed envelope carrying ``PosixPath('/Users/...')`` where a caller expected a string. These are the two
ways a payload can still fail to be JSON, and the assertion in each is the *path*: ``json.dumps`` says what it
could not serialize and never where, which over a result with ninety keys is not a place to start looking.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vitruvio.kernel import VitruvioError, json_dumps


class TestAValueWithNoJsonForm:
    def test_it_is_refused_rather_than_stringified(self) -> None:
        with pytest.raises(VitruvioError) as caught:
            json_dumps({"rows": [{"path": Path("/tmp/x")}]})

        assert caught.value.code == "WIRE_CONTRACT"
        assert "data.rows[0].path is PosixPath" in caught.value.message

    def test_a_key_json_cannot_name_is_named_by_the_key(self) -> None:
        with pytest.raises(VitruvioError, match=r"data\[PosixPath"):
            json_dumps({Path("/tmp/x"): "one"})


class TestANumberJsonHasNoWordFor:
    """`json.dumps` writes the bare words `NaN`, `Infinity` and `-Infinity` by default. Neither is JSON, and a
    strict parser rejects the whole document -- so a division that came out wrong upstream arrives at a consumer
    as an unparseable envelope instead of a named field."""

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_it_is_refused_and_the_field_is_named(self, value: float) -> None:
        with pytest.raises(VitruvioError) as caught:
            json_dumps({"statistics": {"mean": value}})

        assert caught.value.code == "WIRE_CONTRACT"
        assert "data.statistics.mean is not finite" in caught.value.message

    def test_an_ordinary_float_still_serializes(self) -> None:
        assert json.loads(json_dumps({"p95": 80.4}))["p95"] == 80.4
