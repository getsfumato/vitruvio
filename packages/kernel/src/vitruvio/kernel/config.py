"""The shape of ``vitruvio.toml``, and the resolved configuration the runtime is built from.

``vitruvio.toml`` is a reproducibility artifact and is meant to be committed: it records which brain,
which actor, which retention policy, which embedding models and which indices, so that two people running
``vitruvio search`` against the same brain get comparable answers. Anything that must *not* be committed --
API keys, registry tokens -- is read from the environment and has no field here at all. That is a
structural guarantee rather than a convention: there is nowhere in this schema to put a secret.

Two models are worth telling apart:

* :class:`ProjectConfig` is the file. Every field is optional, because a brain with no configuration at all
  must still open.
* :class:`ResolvedConfig` is the answer to "what are we actually doing", after flags, environment, file and
  saved state have been merged. It knows *where each value came from*, which is what lets
  ``vitruvio config show`` explain itself instead of just printing values.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.blocks.provenance import Actor, ActorKind
from boltzmann.indices.base import IndexKind
from boltzmann.query.request import RetrievalMode
from boltzmann.retention.policy import RetentionPolicy
from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from collections.abc import Mapping


DEFAULT_REGISTRY_HOST = "docker.io"
"""Where a derived repository lives when only an account is known.

Docker Hub because that is where an account most people already have is, and because `registry login
--from-docker` imports exactly that account. A project that publishes elsewhere sets `[registry].namespace`.
"""

REPOSITORY_SEGMENT = re.compile(r"^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*$")
"""What one path component of an OCI repository name may look like, from the distribution spec.

Enforced on a project and brain name rather than discovered at push time, because the alternative is a registry
rejecting `Álgebra II` after the artifact has already been packed -- and the error a registry returns for a
malformed name says nothing about which of the two you got wrong.
"""


class Origin(StrEnum):
    """Where a resolved value came from, reported by ``config show``.

    Ordered by precedence, weakest first, so ``max`` over origins is meaningful.
    """

    DEFAULT = "default"
    STATE = "state"
    FILE = "file"
    ENVIRONMENT = "environment"
    FLAG = "flag"


class PolicyProfile(StrEnum):
    """Named retention postures, so a brain does not have to spell out six fields to get a sane one."""

    CONSERVATIVE = "conservative"
    """Nothing canonical may be dropped, no redaction, review large cascades. The default."""
    PERMISSIVE = "permissive"
    """Canonical drops allowed, no review threshold. For a brain you are still shaping."""
    ARCHIVAL = "archival"
    """Nothing may be dropped at all: supersession and demotion only."""


class ActorSpec(BaseModel):
    """
    Who this configuration writes as.

    A write with no actor is a provenance record that lies about its own origin, so the runtime refuses
    to write when no id can be resolved rather than inventing one.

    Attributes:
        id (str | None): Stable identifier, such as an email address.
        kind (ActorKind): What sort of agent. Defaults to ``human``; an agent driving the CLI is expected
            to set ``agent`` explicitly, and the shipped skills say so.
        name (str | None): Human-readable label.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str | None = None
    kind: ActorKind = ActorKind.HUMAN
    name: str | None = None


class PolicySpec(BaseModel):
    """
    The retention posture, as a profile plus explicit overrides.

    Attributes:
        profile (PolicyProfile): The starting point.
        canonical_drop_allowed (bool | None): Override the profile's answer.
        cascade_review_threshold (int | None): Dependents a cascade may drop before review is required.
        retained_roots (int | None): How many recent snapshots stay reachable for pruning.
        redactable_media_types (list[str] | None): Which content may have its bytes destroyed. Left unset,
            redaction is forbidden entirely, which is the safe default: redaction is for law and safety,
            not for cleanup.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: PolicyProfile = PolicyProfile.CONSERVATIVE
    canonical_drop_allowed: bool | None = None
    cascade_review_threshold: int | None = None
    retained_roots: int | None = Field(default=None, ge=1)
    redactable_media_types: list[str] | None = None

    def build(self) -> RetentionPolicy:
        """
        Turn the spec into the SDK's policy object.

        Returns:
            RetentionPolicy: The policy the brain enforces.
        """
        droppable = [
            MemoryType.CANONICAL,
            MemoryType.SEMANTIC,
            MemoryType.PROCEDURAL,
            MemoryType.PROVENANCE,
        ]
        canonical = False
        review: int | None = 25
        if self.profile is PolicyProfile.PERMISSIVE:
            canonical, review = True, None
        elif self.profile is PolicyProfile.ARCHIVAL:
            # Archival is not "drop nothing canonical"; it is "drop nothing". The SDK expresses that as an
            # empty droppable set rather than as a flag, so supersession and demotion remain the only paths.
            droppable, review = [], None

        if self.canonical_drop_allowed is not None:
            canonical = self.canonical_drop_allowed
        if self.cascade_review_threshold is not None:
            review = self.cascade_review_threshold

        arguments: dict[str, Any] = {
            "droppable_modules": droppable,
            "canonical_drop_allowed": canonical,
            "cascade_review_threshold": review,
            "redactable_media_types": self.redactable_media_types,
        }
        if self.retained_roots is not None:
            arguments["retained_roots"] = self.retained_roots
        return RetentionPolicy(**arguments)


class EmbedderSpec(BaseModel):
    """
    One embedding model, named the way the registry resolves it.

    Attributes:
        provider (str): Registry key, e.g. ``hashing``, ``local-st``, ``local-siglip``, ``openai``.
        model (str): The model within that provider.
        revision (str | None): A commit sha or vendor version. Pinning it is what stops a rebuild from
            silently landing vectors in a different space than the ones already indexed.
        dims (int | None): Expected dimensionality, checked against the loaded model.
        batch (int | None): Maximum batch size handed to the model at once.
        device (str | None): ``cpu``, ``cuda``, ``mps``. Left unset, the provider decides.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    revision: str | None = None
    dims: int | None = Field(default=None, ge=1)
    batch: int | None = Field(default=None, ge=1)
    device: str | None = None

    @property
    def uri(self) -> str:
        """The ``provider:model`` string the embedder registry takes."""
        return f"{self.provider}:{self.model}"


DEFAULT_TEXT_EMBEDDER = EmbedderSpec(provider="hashing", model="bow", dims=256)
"""What vitruvio embeds with when nothing is configured and no extra is installed.

Deliberately not a real model. Feature hashing needs no weights, no network and no torch, so a bare
install can build a vector index and exercise every code path -- and its model tag says ``hashing/bow``
loudly enough that nobody mistakes the result for semantics.
"""


class IndexSpec(BaseModel):
    """
    One index, registered on one memory type.

    Attributes:
        memory_type (MemoryType): Which module it indexes.
        kind (IndexKind): Which of the six kinds it is.
        embedder (Literal["text", "vision"] | None): Which configured embedder a vector index uses.
        model_tag (str | None): Written back on the first build. On every later open it is compared against
            the embedder's own tag: a mismatch means the vectors in the index and the vectors a query would
            produce live in unrelated spaces, and the cosines between them are noise. The planner refuses
            such an index rather than degrading, because noise that ranks is worse than an absent index.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_type: MemoryType
    kind: IndexKind
    embedder: Literal["text", "vision"] | None = None
    model_tag: str | None = None


def default_indices() -> list[IndexSpec]:
    """
    The registration vitruvio recommends when a brain declares none.

    Provenance gets structural indices only: its blocks carry no prose worth embedding, and letting the
    text of a registration record compete in a similarity ranking is pure noise. Graph is registered where
    edges actually exist -- declared relations, procedure steps, and the provenance records that hold
    ``derived_from`` and ``supersedes``.

    Returns:
        list[IndexSpec]: The default index set.
    """
    structural = (IndexKind.HASH_MAP, IndexKind.BTREE, IndexKind.BITMAP)
    searchable = (MemoryType.CANONICAL, MemoryType.EPISODIC, MemoryType.SEMANTIC, MemoryType.PROCEDURAL)
    graphed = (MemoryType.SEMANTIC, MemoryType.PROCEDURAL, MemoryType.PROVENANCE)

    specs = [IndexSpec(memory_type=memory, kind=kind) for memory in MemoryType for kind in structural]
    specs += [IndexSpec(memory_type=memory, kind=IndexKind.INVERTED) for memory in searchable]
    specs += [IndexSpec(memory_type=memory, kind=IndexKind.VECTOR, embedder="text") for memory in searchable]
    specs += [IndexSpec(memory_type=memory, kind=IndexKind.GRAPH) for memory in graphed]
    return specs


class PlannerConfig(BaseModel):
    """
    The knobs of the cost-based planner.

    Defaults are the measured ones; they are configuration rather than constants because a brain on a slow
    disk and a brain on a laptop SSD do not agree about what an index costs.

    Attributes:
        mode_default (RetrievalMode): What an unqualified query means.
        objective_lambda (float): Weight on the recall term of ``J = cost + lambda * (1 - recall) * miss``.
            Zero optimises for latency alone and is useful mainly to demonstrate what that costs.
        miss_cost_us (int): What a missed answer is declared to be worth, in microseconds of latency.
        brute_threshold (int): Below this many vectors inside a filter mask, an exact scan replaces the
            approximate probe. Feeding a very selective mask into HNSW *loses* recall, because the graph
            walk visits a fixed number of nodes and only a fraction survive the filter.
        rrf_k (int): The K of reciprocal-rank fusion.
        overfetch (int): Candidate pool multiplier over the requested limit.
        graph_expand_max (int): Ceiling on requested expansion depth.
        plan_cache_size (int): Entries in the plan cache, keyed by query shape and statistics version.
        strict (bool): Turn every degradation into an error. For CI and for ``inspect doctor``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode_default: RetrievalMode = RetrievalMode.AUTO
    objective_lambda: float = Field(default=1.0, ge=0.0)
    miss_cost_us: int = Field(default=250_000, ge=0)
    brute_threshold: int = Field(default=2048, ge=0)
    rrf_k: int = Field(default=60, ge=1)
    overfetch: int = Field(default=4, ge=1)
    graph_expand_max: int = Field(default=3, ge=0)
    plan_cache_size: int = Field(default=512, ge=0)
    strict: bool = False


class RegistrySpec(BaseModel):
    """
    Where this project's brains publish, and how to address them.

    Credentials are absent by design: they come from the environment or from vitruvio's credential store,
    never from a file that gets committed.

    ``reference`` addresses one repository and is what a single-brain project sets. ``namespace`` addresses a
    whole account, and is what a project with several brains sets instead -- each brain then derives its own
    repository under it, so adding a subject to a project does not mean editing a registry reference.

    Attributes:
        reference (str | None): ``<host>/<namespace>/<repo>``, without a tag. One repository, one brain.
        namespace (str | None): ``<host>/<account>``, under which each brain derives a repository. Left unset,
            it is taken from whichever Docker account you are logged in as -- which is the point of
            ``registry login --from-docker``: log in once, and every brain in the project knows where it goes.
        tag (str): The tag to use when none is given.
        insecure (bool): Allow plain HTTP, for a local registry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str | None = None
    namespace: str | None = None
    tag: str = "latest"
    insecure: bool = False

    @field_validator("namespace")
    @classmethod
    def _reject_repository_shaped_namespace(cls, value: str | None) -> str | None:
        """A namespace is a host and an account, not a repository: three segments means a repo was pasted in."""
        if value and len(value.strip("/").split("/")) > 2:
            raise ValueError(
                f"namespace {value!r} looks like a repository; it should be <host>/<account>, and each brain "
                f"derives its own repository under it. Set [registry].reference for a single-brain project"
            )
        return value.strip("/") if value else value

    @field_validator("reference")
    @classmethod
    def _reject_tagged_reference(cls, value: str | None) -> str | None:
        """A repository and a tag are separate fields; conflating them produces `repo:tag:tag`."""
        if value and ":" in value.rsplit("/", 1)[-1]:
            raise ValueError(f"reference {value!r} carries a tag; set [registry].tag instead")
        return value


class IngestSpec(BaseModel):
    """
    Defaults for the ingestion path.

    Attributes:
        default_pipeline (str | None): Normalization pipeline applied when none is named.
        proposer (str): Which candidate proposer ``ingest run`` uses. ``file`` reads a candidate set an
            agent wrote, which is the path that keeps the model outside the runtime.
        allowed_memory_types (list[MemoryType] | None): What a proposer may propose. Canonical and
            provenance are never proposable -- the protocol writes those.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    default_pipeline: str | None = None
    proposer: str = "file"
    allowed_memory_types: list[MemoryType] | None = None


class BrainSpec(BaseModel):
    """
    Which brain this project is about.

    Attributes:
        path (str | None): Path to the layout, resolved *relative to the configuration file* rather than to
            the working directory. A project config that means something different depending on which
            subdirectory you ran the command from would not be a reproducibility artifact.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str | None = None


class NamedBrainSpec(BaseModel):
    """
    One brain of a project that holds several.

    A project is a set of brains that share an actor, a policy, an embedder and a registry account -- one per
    subject, per client, per team. Keeping them separate rather than in one brain is what makes each publishable,
    installable and droppable on its own: a consumer who wants one subject should not pull five.

    Attributes:
        path (str): Path to the layout, resolved relative to the configuration file.
        reference (str | None): Where this brain publishes, when the derived repository is not what you want.
            Usually absent -- the whole point of a project namespace is that each brain derives its own.
        description (str | None): What this brain holds, for ``project show``. A set of six named brains is
            unreadable without one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    reference: str | None = None
    description: str | None = None

    @field_validator("reference")
    @classmethod
    def _reject_tagged_reference(cls, value: str | None) -> str | None:
        """Same rule as the registry's: a repository and a tag are separate fields."""
        if value and ":" in value.rsplit("/", 1)[-1]:
            raise ValueError(f"reference {value!r} carries a tag; set [registry].tag instead")
        return value


class ProjectSpec(BaseModel):
    """
    The project a set of brains belongs to.

    Attributes:
        name (str | None): The project's name. It prefixes every derived repository, so
            ``facultad`` + ``algebra`` publishes to ``<namespace>/facultad-algebra``. That prefix is what keeps
            two projects with a subject of the same name from colliding in one registry account.
        description (str | None): What the project is.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        """A project name becomes part of a repository, so it lives under OCI's naming rules."""
        if value is not None and not REPOSITORY_SEGMENT.match(value):
            raise ValueError(
                f"project name {value!r} cannot be part of a repository name: use lowercase letters, digits, "
                f"and single separators (-, _, .)"
            )
        return value


class ProjectConfig(BaseModel):
    """
    The parsed ``vitruvio.toml``.

    Every section is optional. A brain with no configuration file opens with defaults, which is what makes
    ``vitruvio brain init`` followed immediately by ``vitruvio search`` work.

    Attributes:
        project (ProjectSpec): The project these brains belong to.
        brain (BrainSpec): The single brain, for a project that has one.
        brains (dict[str, NamedBrainSpec]): The named brains, for a project that has several. A subject per
            brain, a client per brain -- whatever the unit of "someone might want only this one" is.
        actor (ActorSpec): Who writes.
        policy (PolicySpec): What may be removed.
        embedding (dict[str, EmbedderSpec]): Keyed ``text`` and ``vision``.
        index (list[IndexSpec]): Which indices to register. Empty means "use the defaults".
        planner (PlannerConfig): Planner knobs.
        registry (RegistrySpec): Publication target.
        ingest (IngestSpec): Ingestion defaults.
        source (Path | None): Which file this came from. Not a TOML key -- set by the loader, and excluded
            from serialization so that a round-trip does not write it back into the file.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project: ProjectSpec = ProjectSpec()
    brain: BrainSpec = BrainSpec()
    brains: dict[str, NamedBrainSpec] = Field(default_factory=dict)
    actor: ActorSpec = ActorSpec()
    policy: PolicySpec = PolicySpec()
    embedding: dict[str, EmbedderSpec] = Field(default_factory=dict)
    index: list[IndexSpec] = Field(default_factory=list)
    planner: PlannerConfig = PlannerConfig()
    registry: RegistrySpec = RegistrySpec()
    ingest: IngestSpec = IngestSpec()
    source: Path | None = Field(default=None, exclude=True)

    @field_validator("brains")
    @classmethod
    def _validate_brain_names(cls, value: dict[str, NamedBrainSpec]) -> dict[str, NamedBrainSpec]:
        """A brain name becomes part of a repository, so it lives under the same rules as the project's."""
        for name in value:
            if not REPOSITORY_SEGMENT.match(name):
                raise ValueError(
                    f"brain name {name!r} cannot be part of a repository name: use lowercase letters, digits, "
                    f"and single separators (-, _, .). `analisis-ii` rather than `Análisis II`"
                )
        return value

    def brain_path(self, name: str) -> Path | None:
        """
        Where one named brain lives, resolved against the configuration file.

        Args:
            name (str): The brain's name in this project.

        Returns:
            Path | None: The layout path, or ``None`` if this project has no such brain.
        """
        spec = self.brains.get(name)
        if spec is None:
            return None
        base = self.source.parent if self.source is not None else Path()
        return (base / spec.path).expanduser().resolve()

    def repository_for(self, name: str, *, account: str | None = None) -> str | None:
        """
        The repository one named brain publishes to.

        Three layers, most specific first: the brain's own ``reference``, then a repository derived from the
        project namespace, and nothing when neither is available.

        The derivation is ``<namespace>/<project>-<name>``. The project prefix is not decoration: without it,
        two projects that each have a brain called ``notes`` would publish to one repository and silently
        overwrite each other, and the second one would only find out when a pull returned the wrong subject.

        Args:
            name (str): The brain's name.
            account (str | None): A registry account to fall back on when no namespace is configured -- in
                practice the Docker login, so that logging in once is enough.

        Returns:
            str | None: The repository, without a tag, or ``None`` when nothing names one.
        """
        spec = self.brains.get(name)
        if spec is not None and spec.reference:
            return spec.reference

        namespace = self.registry.namespace or (f"{DEFAULT_REGISTRY_HOST}/{account}" if account else None)
        if not namespace:
            return None

        prefix = f"{self.project.name}-" if self.project.name else ""
        return f"{namespace}/{prefix}{name}"

    @property
    def text_embedder(self) -> EmbedderSpec:
        """The configured text embedder, or the zero-dependency default."""
        return self.embedding.get("text", DEFAULT_TEXT_EMBEDDER)

    @property
    def vision_embedder(self) -> EmbedderSpec | None:
        """The configured vision embedder, if this brain indexes images at all."""
        return self.embedding.get("vision")

    @property
    def indices(self) -> list[IndexSpec]:
        """The declared index set, or the recommended default when none is declared."""
        return self.index or default_indices()


class ResolvedConfig(BaseModel):
    """
    What the runtime was actually asked to do, and where each answer came from.

    Attributes:
        brain (Path): The selected brain's layout directory, absolute.
        brain_origin (Origin): Which layer of precedence selected it.
        brain_name (str | None): Which of the project's named brains this is, when it is one. Carried because
            it is what a derived repository is built from -- a path cannot tell you that ``./brains/algebra``
            publishes to ``facultad-algebra``.
        project (ProjectConfig): The merged project configuration.
        actor_origin (Origin): Where the actor identity came from.
        config_file (Path | None): The file that was read, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    brain: Path
    brain_origin: Origin
    brain_name: str | None = None
    project: ProjectConfig
    actor_origin: Origin = Origin.DEFAULT
    config_file: Path | None = None

    def repository(self, account: str | None = None) -> str | None:
        """
        Where this brain publishes.

        A named brain derives its repository from the project; an unnamed one uses ``[registry].reference``.

        Args:
            account (str | None): A registry account to fall back on when no namespace is configured.

        Returns:
            str | None: The repository, without a tag, or ``None`` when nothing names one.
        """
        if self.brain_name is not None:
            derived = self.project.repository_for(self.brain_name, account=account)
            if derived is not None:
                return derived
        return self.project.registry.reference

    @property
    def derived(self) -> Path:
        """The brain's derived-state directory."""
        from vitruvio.kernel.paths import derived_dir

        return derived_dir(self.brain)

    def actor(self) -> Actor:
        """
        The SDK actor to record in provenance.

        Returns:
            Actor: The resolved actor.

        Raises:
            ActorUnknownError: If no identifier could be determined. Refusing here is deliberate: the
                alternative is a provenance record attributing a write to nobody, which is worse than a
                failed command.
        """
        from vitruvio.kernel.errors import ActorUnknownError

        spec = self.project.actor
        if not spec.id:
            raise ActorUnknownError(
                "no actor identity is configured, and every write is attributed in provenance",
                hint=(
                    'pass --actor, set VITRUVIO_ACTOR_ID, or add [actor] id = "you@example.com" to '
                    f"{self.config_file or 'vitruvio.toml'}"
                ),
            )
        return Actor(id=spec.id, kind=spec.kind, name=spec.name)

    def policy(self) -> RetentionPolicy:
        """The retention policy this brain enforces."""
        return self.project.policy.build()

    def origins(self) -> Mapping[str, Origin]:
        """A summary of where the load-bearing values came from, for ``config show``."""
        return {"brain": self.brain_origin, "actor": self.actor_origin}
