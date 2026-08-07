"""The vitruvio kernel: configuration, brain discovery, identity, paths and errors.

This package answers three questions and nothing else: *which brain*, *who am I*, and *under what policy*.
It depends on pydantic and the Boltzmann SDK's value types, and on nothing heavier -- no index engine, no
embedder, no registry client. That is deliberate and load-bearing: ``vitruvio config show`` and
``vitruvio brain use`` must start in tens of milliseconds, and if configuration lived alongside the runtime
then importing it would drag in ``usearch`` and the index registry.

Every other vitruvio package imports this one; this one imports no other vitruvio package.
"""

from __future__ import annotations

from vitruvio.kernel.config import (
    DEFAULT_TEXT_EMBEDDER,
    ActorSpec,
    BrainSpec,
    EmbedderSpec,
    IndexSpec,
    IngestSpec,
    Origin,
    PlannerConfig,
    PolicyProfile,
    PolicySpec,
    ProjectConfig,
    RegistrySpec,
    ResolvedConfig,
    default_indices,
)
from vitruvio.kernel.discovery import (
    ENV_ACTOR_ID,
    ENV_ACTOR_KIND,
    ENV_BRAIN,
    ENV_CONFIG,
    find_config_file,
    load_project,
    parse_actor_kind,
    read_state,
    remember_brain,
    resolve,
    update_config,
    write_state,
)
from vitruvio.kernel.errors import (
    ActorUnknownError,
    BrainNotFoundError,
    BrainNotSelectedError,
    CandidatesRejectedError,
    ConfigError,
    CredentialError,
    ExitCode,
    VitruvioError,
)
from vitruvio.kernel.paths import (
    CONFIG_FILE,
    DERIVED_DIR,
    cache_home,
    config_home,
    credentials_file,
    derived_dir,
    is_layout,
    model_cache,
    prepare_model_cache,
    state_file,
    state_home,
)
from vitruvio.kernel.secrets import (
    REDACTED,
    TOKEN_URL,
    Secret,
    from_environment,
    provider_key,
    registry_credentials,
)
from vitruvio.kernel.version import __version__

__all__ = [
    "CONFIG_FILE",
    "DEFAULT_TEXT_EMBEDDER",
    "DERIVED_DIR",
    "ENV_ACTOR_ID",
    "ENV_ACTOR_KIND",
    "ENV_BRAIN",
    "ENV_CONFIG",
    "REDACTED",
    "TOKEN_URL",
    "ActorSpec",
    "ActorUnknownError",
    "CandidatesRejectedError",
    "BrainNotFoundError",
    "BrainNotSelectedError",
    "BrainSpec",
    "ConfigError",
    "CredentialError",
    "EmbedderSpec",
    "ExitCode",
    "IndexSpec",
    "IngestSpec",
    "Origin",
    "PlannerConfig",
    "PolicyProfile",
    "PolicySpec",
    "ProjectConfig",
    "RegistrySpec",
    "ResolvedConfig",
    "Secret",
    "VitruvioError",
    "__version__",
    "cache_home",
    "config_home",
    "credentials_file",
    "default_indices",
    "derived_dir",
    "find_config_file",
    "from_environment",
    "is_layout",
    "load_project",
    "model_cache",
    "parse_actor_kind",
    "prepare_model_cache",
    "provider_key",
    "read_state",
    "registry_credentials",
    "remember_brain",
    "resolve",
    "state_file",
    "state_home",
    "update_config",
    "write_state",
]
