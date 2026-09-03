"""Importing the service layer must not import an index engine, an embedder, or a benchmark harness.

This is the test the capability gate was assumed to be. It is not: `test_an_inspect_operation_does_not_import_an_embedder`
asserts that torch and sentence_transformers stay out of `sys.modules`, and they do -- but they would stay out even if
every one of `service.py`'s function-local imports were hoisted to module scope, because the engines themselves defer
their heavy dependencies (`usearch` is imported inside a function in `indices/vector.py`, and `sentence_transformers` is
resolved by name through the embeddings registry). So that test cannot see the regression this one is about.

What the function-local imports actually buy is startup latency, which `ARCHITECTURE.md` makes load-bearing: on
this machine `import vitruvio.runtime` costs ~124ms, and adding `vitruvio.indices` (+24ms), `asyncio` (+17ms),
`vitruvio.stats` (+4ms), `vitruvio.embeddings` (+3ms) and `vitruvio.bench.harness` (+2ms) is a ~40% regression on every
invocation -- including `vitruvio --help` and `vitruvio config show`.

In a subprocess because it has to be: by the time pytest runs, the test session has already imported most of these.
"""

from __future__ import annotations

import subprocess
import sys

DEFERRED = (
    "vitruvio.indices",
    "vitruvio.embeddings",
    "vitruvio.stats",
    "vitruvio.bench",
    "asyncio",
)
"""Packages the runtime may reach only from inside a function.

Named here rather than inline so that a module which legitimately becomes eager is one edit, made deliberately, with
this docstring in front of it -- and not a quietly deleted assertion.
"""


def test_importing_the_runtime_defers_every_heavy_package() -> None:
    """The whole service layer, imported, and nothing an INSPECT command would not need."""
    program = f"import sys, vitruvio.runtime\nprint(':'.join(name for name in {DEFERRED!r} if name in sys.modules))\n"
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True)
    eager = [name for name in result.stdout.strip().split(":") if name]
    assert not eager, (
        f"importing vitruvio.runtime pulled in {', '.join(eager)}. These cost ~50ms between them, on every CLI "
        "invocation. Move the import inside the function that needs it -- see the module docstring in "
        "vitruvio.runtime.indexset for the pattern."
    )


def test_the_service_module_itself_defers_them_too() -> None:
    """Imported directly rather than through the package, so a re-export cannot hide an eager import."""
    program = (
        f"import sys, vitruvio.runtime.service\nprint(':'.join(name for name in {DEFERRED!r} if name in sys.modules))\n"
    )
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True)
    assert not [name for name in result.stdout.strip().split(":") if name]
