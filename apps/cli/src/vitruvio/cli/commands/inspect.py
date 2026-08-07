"""``vitruvio inspect`` -- read the brain's structure without changing it.

The command that earns its place here is ``resolvability``. A block can be a **verifiable member** of a version
and still not be readable: after a selective install, or after a redaction destroyed its bytes under an erasure
policy. Those two are different from each other and both are different from corruption, and a tool that
reported them identically would make a removed block indistinguishable from a broken one -- which the protocol
explicitly forbids.
"""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from vitruvio.cli.context import current
from vitruvio.cli.render import short
from vitruvio.kernel import ExitCode

app = App(
    name="inspect",
    help="Read the brain's structure: roots, modules, blocks, resolvability.",
    result_action="return_value",
    exit_on_error=False,
)


@app.command(name="resolvability")
def resolvability() -> ExitCode:
    """Report which blocks are readable, which are tombstoned, and which are simply absent.

    `intact` being false is not automatically a problem: a brain installed selectively is missing layers on
    purpose. What it does mean is that some block a root still names cannot be read, and the report says which.
    """
    console = current().console
    result = current().service().resolvability()

    counts = result["counts"]
    lines = [
        f"{'module':<12} {'resolvable':>10} {'tombstoned':>11} {'missing':>8}",
        f"{'-' * 12} {'-' * 10} {'-' * 11} {'-' * 8}",
    ]
    modules = sorted(set(counts["resolvable"]) | set(counts["tombstoned"]) | set(counts["missing"]))
    for kind in modules:
        lines.append(
            f"{kind:<12} {counts['resolvable'].get(kind, 0):>10} "
            f"{counts['tombstoned'].get(kind, 0):>11} {counts['missing'].get(kind, 0):>8}"
        )
    if not modules:
        lines = ["No modules installed."]
    lines += ["", f"intact: {'yes' if result['intact'] else 'no'}"]
    if not result["intact"]:
        lines.append("  a tombstoned block is a verifiable member whose bytes were destroyed under policy")
        lines.append("  a missing block was never installed -- a selective pull is the usual reason")
    return console.emit("inspect.resolvability", result, lines=lines)


@app.command(name="roots")
def roots() -> ExitCode:
    """Print every installed module's Merkle root, and the snapshot digest that pins the set.

    Two brains holding the same knowledge have the same roots, whatever they were stored or transported by.
    That is what makes a root the identity of a knowledge state rather than of a file.
    """
    console = current().console
    result = current().service().roots()
    lines = [f"snapshot  {result['snapshot']}", ""]
    lines += [f"{kind:<12} {root}" for kind, root in sorted(result["roots"].items())]
    if not result["roots"]:
        lines = ["No modules installed."]
    return console.emit("inspect.roots", result, lines=lines)


@app.command(name="module")
def module(
    memory_type: str,
    *,
    limit: Annotated[int, Parameter(name=["--limit"])] = 20,
) -> ExitCode:
    """Print one module's shape and a sample of its block identities.

    Parameters
    ----------
    memory_type
        canonical, episodic, semantic, procedural or provenance.
    limit
        How many block identities to list.
    """
    console = current().console
    result = current().service().module(memory_type, limit=limit)
    lines = [
        f"module     {result['memory_type']}",
        f"root       {result['root']}",
        f"blocks     {result['block_count']}",
        f"indices    {', '.join(result['indices']) or '(none)'}",
        "",
        *[f"  {identity}" for identity in result["block_ids"]],
    ]
    if result["truncated"]:
        lines.append(f"  ... {result['block_count'] - len(result['block_ids'])} more")
    return console.emit("inspect.module", result, lines=lines)


@app.command(name="block")
def block(block_id: str) -> ExitCode:
    """Read one block by identity.

    The bytes are verified against the identity on the way out of the store, so a block that resolves is a block
    that hashes to the name it was filed under.

    Parameters
    ----------
    block_id
        A `sha256:...` block identity.
    """
    import json

    console = current().console
    result = current().service().resolve(block_id)
    lines = [
        f"block        {result['block_id']}",
        f"memory type  {result['memory_type']}",
        "",
        *json.dumps(result["payload"], indent=2, ensure_ascii=False).splitlines(),
    ]
    return console.emit("inspect.block", result, lines=lines)


@app.command(name="prove")
def prove(
    block_id: str,
    *,
    memory_type: Annotated[str, Parameter(name=["--memory-type"])],
) -> ExitCode:
    """Produce a Merkle inclusion proof for one block, already checked against the module's root.

    The proof is `O(log n)` sibling hashes, so membership in a version can be demonstrated without holding the
    rest of the module. It is returned already verified: leaving that to the caller would hand over the one
    thing the protocol does not leave to a caller.

    Parameters
    ----------
    block_id
        The block.
    memory_type
        Which module should contain it.
    """
    console = current().console
    result = current().service().prove(block_id, memory_type)
    lines = [
        f"block       {result['block_id']}",
        f"root        {result['root']}",
        f"leaf index  {result['leaf_index']} of {result['tree_size']}",
        f"audit path  {len(result['audit_path'])} hashes",
        "",
        f"verified: {'yes' if result['verified'] else 'NO'}",
    ]
    return console.emit("inspect.prove", result, lines=lines)


@app.command(name="doctor")
def doctor() -> ExitCode:
    """Check the environment: what is installed, what is configured, and what would fail.

    Reports rather than fixes. The most useful line is usually about the embedder: a vector index whose model
    tag does not match the configured embedder is not degraded, it is *wrong* -- the two spaces are unrelated,
    so the cosines between them are noise -- and the planner will refuse it rather than rank on it.
    """
    console = current().console
    context = current()

    checks: list[dict[str, object]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    from importlib.util import find_spec

    from vitruvio.kernel import model_cache

    for label, module_name, extra in (
        ("oras (registry transport)", "oras", "pyboltzmann[oci]"),
        ("usearch (vector index)", "usearch", "part of vitruvio-indices"),
        ("pyroaring (bitmap index)", "pyroaring", "part of vitruvio-indices"),
        ("sentence-transformers (local text)", "sentence_transformers", "vitruvio[local]"),
        ("pillow + pypdfium2 (vision)", "pypdfium2", "vitruvio[vision]"),
        ("keyring (credential store)", "keyring", "vitruvio[keyring]"),
    ):
        present = find_spec(module_name) is not None
        check(label, present, "installed" if present else f"absent -- install {extra}")

    try:
        config = context.resolve()
        check("brain", True, f"{config.brain} (selected by {config.brain_origin.value})")
        check(
            "actor",
            bool(config.project.actor.id),
            config.project.actor.id or "not set -- writes will be refused, because every write is attributed",
        )
        service = context.service()
        state = service.verify()
        check(
            "integrity",
            state["verified"],
            f"{state['block_count']} blocks verify" if state["verified"] else "roots do not match",
        )
    except Exception as error:  # the point of doctor is to report a broken setup, not to fail on one
        from vitruvio.runtime import translate

        translated = translate(error)
        check("brain", False, f"{translated.code}: {translated.message}")

    cache = model_cache()
    size = sum(item.stat().st_size for item in cache.rglob("*") if item.is_file()) if cache.exists() else 0
    check("model cache", True, f"{cache} ({size / 1_048_576:.1f} MiB)")

    failures = [item for item in checks if not item["ok"]]
    lines = [f"{'ok ' if item['ok'] else 'MISS'}  {item['check']:<36} {item['detail']}" for item in checks]
    result = {"checks": checks, "failures": len(failures)}
    return console.emit("inspect.doctor", result, lines=lines)


__all__ = ["app", "short"]
