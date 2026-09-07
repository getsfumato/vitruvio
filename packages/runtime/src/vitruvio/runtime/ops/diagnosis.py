"""What would disappoint somebody about this brain, in one pass.

``inspect doctor`` used to live entirely in the CLI and checked five things: which optional extras import, which
brain is selected, whether an actor is configured, whether the roots verify, and how large the model cache is. The
documentation promised more -- stale indices, missing modules, model-tag mismatches, tombstoned blocks, an unreachable
registry -- and doctor reported a healthy setup while every one of those held. The diagnosis lives here now, as one
runtime operation, so that the CLI renders it and any later interface asks the same question of the same code.

Every probe opens the brain at ``INSPECT`` at most: no index engine, no embedder, no model. Index sidecars are read
by their headers, off disk, which is how a vector index built with an embedder this machine cannot even construct is
still reported. The registry is not contacted unless asked, because a check that runs "after a pull and before a
publish" has to be runnable with no network. Each probe reports its own failure as a row rather than aborting the
pass: one broken thing is exactly when the rest of the report is needed.

Every row is declared once, in :data:`CHECKS`: a stable ``code`` a machine branches on, the label a person reads,
and the remedy. The documentation's table is tested against that declaration, so the two cannot drift again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vitruvio.kernel import Origin, ResolvedConfig, is_layout, model_cache
from vitruvio.runtime.mapping import translate
from vitruvio.runtime.ops.embedders import EmbedderOps
from vitruvio.runtime.ops.inspection import InspectionOps
from vitruvio.runtime.ops.lifecycle import LifecycleOps
from vitruvio.runtime.ops.remote import RemoteOps
from vitruvio.runtime.session import BrainSession

if TYPE_CHECKING:
    from vitruvio.indices.format import Header

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"
SEVERITIES = (OK, WARN, FAIL, SKIP)
"""What a row can say about itself. ``skip`` is for a check that was not run -- the registry, unless asked."""


@dataclass(frozen=True, slots=True)
class Check:
    """
    One thing doctor can report, declared once.

    Attributes:
        code (str): Stable and machine-readable; a caller branches on it, and the documentation lists it.
        label (str): What a person reads in the first column.
        remedy (str | None): The next action when the check trips. ``None`` when there is nothing to do about it.
    """

    code: str
    label: str
    remedy: str | None


CHECKS: tuple[Check, ...] = (
    Check("env.extra.oras", "oras (registry transport)", "install pyboltzmann[oci]"),
    Check("env.extra.usearch", "usearch (vector index)", "reinstall vitruvio-indices, which depends on it"),
    Check("env.extra.pyroaring", "pyroaring (bitmap index)", "reinstall vitruvio-indices, which depends on it"),
    Check("env.extra.sentence_transformers", "sentence-transformers (local text)", "install vitruvio[local]"),
    Check("env.extra.pypdfium2", "pillow + pypdfium2 (vision, and previews)", "install vitruvio[vision]"),
    Check("env.extra.keyring", "keyring (credential store)", "install vitruvio[keyring]"),
    Check("config.invalid", "config", "fix vitruvio.toml; `vitruvio config validate` names the field"),
    Check("config.actor", "actor", "`vitruvio config set actor.id you@example.org`, or pass --actor"),
    Check("config.collaborators", "collaborators", None),
    Check("brain.layout", "brain", "pass --brain PATH, run `vitruvio brain use PATH`, or `vitruvio brain init PATH`"),
    Check(
        "brain.integrity", "integrity", "run `vitruvio brain verify`; if it fails, pull the brain again from its origin"
    ),
    Check(
        "modules.installed",
        "modules",
        "register evidence with `vitruvio source register FILE`, or `vitruvio dist pull`",
    ),
    Check("modules.partial", "modules", "run `vitruvio dist pull` without --module to install the rest"),
    Check("blocks.missing", "blocks", "run `vitruvio dist pull` to fetch the layers that are absent"),
    Check(
        "blocks.tombstoned",
        "blocks",
        "nothing to fix: redaction is lawful; `vitruvio inspect resolvability` lists them",
    ),
    Check(
        "blocks.unattributed",
        "attribution",
        "re-apply the catalog manifest with `vitruvio catalog apply`; `vitruvio inspect blocks MODULE` names them",
    ),
    Check("indices.unbuilt", "indices", "run `vitruvio index build`"),
    Check("indices.stale", "indices", "run `vitruvio index build`"),
    Check(
        "indices.model_mismatch",
        "vector index",
        "configure the embedder the index was built with, or rebuild under this one with `vitruvio index build --force`",
    ),
    Check(
        "embedder.unavailable",
        "embedder",
        "install the extra for the configured provider; `vitruvio config embedder list` names it",
    ),
    Check(
        "embedder.semantic",
        "embedder",
        "set a real model under [embedding.text]; `vitruvio config embedder list` shows what this build can run",
    ),
    Check("registry.reference", "registry", "set [registry].reference or namespace in vitruvio.toml"),
    Check("registry.credentials", "registry", "run `vitruvio registry login HOST`"),
    Check(
        "registry.reachable",
        "registry",
        "check the host and the network; --insecure for plain HTTP; `vitruvio registry login` if it refused you",
    ),
    Check("cache.models", "model cache", None),
)
"""Every code doctor can emit. The order is the order rows appear in."""

_BY_CODE: dict[str, Check] = {check.code: check for check in CHECKS}

EXTRAS: tuple[tuple[str, str, str, str], ...] = (
    ("env.extra.oras", "oras", WARN, "pyboltzmann[oci]"),
    ("env.extra.usearch", "usearch", FAIL, "vitruvio-indices, which depends on it"),
    ("env.extra.pyroaring", "pyroaring", FAIL, "vitruvio-indices, which depends on it"),
    ("env.extra.sentence_transformers", "sentence_transformers", WARN, "vitruvio[local]"),
    ("env.extra.pypdfium2", "pypdfium2", WARN, "vitruvio[vision]"),
    ("env.extra.keyring", "keyring", WARN, "vitruvio[keyring]"),
)
"""The importable modules doctor probes: code, import name, severity when absent, and what installs it.

Two of them fail rather than warn. ``usearch`` and ``pyroaring`` are dependencies of vitruvio-indices, not extras,
so their absence means a broken install rather than a feature nobody asked for.
"""


def row(code: str, severity: str, detail: str, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    One doctor row, in the one shape every row has.

    Args:
        code (str): A code from :data:`CHECKS`.
        severity (str): One of :data:`SEVERITIES`.
        detail (str): What was found, for a person.
        data (dict[str, Any] | None): What was found, for a machine: counts, names, tags.

    Returns:
        dict[str, Any]: ``check``, ``code``, ``ok``, ``severity``, ``detail``, ``remedy`` and ``data``. ``ok`` is
        true for ``ok`` and ``skip``; ``remedy`` is set only when the row trips, because advice on a healthy row
        reads as an instruction.
    """
    check = _BY_CODE[code]
    if severity not in SEVERITIES:
        raise ValueError(f"{severity!r} is not a doctor severity; expected one of {', '.join(SEVERITIES)}")
    return {
        "check": check.label,
        "code": code,
        "ok": severity in (OK, SKIP),
        "severity": severity,
        "detail": detail,
        "remedy": check.remedy if severity in (WARN, FAIL) else None,
        "data": dict(data or {}),
    }


def _tag_differences(held: Any, spec: Any) -> list[str]:
    """
    How a sidecar's model tag disagrees with the configured embedder, field by field.

    Only the fields the configuration actually states are compared: provider and model always, revision and width when
    pinned. The rest of the tag -- dtype, pooling, the projection and chunker identities -- is the embedder's own to
    render, and comparing it would mean constructing the embedder, which is the one thing this check avoids.

    Args:
        held (Any): The parsed tag from the sidecar header.
        spec (Any): The configured :class:`~vitruvio.kernel.EmbedderSpec`.

    Returns:
        list[str]: ``field: held vs configured`` per differing field; empty when they agree.
    """
    differing: list[str] = []
    if held.provider != spec.provider:
        differing.append(f"provider: {held.provider} vs {spec.provider}")
    if held.model != spec.model:
        differing.append(f"model: {held.model} vs {spec.model}")
    if spec.revision and held.revision != spec.revision:
        differing.append(f"revision: {held.revision} vs {spec.revision}")
    if spec.dims and held.dimensions != spec.dims:
        differing.append(f"dimensions: {held.dimensions} vs {spec.dims}")
    return differing


def _per_module(counts: dict[str, int]) -> str:
    """``semantic 2, canonical 1`` -- the modules that have a count, and the count."""
    return ", ".join(f"{kind} {count}" for kind, count in sorted(counts.items()) if count)


class DiagnosisOps:
    """Diagnosis, as one operation."""

    def __init__(self, session: BrainSession) -> None:
        """
        Args:
            session (BrainSession): The shared session.
        """
        self.session = session

    @property
    def config(self) -> ResolvedConfig:
        """The resolved configuration, read through the session that owns it."""
        return self.session.config

    def doctor(self, *, registry: bool = False, local: Path | None = None, anonymous: bool = False) -> dict[str, Any]:
        """
        Everything about this environment and this brain that would disappoint somebody, as rows.

        Reports rather than fixes, and raises nothing a probe can catch: a broken setup is what this exists to
        describe. Offline unless ``registry`` is set, and never heavier than ``INSPECT`` -- index sidecars are read
        by their headers, so a vector index built with an embedder this machine cannot construct is still reported.

        Args:
            registry (bool): Also ask the configured registry for one manifest, read-only. Off by default, so the
                check can run with no network at all.
            local (Path | None): Probe a filesystem registry of OCI layouts rooted here instead of a remote one.
            anonymous (bool): Probe without credentials.

        Returns:
            dict[str, Any]: ``checks``, one row per finding -- each with a stable ``code``, a ``severity`` of ``ok``,
            ``warn``, ``fail`` or ``skip``, a ``detail`` for a person, a ``remedy`` when the row trips and ``data``
            for a machine -- plus ``failures``, ``warnings``, a ``verdict`` and whether the registry was probed.
        """
        rows: list[dict[str, Any]] = []
        rows.extend(self._environment())
        rows.extend(self._actor())
        if self._brain(rows):
            probes: tuple[tuple[str, Callable[[], list[dict[str, Any]]]], ...] = (
                ("brain.integrity", self._integrity),
                ("modules.installed", self._modules),
                ("blocks.missing", self._blocks),
                ("blocks.unattributed", self._attribution),
                ("indices.stale", self._indices),
            )
            for code, probe in probes:
                rows.extend(self._guarded(code, probe))
        rows.extend(self._guarded("embedder.unavailable", self._embedders))
        rows.extend(self._registry(registry=registry, local=local, anonymous=anonymous))
        rows.extend(self._cache())

        failures = sum(1 for item in rows if item["severity"] == FAIL)
        warnings = sum(1 for item in rows if item["severity"] == WARN)
        return {
            "checks": rows,
            "failures": failures,
            "warnings": warnings,
            "verdict": FAIL if failures else WARN if warnings else OK,
            "registry_probed": registry,
        }

    # --- Probes -------------------------------------------------------------------------------------------------

    def _guarded(self, code: str, probe: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """Run one probe; a probe that raises becomes a failing row under its own code, so the rest still report."""
        try:
            return probe()
        except Exception as error:  # doctor describes a broken setup, so it must not fail on one
            translated = translate(error)
            return [row(code, FAIL, f"{translated.code}: {translated.message}", data={"code": translated.code})]

    def _environment(self) -> list[dict[str, Any]]:
        """Which optional modules import. An absent extra is a feature nobody asked for; an absent dependency is not."""
        from importlib.util import find_spec

        rows: list[dict[str, Any]] = []
        for code, module_name, severity, extra in EXTRAS:
            present = find_spec(module_name) is not None
            detail = "installed" if present else f"absent -- install {extra}"
            rows.append(
                row(code, OK if present else severity, detail, data={"module": module_name, "installed": present})
            )
        return rows

    def _actor(self) -> list[dict[str, Any]]:
        actor = self.config.project.actor.id
        detail = actor or "not set -- writes will be refused, because every write is attributed"
        rows = [row("config.actor", OK if actor else WARN, detail, data={"actor": actor or None})]
        # Informational, never a fault: a person writing alone declares nobody. Shown because the declaration is now
        # the only set of parties a write may record, and an `--assisted-by` outside it is refused.
        declared = [spec.id for spec in self.config.project.assisted_by]
        detail = (
            f"{', '.join(declared)} ({self.config.collaborators_origin.value})"
            if declared
            else "none declared -- writes record no assistant; declare agents under assisted_by to record them"
        )
        rows.append(row("config.collaborators", OK, detail, data={"collaborators": declared}))
        return rows

    def _brain(self, rows: list[dict[str, Any]]) -> bool:
        """
        Whether a brain is selected and is one, reported as a row.

        Returns:
            bool: True when the probes that open the brain can run.
        """
        config = self.config
        origin = config.brain_origin.value
        if config.brain_origin is Origin.DEFAULT:
            # What `resolve(require_brain=False)` hands back when nothing selected one: a stand-in that is never
            # opened. There is no brain to open, and saying so is the whole row.
            rows.append(row("brain.layout", FAIL, "no brain is selected", data={"origin": origin}))
            return False
        selected = config.brain
        data = {"brain": str(selected), "origin": origin}
        if not is_layout(selected):
            detail = "does not exist" if not selected.exists() else "is not an OCI layout"
            rows.append(row("brain.layout", FAIL, f"{selected} {detail} (selected by {origin})", data=data))
            return False
        rows.append(row("brain.layout", OK, f"{selected} (selected by {origin})", data=data))
        return True

    def _integrity(self) -> list[dict[str, Any]]:
        state = LifecycleOps(self.session).verify()
        verified = bool(state["verified"])
        detail = f"{state['block_count']} blocks verify" if verified else "the roots do not match the blocks"
        data = {"verified": verified, "block_count": state["block_count"]}
        return [row("brain.integrity", OK if verified else FAIL, detail, data=data)]

    def _modules(self) -> list[dict[str, Any]]:
        """What is installed, and whether a pull left some of the published modules behind."""
        state = LifecycleOps(self.session).state()
        installed = [str(item) for item in state["installed"]]
        detail = ", ".join(installed) if installed else "nothing is installed yet"
        rows = [row("modules.installed", OK if installed else WARN, detail, data={"installed": installed})]

        origin = state.get("origin") or {}
        if origin:
            partial = bool(origin.get("partial"))
            where = f"{origin.get('reference')}:{origin.get('tag')}"
            detail = (
                f"a selective pull from {where}: the modules it did not ask for are absent, and a push over that tag "
                "is refused"
                if partial
                else f"a complete pull from {where}"
            )
            rows.append(row("modules.partial", WARN if partial else OK, detail, data={"origin": origin}))
        return rows

    def _blocks(self) -> list[dict[str, Any]]:
        """Readable, tombstoned, or simply absent -- three different things, and only the last is a fault."""
        counts = InspectionOps(self.session).resolvability()["counts"]
        missing = sum(counts["missing"].values())
        tombstoned = sum(counts["tombstoned"].values())
        resolvable = sum(counts["resolvable"].values())

        rows: list[dict[str, Any]] = []
        if missing:
            detail = f"{missing} named blocks are absent from the store: {_per_module(counts['missing'])}"
            rows.append(row("blocks.missing", FAIL, detail, data={"missing": counts["missing"]}))
        if tombstoned:
            detail = (
                f"{tombstoned} blocks are tombstoned -- redacted under policy, still verifiable members: "
                f"{_per_module(counts['tombstoned'])}"
            )
            rows.append(row("blocks.tombstoned", WARN, detail, data={"tombstoned": counts["tombstoned"]}))
        if not rows:
            rows.append(
                row("blocks.missing", OK, f"every named block resolves ({resolvable})", data={"resolvable": resolvable})
            )
        return rows

    def _attribution(self) -> list[dict[str, Any]]:
        """
        Every block names who created it, or the ledger has a hole a reader will mistake for an answer.

        A creation record is a registration or a derivation naming the block. A block with neither reads as
        "unknown" in every list and as "no creation provenance names this block" in the audit -- the same words
        a genuinely unattributed block would get, which is why it is a finding and not a curiosity. Catalog
        structure committed before pyboltzmann 0.9.1 is the known case, and re-applying the manifest is its remedy.
        """
        from boltzmann.blocks.memory_type import MemoryType

        from vitruvio.runtime.assembly import Capability
        from vitruvio.runtime.authorship import CREATION_RECORDS
        from vitruvio.runtime.provenance import decode_record

        brain = self.session.brain(Capability.INSPECT)
        installed = brain.snapshot().installed
        created: set[str] = set()
        if MemoryType.PROVENANCE in installed:
            provenance = brain.module(MemoryType.PROVENANCE)
            readable = provenance.resolvable()
            for identity in provenance.block_ids:
                if not readable.get(identity, True):
                    continue
                record = decode_record(provenance.get(identity))
                if record is None or record.get("record_type") not in CREATION_RECORDS:
                    continue
                subject = record.get("block")
                if isinstance(subject, str):
                    created.add(subject)

        unattributed: dict[str, int] = {}
        attributed = 0
        for memory_type in installed:
            if memory_type is MemoryType.PROVENANCE:
                continue
            module = brain.module(memory_type)
            readable = module.resolvable()
            for identity in module.block_ids:
                if not readable.get(identity, True):
                    continue
                if str(identity) in created:
                    attributed += 1
                else:
                    unattributed[memory_type.value] = unattributed.get(memory_type.value, 0) + 1

        if unattributed:
            total = sum(unattributed.values())
            detail = (
                f"{total} blocks name no creator -- no registration or derivation record: {_per_module(unattributed)}"
            )
            return [row("blocks.unattributed", WARN, detail, data={"unattributed": unattributed})]
        return [
            row(
                "blocks.unattributed",
                OK,
                f"every block names who created it ({attributed})",
                data={"attributed": attributed},
            )
        ]

    def _indices(self) -> list[dict[str, Any]]:
        """
        Whether the declared indices exist on disk and describe the installed composition.

        Read from the sidecar headers, never through the index set: building the set would construct the configured
        embedder, and the header already says what composition an index was built against and, for a vector index,
        which embedder produced it.
        """
        from vitruvio.indices import format as envelope
        from vitruvio.runtime.indexset import sidecar_headers

        roots: dict[str, str] = InspectionOps(self.session).roots()["roots"]
        headers = sidecar_headers(self.config)
        by_name = {path.name: header for path, header in headers}

        unbuilt: list[str] = []
        stale: list[str] = []
        fresh: list[str] = []
        for spec in self.config.project.indices:
            module = spec.memory_type.value
            if module not in roots:
                continue
            name = f"{module}.{spec.kind.value}"
            header = by_name.get(f"{name}{envelope.SUFFIX}")
            if header is None:
                # A vector sidecar is absent whenever the module projects to nothing embeddable -- provenance, or
                # canonical blocks registered without a view -- and that is not a fault. A structural index always has
                # a body for a module with blocks, so its absence is.
                if spec.kind.value != "vector":
                    unbuilt.append(name)
            elif header.merkle_root != roots[module]:
                stale.append(name)
            else:
                fresh.append(name)

        rows: list[dict[str, Any]] = []
        if unbuilt:
            detail = f"{len(unbuilt)} declared indices have never been built: {', '.join(unbuilt)}"
            rows.append(row("indices.unbuilt", WARN, detail, data={"unbuilt": unbuilt}))
        if stale:
            detail = (
                f"{len(stale)} sidecars describe a different composition than the one installed: {', '.join(stale)}"
            )
            rows.append(row("indices.stale", WARN, detail, data={"stale": stale}))
        if not unbuilt and not stale:
            detail = (
                f"{len(fresh)} sidecars match the installed composition"
                if fresh
                else "no indices are declared for the installed modules"
            )
            rows.append(row("indices.stale", OK, detail, data={"fresh": fresh}))
        rows.extend(self._vector_tags(headers))
        return rows

    def _vector_tags(self, headers: list[tuple[Path, Header]]) -> list[dict[str, Any]]:
        """
        Whether each vector sidecar was built with the embedder that is configured now.

        Compared field by field from the tag in the header -- provider, model, and the revision and width when the
        configuration pins them -- rather than by constructing the embedder and rendering its tag, which would load
        a model to answer a question about a file. That is also what makes the check work after a pull onto a machine
        without the extra the index needs: the sidecar still says what it was built with.
        """
        from vitruvio.embeddings.tag import ModelTag

        mismatched: list[dict[str, Any]] = []
        unparseable: list[dict[str, Any]] = []
        aligned: list[str] = []
        for _, header in headers:
            if header.kind != "vector" or not header.model_tag:
                continue
            spec = self._embedder_for(header.memory_type)
            if spec is None:
                continue
            held = ModelTag.parse(header.model_tag)
            if held is None:
                unparseable.append({"module": header.memory_type, "tag": header.model_tag})
                continue
            differing = _tag_differences(held, spec)
            if differing:
                mismatched.append({"module": header.memory_type, "tag": header.model_tag, "differs": differing})
            else:
                aligned.append(header.memory_type)

        rows: list[dict[str, Any]] = []
        if mismatched:
            detail = "; ".join(f"{item['module']}: {', '.join(item['differs'])}" for item in mismatched)
            rows.append(row("indices.model_mismatch", FAIL, detail, data={"mismatched": mismatched}))
        if unparseable:
            detail = "tags this build cannot read: " + "; ".join(
                f"{item['module']}: {item['tag']}" for item in unparseable
            )
            rows.append(row("indices.model_mismatch", WARN, detail, data={"unparseable": unparseable}))
        if aligned and not mismatched and not unparseable:
            detail = f"{len(aligned)} vector sidecars were built with the configured embedder"
            rows.append(row("indices.model_mismatch", OK, detail, data={"aligned": aligned}))
        return rows

    def _embedder_for(self, memory_type: str) -> Any:
        """The embedder spec the declared vector index for ``memory_type`` uses, or ``None`` when none is declared."""
        for spec in self.config.project.indices:
            if spec.memory_type.value == memory_type and spec.kind.value == "vector":
                if spec.embedder == "vision":
                    return self.config.project.vision_embedder
                return self.config.project.text_embedder
        return None

    def _embedders(self) -> list[dict[str, Any]]:
        """Whether the configured text embedder can run here, and whether its vectors mean anything."""
        report = EmbedderOps(self.session).embedders()
        text = report["text"]
        provider = text["provider"]
        known = {item["provider"]: item for item in report["providers"]}
        entry = known.get(provider)
        if entry is None or not entry["installed"]:
            extra = (entry or {}).get("extra")
            detail = f"the configured text embedder {provider}/{text['model']} cannot be constructed here"
            if extra:
                detail += f" -- install {extra}"
            return [row("embedder.unavailable", FAIL, detail, data={"text": text, "extra": extra})]
        semantic = bool(report["semantic"])
        detail = f"{provider}/{text['model']}"
        if not semantic:
            detail += " ranks by hashed features, which carry no meaning: a synonym will never match"
        return [row("embedder.semantic", OK if semantic else WARN, detail, data={"text": text, "semantic": semantic})]

    def _registry(self, *, registry: bool, local: Path | None, anonymous: bool) -> list[dict[str, Any]]:
        """
        What is known about the registry without contacting it, and one read-only request when asked.

        The reference and the credentials are configuration and can always be reported. Reachability is a network
        request, so it is a ``skip`` row unless ``registry`` is set.
        """
        rows: list[dict[str, Any]] = []
        reference = self.config.repository()
        tag = self.config.project.registry.tag
        if reference is None:
            rows.append(row("registry.reference", WARN, "no registry is configured; publishing needs a reference"))
            rows.append(row("registry.reachable", SKIP, "not probed: there is no reference to probe"))
            return rows

        rows.append(row("registry.reference", OK, f"{reference}:{tag}", data={"reference": reference, "tag": tag}))
        if local is None:
            rows.append(self._credentials(reference))
        if not registry:
            rows.append(row("registry.reachable", SKIP, "not probed; pass --registry for one read-only request"))
            return rows
        rows.append(self._probe(reference, local=local, anonymous=anonymous))
        return rows

    def _credentials(self, reference: str) -> dict[str, Any]:
        """Which credentials a push would use, resolved the way `registry whoami` resolves them: no network."""
        from vitruvio.runtime.registry import credential_for, host_of

        host = host_of(reference)
        try:
            credential = credential_for(reference)
        except Exception as error:
            translated = translate(error)
            return row("registry.credentials", FAIL, f"{translated.code}: {translated.message}", data={"host": host})
        if credential.anonymous:
            detail = (
                f"no credentials for {host}; a push will be refused, an anonymous pull of a public repository works"
            )
            return row("registry.credentials", WARN, detail, data={"host": host, "source": credential.source})
        detail = f"{credential.username} at {host}, from {credential.source}"
        data = {"host": host, "username": credential.username, "source": credential.source}
        return row("registry.credentials", OK, detail, data=data)

    def _probe(self, reference: str, *, local: Path | None, anonymous: bool) -> dict[str, Any]:
        """One manifest resolution, through the same client every distribution operation uses."""
        from vitruvio.runtime.distribution import probe_registry

        remote = RemoteOps(self.session)
        try:
            prepared = remote._prepare(reference, anonymous=anonymous, local=local)
            outcome = remote._run(probe_registry(prepared.effective, prepared.client, prepared.tag))
        except Exception as error:
            translated = translate(error)
            data = {"reference": reference, "local": str(local) if local else None}
            return row("registry.reachable", FAIL, f"{translated.code}: {translated.message}", data=data)
        data = {**outcome, "reference": prepared.reference, "tag": prepared.tag, "local": str(local) if local else None}
        return row("registry.reachable", OK if outcome["reachable"] else FAIL, outcome["detail"], data=data)

    def _cache(self) -> list[dict[str, Any]]:
        cache = model_cache()
        size = sum(item.stat().st_size for item in cache.rglob("*") if item.is_file()) if cache.exists() else 0
        detail = f"{cache} ({size / 1_048_576:.1f} MiB)"
        return [row("cache.models", OK, detail, data={"path": str(cache), "bytes": size})]
