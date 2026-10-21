"""HTTP envelopes using the runtime's existing error classification."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from vitruvio.kernel import VitruvioError, __version__
from vitruvio.runtime import report_for

logger = logging.getLogger(__name__)


def envelope(command: str, data: Any, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "vitruvio": __version__,
        "command": command,
        "ok": True,
        "data": data,
        "warnings": warnings or [],
        "error": None,
    }


def install_errors(app: FastAPI) -> None:
    def failure(
        path: str, status: int, code: str, kind: str, message: str, *, hint: str | None, retryable: bool
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={
                "vitruvio": __version__,
                "command": path,
                "ok": False,
                "data": None,
                "warnings": [],
                "error": {"code": code, "kind": kind, "message": message, "hint": hint, "retryable": retryable},
            },
        )

    @app.exception_handler(VitruvioError)
    async def vitruvio_error(_request: Request, error: VitruvioError) -> JSONResponse:
        report = report_for(error)
        return failure(
            _request.url.path,
            report.http_status,
            error.code,
            type(error).__name__,
            error.message,
            hint=error.hint,
            retryable=error.retryable,
        )

    @app.exception_handler(RequestValidationError)
    async def request_error(request: Request, error: RequestValidationError) -> JSONResponse:
        details = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}" for issue in error.errors()
        )
        return failure(request.url.path, 422, "USAGE", "RequestValidationError", details, hint=None, retryable=False)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = "NOT_FOUND" if error.status_code == 404 else "HTTP_ERROR"
        return failure(
            request.url.path, error.status_code, code, "HTTPException", str(error.detail), hint=None, retryable=False
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.exception("unhandled HTTP error", exc_info=error)
        return failure(
            request.url.path, 500, "INTERNAL", type(error).__name__, "internal server error", hint=None, retryable=False
        )
