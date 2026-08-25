"""The update check: ordering, caching, and every way it is allowed to fail.

Nothing here touches the network. `fetch_latest` is the one function that would, and it is replaced wherever a
test needs an answer -- a suite that asked GitHub would be a suite that fails when GitHub is down, which is the
exact behaviour this module exists to prevent in the product.

The tests that matter most are the ones about *not* doing something: not reporting a prerelease as an upgrade,
not failing when offline, not asking again inside the TTL, and not asking at all when told not to.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from vitruvio.kernel import updates
from vitruvio.kernel.version import __version__


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A cache directory per test, and no opt-out inherited from whoever is running the suite."""
    monkeypatch.delenv(updates.OPT_OUT, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path


class TestOrdering:
    """A version comparison that is wrong is worse than no check: it offers people the wrong build."""

    @pytest.mark.parametrize(
        ("candidate", "than", "expected"),
        [
            ("0.6.0", "0.5.1", True),
            ("0.5.1", "0.5.1", False),
            ("0.5.0", "0.5.1", False),
            ("1.0.0", "0.9.9", True),
            ("0.10.0", "0.9.0", True),
            # The case a tuple comparison gets wrong, in both spellings semantic-release can emit. A
            # prerelease precedes its release, so it is never an upgrade over it.
            ("0.6.0b1", "0.6.0", False),
            ("0.6.0", "0.6.0b1", True),
            ("0.6.0-b.1", "0.6.0", False),
            ("0.6.0", "0.6.0-b.1", True),
            # A tag nobody can order is nobody's upgrade.
            ("not-a-version", "0.5.1", False),
            ("0.6.0", "not-a-version", False),
        ],
    )
    def test_it_orders_releases_and_prereleases(self, candidate: str, than: str, expected: bool) -> None:
        assert updates.is_newer(candidate, than) is expected

    @pytest.mark.parametrize(
        ("candidate", "normalized"),
        [("v1.2.3", "1.2.3"), ("1.2.3-rc.1", "1.2.3rc1"), ("not a version; echo no", None)],
    )
    def test_it_normalizes_only_real_versions(self, candidate: str, normalized: str | None) -> None:
        assert updates.normalize_version(candidate) == normalized


class TestTheCheck:
    def test_a_newer_release_is_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(updates, "fetch_latest", lambda timeout=0: "99.0.0")

        found = updates.check()

        assert found.latest == "99.0.0"
        assert found.available is True
        assert found.current == __version__
        assert found.checked is True

    def test_the_running_version_is_not_an_upgrade(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(updates, "fetch_latest", lambda timeout=0: __version__)

        found = updates.check()

        assert found.known is True
        assert found.available is False

    def test_being_unable_to_ask_is_not_a_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Offline, rate-limited, behind a proxy returning HTML -- all the same answer, and none of them raise."""
        monkeypatch.setattr(updates, "fetch_latest", lambda timeout=0: None)

        found = updates.check()

        assert found.latest is None
        assert found.known is False
        assert found.available is False, "'could not tell' must never read as 'there is something new'"

    def test_a_failed_lookup_is_cached_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Otherwise a machine that is offline pays the timeout on every command until it comes back."""
        calls = {"n": 0}

        def counted(timeout: float = 0) -> None:
            calls["n"] += 1

        monkeypatch.setattr(updates, "fetch_latest", counted)
        updates.check()
        updates.check()

        assert calls["n"] == 1, "the second call must read the cache, including a cached failure"

    def test_the_second_check_inside_the_ttl_asks_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}

        def counted(timeout: float = 0) -> str:
            calls["n"] += 1
            return "99.0.0"

        monkeypatch.setattr(updates, "fetch_latest", counted)
        first = updates.check()
        second = updates.check()

        assert calls["n"] == 1
        assert first.checked is True
        assert second.checked is False, "a cached answer says so, for a caller reporting when it last looked"
        assert second.available is True

    def test_a_stale_cache_is_asked_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(updates, "fetch_latest", lambda timeout=0: "99.0.0")
        updates.check()

        from vitruvio.kernel.paths import cache_home

        path = cache_home() / updates.CACHE_FILE
        record = json.loads(path.read_text(encoding="utf-8"))
        record["checked_at"] = time.time() - updates.CHECK_TTL - 1
        path.write_text(json.dumps(record), encoding="utf-8")

        assert updates.is_due() is True
        assert updates.check().checked is True

    def test_an_unreadable_cache_is_a_miss_and_not_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from vitruvio.kernel.paths import cache_home

        path = cache_home() / updates.CACHE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json", encoding="utf-8")
        monkeypatch.setattr(updates, "fetch_latest", lambda timeout=0: "99.0.0")

        assert updates.check().available is True


class TestTheOptOut:
    def test_it_asks_nothing_and_reports_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(timeout: float = 0) -> str:
            raise AssertionError("the opt-out must be honoured before anything reaches the network")

        monkeypatch.setenv(updates.OPT_OUT, "1")
        monkeypatch.setattr(updates, "fetch_latest", refuse)

        found = updates.check()

        assert found.known is False
        assert found.available is False
        assert updates.is_due() is False
        assert updates.cached_update().available is False

    def test_an_explicit_check_overrides_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The variable silences the *ambient* check. Reading it as a prohibition on `vitruvio update` would be
        reading it wrong -- somebody who typed the command asked the question directly."""
        monkeypatch.setenv(updates.OPT_OUT, "1")
        monkeypatch.setattr(updates, "fetch_latest", lambda timeout=0: "99.0.0")

        assert updates.check(force=True).available is True

    @pytest.mark.parametrize("value", ["0", "false", "no", ""])
    def test_an_explicit_falsehood_is_not_an_opt_out(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(updates.OPT_OUT, value)
        assert updates.opted_out() is False


class TestTheCachedRead:
    def test_it_asks_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """What the post-command notice reads through, so printing it can never cost a request."""

        def refuse(timeout: float = 0) -> str:
            raise AssertionError("the cached read must not reach the network")

        monkeypatch.setattr(updates, "fetch_latest", refuse)

        assert updates.cached_update().known is False

    def test_it_reports_what_the_last_check_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(updates, "fetch_latest", lambda timeout=0: "99.0.0")
        updates.check()

        found = updates.cached_update()

        assert found.available is True
        assert found.latest == "99.0.0"


class TestTheSourceGuard:
    def test_a_checkout_is_recognised(self) -> None:
        """The suite runs from the checkout, so this is the honest answer here -- and it is what stops
        `vitruvio update` from running an installer over somebody's working copy."""
        assert updates.installed_from_source() is True

    def test_an_installed_distribution_is_not(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(updates, "__file__", "/opt/x/lib/python3.11/site-packages/vitruvio/kernel/updates.py")
        assert updates.installed_from_source() is False


class TestFetching:
    """`fetch_latest` itself, with the transport replaced -- the parsing is what has edges."""

    def _answering(self, monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
        import urllib.request

        class Response:
            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *exception: object) -> None:
                return None

        monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: Response())

    def test_it_strips_the_leading_v(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._answering(monkeypatch, {"tag_name": "v1.2.3"})
        assert updates.fetch_latest() == "1.2.3"

    def test_a_tag_without_one_is_taken_as_is(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._answering(monkeypatch, {"tag_name": "1.2.3"})
        assert updates.fetch_latest() == "1.2.3"

    @pytest.mark.parametrize("payload", [{}, {"tag_name": ""}, {"tag_name": 7}, [], "not a document"])
    def test_an_unusable_body_resolves_to_nothing(self, monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
        self._answering(monkeypatch, payload)
        assert updates.fetch_latest() is None

    def test_a_transport_failure_resolves_to_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.request

        def explode(*args: object, **kwargs: object) -> None:
            raise OSError("no route to host")

        monkeypatch.setattr(urllib.request, "urlopen", explode)
        assert updates.fetch_latest() is None
