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
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from boltzmann.blocks.memory_type import MemoryType
from boltzmann.blocks.provenance import Actor, ActorKind
from boltzmann.indices.base import IndexKind
from boltzmann.query.request import RetrievalMode
from boltzmann.retention.policy import RetentionPolicy
from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class ReconcileStrategy(StrEnum):
    """How this brain records a history it joined.

    Declared per brain rather than passed per command, because it is the same answer every time for a given
    brain and nobody wants to retype it -- but it is deliberately **not defaulted**. The three strategies land
    the same blocks and differ only in the lineage recorded, and therefore in who stays on record as the author
    of the incoming work. A default would be vitruvio deciding that on the operator's behalf, which is exactly
    what the SDK refuses to do by making the field required. A key in a file someone wrote is that person
    stating it; an absent key is nobody having stated it, and it means a fetch reconciles nothing.

    Mirrors ``boltzmann.reconcile.ReconcileStrategy`` rather than importing it: the kernel stays importable
    without the SDK, which is the seam this package exists to hold. :mod:`vitruvio.runtime.coerce` converts.
    """

    MERGE = "merge"
    """Name both histories as parents. The only one under which their snapshots -- and, once signing lands,
    their signatures -- still cover something."""
    REBASE = "rebase"
    """Replay their history onto this one. Mints new snapshot identities, so it is legitimate only before
    publication, the same rule as any lineage rewrite."""
    SQUASH = "squash"
    """Collapse their snapshots into one. Useful because an ingestion session mints many intermediate versions
    nobody cares about individually; every provenance record they produced is still kept."""


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
        dims (int | None): Expected dimensionality, checked against the loaded model. For a remote provider this
            is not a hint: the model tag carries it, the tag is what a consumer matches on, and a tag whose width
            came from whatever the network answered that day would not be a reproducible artifact. vitruvio knows
            the common models' widths already; ``config embedder test`` reports the width of one it does not.
        batch (int | None): Maximum batch size handed to the model at once.
        device (str | None): ``cpu``, ``cuda``, ``mps``. Left unset, the provider decides.
        base_url (str | None): Override the provider's endpoint. What points Ollama at another host, or an
            OpenAI-compatible gateway at itself. Not part of the model tag -- *where* a request went does not
            change where the vector lands, and putting it in the tag would make a brain unpublishable across two
            machines that reach the same model by different routes.
        options (dict[str, Any]): Provider-specific request fields, merged into the request body. OpenRouter's
            ``provider`` routing object lives here. Deliberately untyped: this is the escape hatch for a vendor
            feature vitruvio has no opinion about, and typing it would mean a schema change per vendor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    revision: str | None = None
    dims: int | None = Field(default=None, ge=1)
    batch: int | None = Field(default=None, ge=1)
    device: str | None = None
    base_url: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

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


SOURCE_NAME = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
"""What a source may be called.

Not the repository rule -- a source name never becomes part of one -- but the same spirit: it is typed on a command
line (``vitruvio source pull algebra-aula``) and printed in a table, so a space or a capital in it is a papercut for
no gain.
"""

DEFAULT_SOURCE_TIMEOUT = 300
"""How long a source gets before it is abandoned, in seconds.

Five minutes rather than the credential helper's five seconds: a source is allowed to do real work. Downloading a
semester of course material, or running OCR over it, legitimately takes minutes. The point of the bound is that
*something* eventually kills a hung fetch, not that it kills a slow one.
"""


class SourceSpec(BaseModel):
    """
    One place vitruvio pulls material from, declared rather than typed each time.

    This schema **names** a kind; it cannot **define** one, and that is its most important property. There is
    nowhere here to put a command line. A source vitruvio does not ship is a Python subclass you install on your
    own machine, so cloning a repository and running ``vitruvio source pull`` cannot execute a stranger's argv --
    the same structural move this module already makes for secrets, which have no field either.

    Attributes:
        kind (str): Which strategy acquires from this source. A built-in kind (``directory``) or one a plugin
            registers. Unknown at parse time on purpose: the set is open, and the resolver reports an unknown kind
            as a configuration error naming what is installed.
        path (str | None): A directory or file the source works from, resolved against **this file's directory**
            rather than the working directory. First-class rather than an ``options`` key precisely so that a
            plugin author does not have to remember that rule, or reimplement it.
        media_type (str | None): Override the media type inferred from each item.
        normalize_with (str | None): Which normalization pipeline to apply to what is fetched.
        license (str | None): Recorded on every block this source produces. Material arriving from a faculty
            platform or from arXiv has terms, and the place to state them once is the declaration.
        timeout (int): Seconds a single acquisition gets.
        max_bytes (int | None): Refuse an item larger than this, checked before it is read.
        options (dict[str, Any]): Kind-specific fields, validated by the subclass constructor -- the only code
            that knows the shape. Untyped because the set of kinds is open: a discriminated union is right for a
            closed set, and vitruvio cannot know a third-party plugin's fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    path: str | None = None
    media_type: str | None = None
    normalize_with: str | None = None
    license: str | None = None
    timeout: int = Field(default=DEFAULT_SOURCE_TIMEOUT, ge=1)
    max_bytes: int | None = Field(default=None, ge=1)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _require_kind(cls, value: str) -> str:
        """An empty kind resolves to nothing and would report as "unknown kind ''", which reads like a bug."""
        if not value.strip():
            raise ValueError("kind is required: name a built-in kind or one a plugin registers")
        return value.strip()


def _validate_source_names(value: dict[str, SourceSpec]) -> dict[str, SourceSpec]:
    """Keep source names safe to type, wherever a brain declares them."""
    for name in value:
        if not SOURCE_NAME.match(name):
            raise ValueError(
                f"source name {name!r} should be lowercase letters, digits and single separators (-, _): "
                f"`algebra-aula` rather than `Algebra Aula`"
            )
    return value


class BrainSpec(BaseModel):
    """
    Which brain this project is about.

    Attributes:
        path (str | None): Path to the layout, resolved *relative to the configuration file* rather than to
            the working directory. A project config that means something different depending on which
            subdirectory you ran the command from would not be a reproducibility artifact.
        publish (bool): Whether this brain may be published at all. See :attr:`NamedBrainSpec.publish`.
        reconcile (ReconcileStrategy | None): How to record a joined history. See
            :attr:`NamedBrainSpec.reconcile`.
        sources (dict[str, SourceSpec]): Where this brain acquires canonical evidence from.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str | None = None
    publish: bool = True
    reconcile: ReconcileStrategy | None = None
    sources: dict[str, SourceSpec] = Field(default_factory=dict)

    _source_names = field_validator("sources")(_validate_source_names)


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
        sources (dict[str, SourceSpec]): Where this brain acquires canonical evidence from.
        publish (bool): Whether this brain may be published. ``False`` makes ``dist push`` refuse before it packs
            anything, and ``dist push --all`` skip it.

            For a brain that is somebody else's upstream. Pulling one gives you a local working copy that is
            writable like any other -- nothing in the protocol distinguishes "a brain I authored" from "a brain I
            installed" -- so an absent-minded ``dist push`` publishes a *fork* of a shared brain under whichever
            repository this project derives, and the two lineages then diverge with nobody informed. Declaring it
            here is cheaper than remembering it, which is the whole argument: the mistake is silent, one command
            long, and made by the person who least expects to make it.

            Not a permission and not a security boundary. It stops an accident, not an intent: anyone who edits
            this file can flip it, which is the correct amount of friction for a declaration whose purpose is to
            make a deliberate act look deliberate.
        reconcile (ReconcileStrategy | None): How this brain records a history it joined, which is what lets
            ``dist fetch`` finish the job rather than stopping to ask. Absent by default, and absent means a
            fetch brings the history and reconciles nothing -- see :class:`ReconcileStrategy` for why this is
            the one setting vitruvio will not choose for you.

            Declaring it is not a licence to reconcile anything: a fetch commits only when the plan is clean,
            so this decides *how* a reconciliation is recorded, never *whether* work of yours may leave.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    reference: str | None = None
    description: str | None = None
    publish: bool = True
    reconcile: ReconcileStrategy | None = None
    sources: dict[str, SourceSpec] = Field(default_factory=dict)

    _source_names = field_validator("sources")(_validate_source_names)

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

    def sources_for(self, brain: str | None) -> dict[str, SourceSpec]:
        """Return only the sources owned by one brain.

        ``None`` identifies the single ``[brain]`` declaration. Named projects select one of
        ``[brains.<name>]`` before a source command can run, so a declaration can never float between brains.
        """
        if brain is None:
            return self.brain.sources
        spec = self.brains.get(brain)
        return spec.sources if spec is not None else {}

    def source_root(self, name: str, *, brain: str | None) -> Path | None:
        """
        Where one source's ``path`` points, resolved against the configuration file.

        The same rule as :meth:`brain_path`, and it lives here for the same reason: a relative path in a committed
        file that meant something different depending on which subdirectory the command ran from would not be a
        reproducibility artifact. A plugin author gets the resolved path and never has to know the rule.

        Args:
            name (str): The source's name in the selected brain.
            brain (str | None): The selected named brain, or ``None`` for ``[brain]``.

        Returns:
            Path | None: The resolved root, or ``None`` when there is no such source or it declares no path.
        """
        spec = self.sources_for(brain).get(name)
        if spec is None or spec.path is None:
            return None
        base = self.source.parent if self.source is not None else Path()
        return (base / Path(spec.path).expanduser()).expanduser().resolve()

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
        project_origin (Origin): Which layer selected the project -- ``--project`` or ``--config``, the
            environment, or the walk-up from the working directory. Carried for the same reason
            :attr:`brain_origin` is: an invocation can arrive in a project four ways and only one of them is
            visible in what was typed.
        actor_origin (Origin): Where the actor identity came from.
        config_file (Path | None): The file that was read, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    brain: Path
    brain_origin: Origin
    brain_name: str | None = None
    project: ProjectConfig
    project_origin: Origin = Origin.DEFAULT
    actor_origin: Origin = Origin.DEFAULT
    config_file: Path | None = None

    @property
    def project_name(self) -> str | None:
        """The project's declared name, when it declares one."""
        return self.project.project.name

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
    def sources(self) -> dict[str, SourceSpec]:
        """The declarations owned by the selected brain, and no other brain's."""
        return self.project.sources_for(self.brain_name)

    def source_root(self, name: str) -> Path | None:
        """Resolve one selected-brain source path against ``vitruvio.toml``."""
        return self.project.source_root(name, brain=self.brain_name)

    def source_config_key(self, name: str) -> str:
        """The TOML key where a source for the selected brain is stored."""
        if self.brain_name is not None:
            return f"brains.{self.brain_name}.sources.{name}"
        return f"brain.sources.{name}"

    @property
    def publish_allowed(self) -> bool:
        """
        Whether the selected brain may be published.

        Read from the named brain's declaration when this is one, and from ``[brain]`` otherwise -- a single-brain
        project must be able to say the same thing, or the answer to "can I protect this one?" would depend on
        whether the project happens to have a second brain.
        """
        if self.brain_name is not None and self.brain_name in self.project.brains:
            return self.project.brains[self.brain_name].publish
        return self.project.brain.publish

    @property
    def reconcile_strategy(self) -> ReconcileStrategy | None:
        """
        How the selected brain records a history it joined, if it says.

        Read the same way :attr:`publish_allowed` is, and for the same reason: a single-brain project must be
        able to declare it under ``[brain]``, or the answer would depend on whether the project happens to have
        a second brain.

        ``None`` is a real answer and the default one. It means nobody has chosen, and choosing is not
        vitruvio's to do -- so a caller holding ``None`` reports that and stops rather than picking.
        """
        if self.brain_name is not None and self.brain_name in self.project.brains:
            return self.project.brains[self.brain_name].reconcile
        return self.project.brain.reconcile

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
        return {"project": self.project_origin, "brain": self.brain_origin, "actor": self.actor_origin}
