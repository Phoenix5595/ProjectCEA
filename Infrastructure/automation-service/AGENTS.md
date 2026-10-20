# Automation Service

Core control service on port 8001: device registry, live-configured 1–5-second control loop, PID/VPD climate logic, schedules, and light control. Also serves the React SPA via Caddy at `:8080`.

## Startup and container boundaries

`app/main.py` builds the FastAPI app; `app/container.py` wires dependencies. `app/models/config_schema.py` validates `automation_config.yaml` at startup, and invalid config fails launch. Key config facts:

- `automation` service settings are resolved DB > `/var/lib/projectcea/settings-cache/automation.json` > YAML fallback; updates apply live without a restart.
- MCP23017 relays: I2C bus 0, address 39 decimal (`0x27`).
- DFR0971 dimmers: I2C bus 1, decimal 88/89/90 (`0x58/0x59/0x5A`).

Alembic schema migrations run at deploy time through
`Infrastructure/scripts/db_migrate.py apply`, not during automation startup.
Startup verifies the deployed database heads and logs a mismatch without
blocking service operation.

## Registry and hardware ownership

`device_registry` is the sole source of truth for device identity, relay channels, and DFR board/channel bindings. No YAML device definitions or commissioning path participates in runtime control.

| Component | Responsibility |
|---|---|
| `RuntimeDeviceRegistry` | Builds and atomically installs one immutable `RuntimeDeviceSnapshot` per tick |
| `DeviceRegistryService` | Sole supported mutation path; performs safe output sequencing and confirmed relay steals |
| `RelayBoardStateManager` | Sole MCP23017 board sampler and owner of `cea:relay:board_snapshot` |
| `DeviceCommandService` | Assigned-device command state (`AUTO`, `MANUAL_OFF`, `TIMED_ON`) |

An empty registry is valid: it installs a ready empty snapshot and emits no relay-ON or nonzero DFR commands.

## Control cadence and safety

The control cadence is the live `automation.update_interval` service setting (1–5 s), with YAML fallback. VPD is the master climate controller; PID is used for heating, cooling, and CO2 only. The global heating-failure↔exhaust interlock is **not currently configured**; the removal is recorded in `automation_config.yaml`.

`GET /api/devices/control-snapshot` joins the registry snapshot, relay observation, assigned-device command state, and DFR commanded/acknowledged intensity into one read model.

## Automation-state history

The history writer records PID gains only for the standard PID controller used by a device. There is currently no producer for `active_rule_ids`, `active_schedule_ids`, or `schedule_*`; those fields remain empty or `NULL` and do not indicate active rules or schedules.

`GET /api/settings` reports shared service settings and schema; `PUT /api/settings/{service}` validates and persists changes live. `/api/config` remains hardware-only and its restart metadata tracks hardware changes.

## Operational event log

The service owns the typed operational-event contract, all semantic producers, and the authenticated replay API.

- Envelope and payload families: [`app/events/operational_models.py`](app/events/operational_models.py)
- Producer ports and dispatcher: [`app/events/operational_ports.py`](app/events/operational_ports.py), [`app/events/operational_stream.py`](app/events/operational_stream.py)
- Alarm durability side-effect: [`app/events/alarm_journal.py`](app/events/alarm_journal.py)
- History and SSE routes: [`app/routes/operational_events.py`](app/routes/operational_events.py)
- Mutation coverage and safe diffs: [`app/events/mutation_context.py`](app/events/mutation_context.py), [`app/events/mutation_diff.py`](app/events/mutation_diff.py), [`app/events/mutation_coverage.py`](app/events/mutation_coverage.py)
- Runtime wiring: [`app/container.py`](app/container.py), [`app/routes/routes.py`](app/routes/routes.py)
- Runbook: [`app/events/AGENTS.md`](app/events/AGENTS.md)

Producers call `emit_nowait()` at authoritative decision or commit edges. The dispatcher uses bounded queues so the 1-second control loop never awaits Redis. Alarm/error lifecycle rows are durably persisted after stream publication.

## Repositories and routes

Repositories live in `app/repositories/` and own all DB access. Routes live in `app/routes/` and cover schedules, lights, climate periods, devices/registry, hardware, PID, room modes, alarms, system config, and the operational event endpoints. See `Infrastructure/automation-service/REQUIREMENTS.md` for normative behavior and `ARCHITECTURE.md` for service boundaries.

## Explicit grow boundaries

Calendar grow mutations use sorted room advisory locks, then grow/catalog row
locks, with overlap checks in the same transaction. Phase dates and rooms,
not creation time or the active lighting schedule, determine membership.
Generated bed vegetation is two or three weeks in Flower Room; clone/pot
vegetation stays in Veg Room. Existing/manual dates are not clamped.

`/api/calendar/grow-plans/{uuid}/close-preview`, `/close` and `/reopen` expose
explicit owner closure with Calendar revision checks. Closed phase edits,
deletes and grow deletion return `grow_closed`. First closure fixes the
original drying end and twelve-calendar-month anniversary; reopening does not
reset either or silently restore ZIP history. Closing queues an `export`; the
service publishes a verified ZIP before anniversary expiry can delete source
rows. `ARCHIVE_LIFECYCLE_ENABLED` defaults to `false`, so destructive lifecycle
execution does not run by default.

After verified source deletion, a scope correction overlapping removed time is
rejected with `archive_correction_requires_source`. Rebuilds use the prior
verified ZIP plus current source rows for the affected time window; deleted
rows cannot be reassigned to another room or identity.

## Confirmed outbound Calendar mirroring

Event writes commit locally before syncing their exact changed IDs within one
five-second wall-clock deadline. Four remote operations maximum; a one-second
due poll drains the same leased queue. UID reconciliation and conditional
ETags prevent duplicate uncertain creates and blind remote overwrites.
Generation/revision/lease compare-and-set fences reject older responses.

Fresh Nextcloud credentials are entered in Calendar settings. Saving verified
settings leaves the connection disabled until the explicit backup-confirmed
“Reconnect and mirror all local events” action. The existing calendar named
`Culture` (`TARGET_CALENDAR_NAME` in `app/routes/calendar.py`, case-insensitive)
and a writable Files root are discovered separately; no calendar is created or
cleared, and only events carrying the Pi's own UID/`X-CEA-EVENT-ID` are deleted.
Secrets stay Fernet-encrypted with `CALENDAR_SYNC_ENCRYPTION_KEY`, loaded by both
automation-service and grow-archive-service from
`/opt/projectcea/shared/env/calendar_sync.env` (mode 0600; losing or rotating the key
makes saved credentials undecryptable, so re-save the connection afterwards). Missing
or wrong keys expose unavailable status without discarding local data.

Proof: `bash Infrastructure/database/tests/test-grow-data-lifecycle.sh --case calendar-sync`.

## Local verification

```bash
cd Infrastructure/automation-service && ruff check . && ruff format --check . && python3 -m compileall -q app && pytest -q app/tests/pure
```

Production endpoints, I2C, and the production database must never be contacted by automated tests.

## Where to Look

| Topic | Document |
|---|---|
| Control layer details | [`app/control/AGENTS.md`](app/control/AGENTS.md) |
| Operational event runbook | [`app/events/AGENTS.md`](app/events/AGENTS.md) |
