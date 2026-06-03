# Task 6: Structured Error Handling Middleware for Backend

## Summary

Added structured error handling middleware to the backend service, copying the pattern from automation-service.

## Files Created

### `Infrastructure/backend/app/middleware/__init__.py`
- Re-exports `APIError`, `NotFoundError`, `ValidationAPIError`, `ConflictError`, `api_error_handler`

### `Infrastructure/backend/app/middleware/exception_handler.py`
- Adapted from `Infrastructure/automation-service/app/middleware/exception_handler.py`
- Classes: `APIError` (base), `NotFoundError` (404), `ValidationAPIError` (422), `ConflictError` (409)
- Helper: `_build_error_response()` — consistent JSON envelope `{"error": {"status_code", "message", ...}}`
- Handler: `api_error_handler(request, exc: APIError) → JSONResponse` — registered via `app.add_exception_handler()`
- Extra: `global_api_exception_handler(request, exc: Exception) → JSONResponse` — catch-all that handles Pydantic `ValidationError`, `APIError`, re-raises `HTTPException`, and formats unexpected errors with `INTERNAL_ERROR`

## Files Modified

### `Infrastructure/backend/app/main.py`
- Added import: `from app.middleware.exception_handler import APIError, api_error_handler`
- Added: `app.add_exception_handler(APIError, api_error_handler)` — registered before the existing catch-all `@app.exception_handler(Exception)` so FastAPI dispatches APIError subclasses correctly

### `Infrastructure/backend/app/routes/sensors.py`
- Added import: `from app.middleware.exception_handler import ValidationAPIError`
- Updated `_validate_sensor_cluster_or_400()`: `raise HTTPException(status_code=400, detail=...)` → `raise ValidationAPIError(message=...)` for `ClusterMismatchError`

## Verification

- LSP diagnostics: 0 new errors introduced across all 4 changed files
- Existing `HTTPException` calls in other routes remain untouched
- Error format matches automation-service exactly (same `_build_error_response` helper)
- Uses same `from shared.infra_logging import get_logger` as rest of backend
