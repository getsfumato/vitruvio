"""One module per protocol domain, each a class over a :class:`~vitruvio.runtime.session.BrainSession`.

``BrainService`` remains the surface every interface drives -- one method per protocol operation, as ADR-0003 has
it -- and delegates each to the domain that owns it. What lives here is the implementation, and the docstring that
explains it; the facade carries a one-line summary and the delegation.

**Two rules, both load-bearing.**

*Nothing is re-exported from this file.* Importing one domain must not import fourteen. The facade imports each
module by name, and a barrel here would make ``lifecycle`` pull in ``retrieval``, ``indices`` and the engines
behind them.

*Heavy imports stay inside the functions that need them.* ``boltzmann``, ``vitruvio.kernel`` and the stateless
runtime helpers (``wire``, ``mapping``, ``assembly``, ``browse``) may be imported at module scope. ``vitruvio``'s
``indices``, ``embeddings``, ``stats`` and ``bench``, the heavier runtime helpers, and ``asyncio`` may not: they
cost about 50ms between them, on every invocation including ``--help``, and
``packages/runtime/tests/test_import_cost.py`` fails if they leak.
"""
