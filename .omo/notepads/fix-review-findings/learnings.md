# Fix Review Findings - Learnings

## 2026-06-01: HTTPS Enforcement for CalDAV Connection Test

### Finding
`POST /api/calendar/sync/connections/test` accepted `http://` URLs for `caldav_base_url`, sending `app_password` in plaintext over unencrypted HTTP — a credential exposure vulnerability.

### Fix
- **Pydantic validator** on `SyncConnectionTest.caldav_base_url`: rejects `http://` URLs with a clear error message unless `CALDAV_ALLOW_HTTP_TEST=true` env var is set.
- **Route-level check**: logs a `WARNING` when the dev override allows HTTP, so it's visible in production logs.
- Used `@validator` decorator with `@classmethod` for proper Pydantic v1 validation pattern.
- `logger` was not defined in routes/calendar.py — added `from shared.infra_logging import get_logger` + `logger = get_logger(__name__)` following the convention used by all other route files.

### Key Insight: Dual-layer defense
The Pydantic validator handles rejection (fail-fast at schema level), while the route-level check handles the dev override warning. This separates concerns: schema validation vs. operational logging. The comment `# Pydantic validator already rejects http:// URLs unless override is set` is necessary to explain why the route-level `if` checks for `http://` AND the env var — without it, a reader might think the HTTP check at route level is redundant or dead code.
