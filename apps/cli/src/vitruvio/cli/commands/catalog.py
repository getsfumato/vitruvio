"""``vitruvio catalog`` -- declare and navigate canonical metadata."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.kernel import CandidatesRejectedError, ExitCode, UsageError

app = App(
    name="catalog",
    help="Classify canonical evidence with portable schemes and classes.",
    result_action="return_value",
    exit_on_error=False,
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise UsageError(f"catalog manifest {path} is not a file")
    try:
        if path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
        else:
            value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        raise UsageError(f"catalog manifest {path} could not be read: {error}") from error
    if not isinstance(value, dict):
        raise UsageError(f"catalog manifest {path} must contain an object/table")
    return value


def _emit_apply(document: dict[str, Any], *, dry_run: bool) -> ExitCode:
    console = current().console
    result = current().service().catalog_apply(document, dry_run=dry_run)
    rejected = [item for item in result["verdicts"] if item["status"] not in {"validated"}]
    view = render.fields(
        [
            ("clean", render.verdict(result["clean"], no="NO")),
            ("applied", render.verdict(result["applied"])),
            ("snapshot", render.digest(result["snapshot"])),
            ("declarations", len(result["verdicts"])),
            ("not applied", len(rejected)),
        ]
    )
    if not result["clean"]:
        return console.fail(
            "catalog.apply",
            CandidatesRejectedError("one or more catalog declarations were rejected"),
            data=result,
            view=view,
        )
    return console.emit("catalog.apply", result, view=view)


@app.command(name="show")
def show() -> ExitCode:
    """List declared schemes, classes, hierarchy and effective sources."""
    console = current().console
    result = current().service().catalog_show()
    table = render.table("scheme", "class", "direct", "effective")
    for scheme in result["schemes"]:
        for item in scheme["classes"]:
            table.add_row(
                scheme["name"],
                item["label"],
                str(len(item["direct_sources"])),
                str(len(item["sources"])),
            )
    return console.emit("catalog.show", result, view=table)


@app.command(name="apply")
def apply(path: Path, *, dry_run: bool = False) -> ExitCode:
    """Validate and atomically apply a TOML or JSON ``vitruvio.catalog/v1`` manifest."""
    return _emit_apply(_load(path), dry_run=dry_run)


@app.command(name="scheme")
def scheme(name: str, *, exclusive: bool = False, dry_run: bool = False) -> ExitCode:
    """Declare a classification scheme."""
    return _emit_apply(
        {
            "schema": "vitruvio.catalog/v1",
            "schemes": [{"name": name, "exclusive": exclusive}],
        },
        dry_run=dry_run,
    )


@app.command(name="class")
def class_(
    scheme: str,
    label: str,
    *,
    broader: Annotated[list[str] | None, Parameter(name=["--broader"], negative=())] = None,
    dry_run: bool = False,
) -> ExitCode:
    """Declare a class, optionally below existing ``scheme/label`` classes."""
    return _emit_apply(
        {
            "schema": "vitruvio.catalog/v1",
            "classes": [{"scheme": scheme, "label": label, "broader": broader or []}],
        },
        dry_run=dry_run,
    )


@app.command(name="place")
def place(
    source: str,
    classes: Annotated[list[str], Parameter(name=["--class"], negative=())],
    *,
    dry_run: bool = False,
) -> ExitCode:
    """Place one canonical block in one or more ``scheme/label`` classes."""
    return _emit_apply(
        {
            "schema": "vitruvio.catalog/v1",
            "placements": [{"source": source, "classes": classes}],
        },
        dry_run=dry_run,
    )


@app.command(name="browse")
def browse(classes: Annotated[list[str], Parameter(name=["--class"], negative=())]) -> ExitCode:
    """Browse the intersection of one or more classes, descendants included."""
    console = current().console
    result = current().service().catalog_browse(classes)
    return console.emit(
        "catalog.browse",
        result,
        view=render.fields([("classes", ", ".join(classes)), ("sources", len(result["sources"]))]),
    )


@app.command(name="path")
def path(
    schemes: Annotated[list[str], Parameter(name=["--scheme"], negative=())],
    path: str = "",
) -> ExitCode:
    """List a virtual path using the requested scheme order."""
    console = current().console
    result = current().service().catalog_path(schemes, path)
    return console.emit(
        "catalog.path",
        result,
        view=render.fields(
            [
                ("path", result["path"] or "/"),
                ("next scheme", result.get("scheme") or "(sources)"),
                ("directories", ", ".join(result["directories"]) or "(none)"),
                ("sources", len(result["sources"])),
            ]
        ),
    )
