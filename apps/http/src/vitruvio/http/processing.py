"""One isolated ingest job from OCI input to a new OCI result tag."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from vitruvio.http.brain import install, local_registry, make_service, resolve_manifest
from vitruvio.http.documents import max_document_bytes, read_document
from vitruvio.http.models import CandidateItem, DocumentItem, IngestionRequest
from vitruvio.kernel import ConfigError, VitruvioError
from vitruvio.runtime import BrainService, evidence_from_bytes

PreparedCallback = Callable[[dict[str, Any], str], None]
logger = logging.getLogger(__name__)


def _error(error: Exception) -> dict[str, Any]:
    if not isinstance(error, VitruvioError):
        logger.exception("unexpected ingest item error", exc_info=error)
        return {"code": "INTERNAL", "message": "internal processing error", "hint": None, "retryable": False}
    return {
        "code": error.code,
        "message": error.message,
        "hint": error.hint,
        "retryable": error.retryable,
    }


async def _process_item(service: BrainService, item: DocumentItem | CandidateItem, limit: int) -> dict[str, Any]:
    if isinstance(item, CandidateItem):
        return service.commit_candidates(item.candidates, item.task)
    content = await read_document(item.uri, limit)
    evidence = evidence_from_bytes(
        content,
        origin=item.origin,
        media_type=item.media_type,
        license=item.license,
        retention_policy=item.retention_policy,
        normalize_with=item.normalize_with,
        max_bytes=limit,
    )
    return service.ingest_run(
        evidence,
        proposer=item.proposer,
        allowed=item.allowed,
        subject=item.subject,
    )


def _sign_governed(service: BrainService, fingerprints: list[str], result: dict[str, Any]) -> None:
    if not fingerprints:
        raise ConfigError("a governed brain requires sign_with fingerprints before publication")
    keys = {entry["fingerprint"] for entry in service.auth_keys()["keys"]}
    allowed = {entry["fingerprint"] for entry in service.auth_trust_root()["keys"] if entry["active"]}
    for fingerprint in fingerprints:
        if fingerprint not in keys or fingerprint not in allowed:
            raise ConfigError(f"SSH key {fingerprint!r} is not loaded and authorized")
        service.auth_sign(fingerprint)
    authenticity = service.auth_status()
    if authenticity.get("state") != "authorized":
        raise VitruvioError("the governed head does not satisfy the required signature policy")
    attribution = service.auth_attribution()
    result["attribution"] = attribution
    if not attribution["complete"] or not attribution["fully_vouched"]:
        result["warnings"].append("governed head attribution is not fully vouched")


async def process_job(
    request: IngestionRequest,
    job_id: str,
    prepared: PreparedCallback,
    progress: Callable[[int, dict[str, Any]], None],
) -> dict[str, Any]:
    if request.config.actor is None:
        raise ConfigError("ingestion requires a declared actor in config.actor")

    with tempfile.TemporaryDirectory(prefix="vitruvio-ingest-") as work:
        service = make_service(Path(work), request.brain, request.config, assisted_by=request.assisted_by)
        digest = await resolve_manifest(service, request.brain)
        installed = await install(service, request.brain, digest)
        governed = bool(installed.authenticity.get("trust_root"))
        if governed and not request.sign_with:
            raise ConfigError("a governed brain requires sign_with fingerprints before ingestion")
        if not governed and request.sign_with:
            raise ConfigError("sign_with was supplied for an ungoverned brain")
        before_job = service.state()["snapshot"]["digest"]
        reports: list[dict[str, Any]] = []
        limit = max_document_bytes(service.config.project.ingest.max_bytes)

        for index, item in enumerate(request.items):
            before_item = service.state()["snapshot"]["digest"]
            try:
                value = await _process_item(service, item, limit)
                report: dict[str, Any] = {"index": index, "kind": item.kind, "ok": True, "data": value}
            except Exception as error:
                report = {"index": index, "kind": item.kind, "ok": False, "error": _error(error)}
            after_item = service.state()["snapshot"]["digest"]
            report["changed"] = before_item != after_item
            reports.append(report)
            progress(index, report)

        changed = service.state()["snapshot"]["digest"] != before_job
        failed = sum(1 for item in reports if not item["ok"])
        result: dict[str, Any] = {
            "job_id": job_id,
            "input": {"repository": request.brain.repository, "tag": request.brain.tag, **installed.metadata()},
            "items": reports,
            "succeeded": len(reports) - failed,
            "failed": failed,
            "changed": changed,
            "warnings": installed.warnings,
            "output": None,
        }
        if not changed:
            result["status"] = "partial" if failed else "completed"
            return result

        authenticity = service.auth_status()
        if authenticity.get("trust_root"):
            _sign_governed(service, request.sign_with, result)

        output_tag = f"ingest-{job_id}"
        packed = service.pack(tag=output_tag)
        # Persist this exact artifact identity before pushing. A reclaimed job can recognize a push that
        # succeeded immediately before its worker died, without proposing a second time.
        result["output"] = {"repository": request.brain.repository, "tag": output_tag, "digest": packed["digest"]}
        prepared(result, packed["digest"])
        published = await service.push_async(
            request.brain.repository,
            tag=output_tag,
            local=local_registry(),
        )
        result["output"] = {
            "repository": request.brain.repository,
            "tag": output_tag,
            "digest": published["digest"],
            "publish": published,
        }
        result["status"] = "partial" if failed else "completed"
        return result
