# CEA Monitoring Service

Read-only Monitoring and Historical APIs. They own reads from TimescaleDB
and Redis; `automation-service` remains the sole control and hardware authority.

## Service contract

- Binds to port **8005** when started with Uvicorn.
- `GET /health` is a liveness response and does not contact Postgres or Redis.
- `GET /ready` returns `200` only when both read dependencies are available.
  It returns `503` otherwise, with distinct `database` and `redis` checks.
- The service performs no database or Redis writes. Readiness uses only
  Postgres `SELECT 1` and Redis `PING` through `shared.health`.
- The monitoring-service sensor/control read API validates ranges down to
  1 second (`sensor_models.MINIMUM_RANGE = timedelta(seconds=1)`). The
  automation-service projection snapshot construction independently requires
  ranges of at least 5 minutes (`app/schemas/monitoring_models.py`
  `MINIMUM_RANGE`). These are separate validators serving different purposes;
  neither is being changed by the plan.

### Historical photoperiod evidence

Room photoperiod reads use the canonical device cluster `main`. New
`photoperiod_transition` rows carry phase indefinitely only inside explicit
`monitoring_photoperiod_coverage` availability. Startup does not claim coverage
before its first observation; unclosed restarts conservatively invalidate the
interval from the last confirmed phase/availability boundary.

Legacy `source='photoperiod'` room observations and raw per-light SUN/MOON
setpoints support only their next 60 seconds, truncated by newer observations.
Agreeing latest historical devices can recover phase as `derived/estimated`;
conflicts, invalid light facts, expiry, and unsupported spans remain
`UNKNOWN/unavailable`. SUN at zero intensity remains SUN. Historical identity
comes from retained facts, not today's device registry or timing configuration.

History and tail share the same half-open read path, including predecessor
evidence and an explicit state anchor at the requested start. Point budgets
never remove phase, provenance, or UNKNOWN boundaries. The `climate` and
`photoperiod` scopes never read the light family or its derived phase
witnesses, so their `photoperiod` output is recorded phase/coverage authority
only. Monitoring performs no backfill or writes, and missing required coverage
schema is an unavailable read, not permission to extrapolate today's schedule.

The coverage schema is defined by `../database/monitoring_read_models.sql`.
Applying it to production requires separate owner authorization before rollout;
the implementation's schema and writer/HTTP smoke run only in the guarded
`monitoring_test_*` harness.


## Historical overview reads

- `GET /api/sensors/monitoring/overview/{location}` and
  `GET /api/monitoring/control/{location}/overview` merge recorded sufficient
  statistics with exact clipped edges. Graph buckets are the finest fitting
  whole-hour width from 1–12 hours; intersecting edge buckets count against
  `max_points` (10–4000, generic default 600). An unfit budget returns 400,
  never a wider bucket or whole-history raw fallback.
- Detailed control history/tail reads use `scope` `all | room | climate |
  photoperiod`. `room` (Room Monitoring/Historical ≤ 7 days) keeps climate
  setpoints, recorded lights and photoperiod while leaving device/PID groups
  empty. `climate` keeps climate setpoints and photoperiod, `photoperiod`
  keeps committed phase/coverage facts only, and `all` remains the complete
  automation-facing scope. The overview reader keeps its own `all | lights`
  scope; `lights` is no longer a history/tail value.
- History/tail ranges are capped at 7 days for `all`/`room` and 400 days for
  `climate`/`photoperiod`; longer ranges return 400 `range_too_long`.
- `GET /api/sensors/monitoring/range/{location}` accepts optional
  `interval_seconds` with `max_points`. It must be a nice ladder interval that
  fits the budget (`ceil(range / interval) <= max_points`), otherwise 400
  `invalid_interval_seconds`; a valid value selects the raw/1-minute/5-minute
  tier by interval and is echoed in response metadata.
- Coverage, native invalidations/watermarks and rebuild faults gate the actual
  sources used by the request. Missing, dirty or unverified source history
  returns 503; `/ready` alone is not Historical readiness proof.
- Historical native metadata comes from `monitoring_history_cagg_watermark`,
  restricted to the seven overview aggregates plus the measurement 1min
  ancestor. It does not scan the deployed legacy materialization-coverage
  fallback or change detailed read models.
  Native invalidation spans below PostgreSQL's minimum timestamp remain outside
  every readable range instead of causing timestamp-decoding failures.
- Aggregate-hour joins use the request's explicit epoch-aligned bucket width;
  raw edge/tail lookups stay parameterized by their bounded time spans. Empty
  coverage-probe intervals short-circuit without scanning retained raw history.
- Verified daily frontiers and relay UTC-day spans advance by exactly 24 hours,
  including Toronto DST transitions. UI calendar presets still use Toronto dates.
- The additive sources and stopped operator jobs are prepared by
  `../database/monitoring_history_overview.sql` and its relay include, separately
  from deployment. Production preparation/backfill/job activation requires
  owner authorization, a protected backup and targeted disposable native proof
  of idempotency, raw/index preservation, parity, writer compatibility and recovery.
- Raw history, existing indexes and pre-existing aggregates remain intact.
  Calendar closure/compaction, grow-aware retention, and ZIP/Nextcloud export
  are separate owner-authorized lifecycle work. Checked-in database defaults
  target `sj_cea`; no production database rename is performed by this service.

## Configuration

| Variable | Purpose |
|---|---|
| `MONITORING_POSTGRES_DSN` | Postgres/TimescaleDB read connection DSN |
| `MONITORING_REDIS_URL` | Redis read connection URL |
| `MONITORING_POSTGRES_POOL_SIZE` | Maximum read-pool size (default: `8`, range `1`–`8`) |
| `MONITORING_POSTGRES_ACQUIRE_TIMEOUT_SECONDS` | Read-pool acquire timeout in seconds (default: `10`, range greater than `0` through `60`) |
| `MONITORING_POSTGRES_STATEMENT_TIMEOUT_MS` | Postgres statement timeout in milliseconds (default: `8000`, range `1`–`60000`) |
| `MONITORING_REDIS_TIMEOUT_SECONDS` | Redis connect and read timeout in seconds (default: `2`, range greater than `0` through `60`) |

Run locally from this directory:

```bash
PYTHONPATH=.. uvicorn monitoring_service.main:app --host 0.0.0.0 --port 8005
```

## Verification

```bash
python3 -m pytest -q tests/test_smoke.py
ruff check monitoring_service tests
python3 -m compileall -q monitoring_service
```

The package uses direct `monitoring_service.*` imports. It must not import
automation control, hardware drivers, relay control, or write-capable adapters.
