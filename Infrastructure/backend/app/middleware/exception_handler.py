"""Global exception handler middleware for FastAPI.

Provides consistent error response format across all API endpoints.
Adapted from automation-service pattern.
"""

from __future__ import annotations

import traceback
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class APIError(Exception):
    """Base exception for API errors with structured error response."""

    def __init__(
        self,
        status_code: int,
        message: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.status_code = status_code
        self.message = message
        self.error_code = error_code
        self.details = details
        super().__init__(message)


class NotFoundError(APIError):
    """Resource not found error."""

    def __init__(self, message: str = "Resource not found", error_code: str = "NOT_FOUND"):
        super().__init__(status_code=404, message=message, error_code=error_code)


class ValidationAPIError(APIError):
    """Validation error."""

    def __init__(self, message: str = "Validation error", details: dict[str, Any] | None = None):
        super().__init__(
            status_code=422, message=message, error_code="VALIDATION_ERROR", details=details
        )


class ConflictError(APIError):
    """Conflict error (e.g., resource already exists)."""

    def __init__(self, message: str = "Resource conflict", error_code: str = "CONFLICT"):
        super().__init__(status_code=409, message=message, error_code=error_code)


def _build_error_response(
    status_code: int,
    message: str,
    error_code: str | None = None,
    details: dict[str, Any] | None = None,
    request_path: str | None = None,
) -> dict[str, Any]:
    """Build a structured error response dictionary."""
    response: dict[str, Any] = {
        "error": {
            "status_code": status_code,
            "message": message,
        }
    }
    if error_code:
        response["error"]["error_code"] = error_code  # type: ignore[index]
    if details:
        response["error"]["details"] = details  # type: ignore[index]
    if request_path:
        response["error"]["request_path"] = request_path  # type: ignore[index]
    return response


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """FastAPI exception handler for ``APIError`` subclasses.

    Registered via ``app.add_exception_handler(APIError, api_error_handler)``
    so that any route raising ``NotFoundError``, ``ValidationAPIError``,
    ``ConflictError``, or a bare ``APIError`` gets a consistent JSON error
    body instead of a generic 500.
    """
    logger.warning(
        f"API error on {request.method} {request.url.path}: {exc.message}",
        extra={"error_code": exc.error_code, "details": exc.details},
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=_build_error_response(
            status_code=exc.status_code,
            message=exc.message,
            error_code=exc.error_code,
            details=exc.details,
            request_path=request.url.path,
        ),
    )


async def global_api_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all exception handler for unhandled errors.

    Handles ``ValidationError`` (Pydantic), ``APIError``, ``HTTPException``,
    and unexpected ``Exception``, returning a consistent JSON response for
    each category.

    Intended to replace the ad-hoc ``@app.exception_handler(Exception)``
    in ``main.py`` with a structured format.  Currently registered as a
    drop-in for the existing handler so existing behaviour is preserved
    while adding the structured ``error`` envelope.
    """
    # --- Pydantic validation errors -------------------------------------------
    if isinstance(exc, ValidationError):
        details: dict[str, str] = {}
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"])
            details[loc] = error["msg"]

        logger.warning(
            f"Validation error on {request.method} {request.url.path}: {exc}",
            extra={"errors": exc.errors()},
        )

        return JSONResponse(
            status_code=422,
            content=_build_error_response(
                status_code=422,
                message="Request validation failed",
                error_code="VALIDATION_ERROR",
                details=details,
                request_path=request.url.path,
            ),
        )

    # --- Custom API errors ----------------------------------------------------
    if isinstance(exc, APIError):
        logger.warning(
            f"API error on {request.method} {request.url.path}: {exc.message}",
            extra={"error_code": exc.error_code, "details": exc.details},
        )

        return JSONResponse(
            status_code=exc.status_code,
            content=_build_error_response(
                status_code=exc.status_code,
                message=exc.message,
                error_code=exc.error_code,
                details=exc.details,
                request_path=request.url.path,
            ),
        )

    # --- FastAPI HTTPException (let FastAPI handle with its default format) ---
    if isinstance(exc, HTTPException):
        raise

    # --- Unexpected errors ----------------------------------------------------
    tb = traceback.format_exc()
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}: {exc}\n{tb}",
        exc_info=True,
    )

    return JSONResponse(
        status_code=500,
        content=_build_error_response(
            status_code=500,
            message="Internal server error",
            error_code="INTERNAL_ERROR",
            details={"type": type(exc).__name__},
            request_path=request.url.path,
        ),
    )
