"""``vitruvio catalog`` -- declare and navigate canonical metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter

from vitruvio.cli import render
from vitruvio.cli.context import current
from vitruvio.cli.documents import load_document
from vitruvio.kernel import CandidatesRejectedError, ExitCode

app = App(
    name="catalog",
    help="Classify canonical evidence with portable schemes and classes.",
    result_action="return_value",
    exit_on_error=False,
)


def _load(path: Path) -> dict[str, Any]:
    return load_document(path, label="catalog manifest")


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


@app.default
def tree() -> ExitCode:
    """Show schemes as folders, classes as subfolders, and canonical sources as leaves."""
    console = current().console
    result = current().service().catalog_tree()
    return console.emit("catalog.tree", result, view=render.catalog_tree(result))


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
        view=render.stack(
            render.fields([("classes", ", ".join(classes)), ("sources", len(result["sources"]))]),
            "",
            render.source_rows(result.get("source_rows") or ()),
        ),
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
        view=render.stack(
            render.fields(
                [
                    ("path", result["path"] or "/"),
                    ("next scheme", result.get("scheme") or "(sources)"),
                    ("directories", ", ".join(result["directories"]) or "(none)"),
                    ("sources", len(result["sources"])),
                ]
            ),
            "" if result.get("source_rows") else None,
            render.source_rows(result.get("source_rows") or ()) if result.get("source_rows") else None,
        ),
    )
