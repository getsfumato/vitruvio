"""Secrets must be unprintable by accident, and paths must never escape the XDG roots."""

from __future__ import annotations

from pathlib import Path

import pytest

from vitruvio.kernel import (
    REDACTED,
    Secret,
    cache_home,
    config_home,
    derived_dir,
    from_environment,
    is_layout,
    prepare_model_cache,
    provider_key,
    registry_credentials,
    state_home,
)
from vitruvio.kernel.secrets import PROVIDER_VARIABLES

TOKEN = "dckr_pat_AbCdEfGhIjKlMnOpQrSt"


class TestSecret:
    def test_str_repr_and_fstring_all_redact(self) -> None:
        """Every accidental path to printing has to be closed, not just the obvious one."""
        secret = Secret(TOKEN, source="env:DOCKER_TOKEN")
        assert str(secret) == REDACTED
        assert f"{secret}" == REDACTED
        assert TOKEN not in repr(secret)
        assert TOKEN not in f"{secret!r}"
        assert TOKEN not in format(secret)  # __format__, the path an f-string takes

    def test_reveal_is_the_only_way_out(self) -> None:
        assert Secret(TOKEN, source="flag").reveal() == TOKEN

    def test_masked_identifies_without_disclosing(self) -> None:
        masked = Secret(TOKEN, source="keyring").masked()
        assert masked.startswith("dckr")
        assert masked.endswith("QrSt")
        assert TOKEN not in masked

    def test_a_short_value_is_not_partially_masked(self) -> None:
        """Showing eight characters of a ten-character secret is not masking it."""
        assert Secret("short", source="flag").masked() == REDACTED

    def test_falsiness_tracks_emptiness(self) -> None:
        assert not Secret("", source="env:UNSET")
        assert Secret("x", source="flag")


class TestEnvironmentResolution:
    def test_first_variable_wins_and_reports_its_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VITRUVIO_OPENAI_API_KEY", "prefixed")
        monkeypatch.setenv("OPENAI_API_KEY", "bare")
        secret = provider_key("openai")
        assert secret is not None
        assert secret.reveal() == "prefixed"
        assert secret.source == "env:VITRUVIO_OPENAI_API_KEY"

    def test_the_bare_name_is_honoured_so_an_existing_shell_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "bare")
        secret = provider_key("anthropic")
        assert secret is not None
        assert secret.reveal() == "bare"

    def test_whitespace_only_counts_as_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "   ")
        assert provider_key("openai") is None

    def test_an_unknown_provider_needs_no_key(self) -> None:
        assert provider_key("hashing") is None

    def test_docker_variables_are_honoured_for_the_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DOCKER_USERNAME/DOCKER_TOKEN is the pair already present in CI jobs."""
        monkeypatch.setenv("DOCKER_USERNAME", "alex")
        monkeypatch.setenv("DOCKER_TOKEN", TOKEN)
        username, token = registry_credentials()
        assert username == "alex"
        assert token is not None
        assert token.reveal() == TOKEN

    def test_the_vitruvio_names_win_over_the_docker_ones(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOCKER_USERNAME", "docker-account")
        monkeypatch.setenv("VITRUVIO_REGISTRY_USERNAME", "vitruvio-account")
        username, _ = registry_credentials()
        assert username == "vitruvio-account"

    def test_every_provider_variable_is_cleared_by_the_test_harness(self) -> None:
        """If this fails, conftest's CLEARED list has fallen behind and a developer's real key is in scope."""
        for names in PROVIDER_VARIABLES.values():
            for name in names:
                assert from_environment(name) is None, f"{name} leaked into the test environment"


class TestPaths:
    def test_xdg_roots_are_honoured(self, isolated_environment: Path) -> None:
        assert config_home().parent == isolated_environment / "xdg_config_home"
        assert state_home().parent == isolated_environment / "xdg_state_home"
        assert cache_home().parent == isolated_environment / "xdg_cache_home"

    def test_a_relative_xdg_value_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The spec says these hold absolute paths; honouring a relative one puts state wherever you ran from."""
        monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
        assert config_home() == Path.home() / ".config" / "vitruvio"

    def test_the_derived_directory_is_a_sibling_of_the_sdk_sidecar(self, tmp_path: Path) -> None:
        """Writing into the SDK's own `boltzmann/` works until the day it does not."""
        assert derived_dir(tmp_path) == tmp_path / ".vitruvio"
        assert "boltzmann" not in str(derived_dir(tmp_path))

    def test_model_cache_sets_the_library_variables_without_overriding_a_deliberate_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        monkeypatch.setenv("HF_HOME", "/somewhere/deliberate")
        monkeypatch.delenv("SENTENCE_TRANSFORMERS_HOME", raising=False)
        cache = prepare_model_cache()
        assert os.environ["HF_HOME"] == "/somewhere/deliberate"
        assert os.environ["SENTENCE_TRANSFORMERS_HOME"] == str(cache)

    def test_a_layout_is_recognised_by_its_marker(self, tmp_path: Path) -> None:
        assert not is_layout(tmp_path)
        (tmp_path / "oci-layout").write_text("{}")
        assert is_layout(tmp_path)
