"""``vitruvio auth`` -- SSH signatures, pins and trust-root governance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.cli.documents import load_document
from vitruvio.kernel import ExitCode, UsageError

app = App(
    name="auth",
    help="Authenticate and govern a brain with detached SSH signatures.",
    result_action="return_value",
    exit_on_error=False,
)


def _load(path: Path) -> dict[str, Any]:
    return load_document(path, label="auth document")


def _write(path: Path, value: dict[str, Any], *, force: bool) -> None:
    if path.exists() and not force:
        raise UsageError(f"{path} already exists", hint="choose another --output or pass --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _records(paths: list[Path] | None) -> list[dict[str, Any]]:
    return [_load(path) for path in paths or []]


@app.command(name="keys")
def keys() -> ExitCode:
    """List public Ed25519 keys available through the current SSH agent."""
    console = current().console
    result = current().service().auth_keys()
    table = render.table("fingerprint", "type")
    for key in result["keys"]:
        table.add_row(key["fingerprint"], key["key_type"])
    return console.emit("auth.keys", result, view=table)


@app.command(name="status")
def status(*, snapshot: str | None = None, offered: bool = False) -> ExitCode:
    """Verify integrity and report the independent authenticity state."""
    console = current().console
    result = current().service().auth_status(snapshot=snapshot, offered=offered)
    return console.emit(
        "auth.status",
        result,
        view=render.fields(
            [
                ("integrity", render.verdict(result["integrity"], no="FAILED")),
                ("authenticity", result["state"]),
                ("snapshot", render.digest(result["snapshot"])),
                ("trust root", render.digest(result.get("trust_root"))),
                ("pinned", render.verdict(result["pinned"])),
                ("signatures", len(result["signatures"])),
            ]
        ),
    )


@app.command(name="trust-root")
def trust_root(*, snapshot: str | None = None) -> ExitCode:
    """Show the trust root, authorized keys, permissions, validity, and consumer pin."""
    console = current().console
    result = current().service().auth_trust_root(snapshot=snapshot)
    return console.emit("auth.trust_root", result, view=render.trust_root(result))


@app.command(name="sign")
def sign(
    key: str,
    *,
    snapshot: str | None = None,
    scope: Annotated[list[str] | None, Parameter(name=["--scope"], negative=())] = None,
) -> ExitCode:
    """Explicitly sign a snapshot through ssh-agent; no private key enters Vitruvio."""
    console = current().console
    result = current().service().auth_sign(key, snapshot=snapshot, scopes=scope)
    return console.emit(
        "auth.sign", result, view=render.fields([("snapshot", result["snapshot"]), ("key", result["key"])])
    )


@app.command(name="pin")
def pin(*, trust_root: str | None = None, source: str | None = None) -> ExitCode:
    """Pin the current trust root (TOFU) or an out-of-band digest."""
    console = current().console
    result = current().service().auth_pin(trust_root=trust_root, source=source)
    return console.emit(
        "auth.pin",
        result,
        view=render.fields([("trust root", result["trust_root"]), ("source", result["source"])]),
    )


@app.command(name="attribution")
def attribution() -> ExitCode:
    """Show which declared actors are vouched by valid signature subjects."""
    console = current().console
    result = current().service().auth_attribution()
    return console.emit("auth.attribution", result)


@app.command(name="plan-rotate")
def plan_rotate(trust_root: Path, *, output: Path, force: bool = False) -> ExitCode:
    """Build once the exact revision document a distributed quorum will countersign."""
    console = current().console
    result = current().service().auth_plan_rotation(_load(trust_root))
    _write(output, result, force=force)
    return console.emit(
        "auth.plan_rotate",
        {**result, "output": str(output)},
        view=render.fields(
            [("wrote", str(output)), ("digest", result["digest"]), ("quorum", result["quorum_required"])]
        ),
    )


@app.command(name="countersign")
def countersign(plan: Path, key: str, *, output: Path, force: bool = False) -> ExitCode:
    """Countersign the exact revision document in a rotation plan."""
    console = current().console
    result = current().service().auth_countersign(_load(plan), key)
    _write(output, result, force=force)
    return console.emit(
        "auth.countersign",
        {**result, "output": str(output)},
        view=render.fields([("wrote", str(output)), ("snapshot", result["snapshot"]), ("key", result["key"])]),
    )


@app.command(name="rotate")
def rotate(
    *,
    trust_root: Path | None = None,
    plan: Path | None = None,
    sign_with: Annotated[list[str] | None, Parameter(name=["--sign-with"], negative=())] = None,
    record: Annotated[list[Path] | None, Parameter(name=["--record"], negative=())] = None,
) -> ExitCode:
    """Commit a local trust-root change or a planned distributed rotation."""
    console = current().console
    result = (
        current()
        .service()
        .auth_rotate(
            trust_root=_load(trust_root) if trust_root else None,
            plan=_load(plan) if plan else None,
            sign_with=sign_with or (),
            records=_records(record),
        )
    )
    return console.emit(
        "auth.rotate", result, view=render.fields([("snapshot", result["snapshot"]), ("revision", result["revision"])])
    )


@app.command(name="revoke")
def revoke(
    key: str,
    *,
    sign_with: Annotated[list[str] | None, Parameter(name=["--sign-with"], negative=())] = None,
    record: Annotated[list[Path] | None, Parameter(name=["--record"], negative=())] = None,
    retired_from: int | None = None,
    compromised_from: str | None = None,
) -> ExitCode:
    """Retire a key or withdraw its signatures from a compromised snapshot onward."""
    console = current().console
    result = (
        current()
        .service()
        .auth_revoke(
            key,
            sign_with=sign_with or (),
            records=_records(record),
            retired_from=retired_from,
            compromised_from=compromised_from,
        )
    )
    return console.emit(
        "auth.revoke", result, view=render.fields([("snapshot", result["snapshot"]), ("revision", result["revision"])])
    )
