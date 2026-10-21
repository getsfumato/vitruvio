"""The ``vitruvio-server`` command: one entry point that runs one of the three roles.

``query`` and ``ingest`` are ASGI apps served by uvicorn; ``worker`` is the Redis Streams consumer. One command rather
than three so the container image has one entrypoint and the role is its argument: ``docker run IMAGE`` is query,
``docker run IMAGE ingest``, ``docker run IMAGE worker``.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

DEFAULT_HOST = "127.0.0.1"
"""Loopback outside a container. The image sets ``VITRUVIO_SERVER_HOST=0.0.0.0`` so a published port reaches it."""

DEFAULT_PORT = 8080

ROLES = {
    "query": "the read-only query API: search, explain, state and resolve over a published brain",
    "ingest": "the ingest API: accepts jobs and reports on them; needs Redis",
    "worker": "the ingest worker: runs queued jobs and publishes the result tag; needs Redis",
}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="vitruvio-server", description="Run one role of Vitruvio Server.")
    roles = root.add_subparsers(dest="role", required=True, metavar="{query,ingest,worker}")
    for role in ("query", "ingest"):
        api = roles.add_parser(role, help=ROLES[role], description=ROLES[role])
        api.add_argument(
            "--host",
            default=os.environ.get("VITRUVIO_SERVER_HOST", DEFAULT_HOST),
            help="interface to bind (default: $VITRUVIO_SERVER_HOST, else %(default)s)",
        )
        api.add_argument(
            "--port",
            type=int,
            default=int(os.environ.get("VITRUVIO_SERVER_PORT", DEFAULT_PORT)),
            help="port to bind (default: $VITRUVIO_SERVER_PORT, else %(default)s)",
        )
    roles.add_parser("worker", help=ROLES["worker"], description=ROLES["worker"])
    return root


def main(argv: Sequence[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.role == "worker":
        from vitruvio.server.worker import main as run_worker

        run_worker()
        return

    import uvicorn

    # The import string rather than the app object: uvicorn imports it on start, so `--help` and an argument error
    # never construct an app or open a Redis connection.
    uvicorn.run(f"vitruvio.server.{args.role}:app", host=args.host, port=args.port)
