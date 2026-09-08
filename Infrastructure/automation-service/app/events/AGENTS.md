# Operational Event Log

This document is the runbook for the versioned, observability-only operational event pipeline. It covers the contract, producer rules, API consumers, failure modes, retention, and how to add a new event type safely.

## What it is

The operational event log gives operators a single, scrollable, room-aware timeline of meaningful control and configuration transitions. Every event records the decision or commit edge where the cause is actually known, not a diff of later state.

It is **not** a control bus, a replacement for existing snapshots or monitoring history, or a permanent audit archive for routine activity.

## Envelope and schema version

Every event uses `schema_version=1` and the immutable envelope in [`operational_models.py`](operational_models.py):

| Field | Type | Notes |
|---|---|---|
| `event_id` | UUID | Stable identity for deduplication. |
| `occurred_at` | UTC datetime | Normalized to UTC; naive datetimes are rejected. |
| `source` | enum | `automation`, `api`, `operator`, `system`, `transport`. |
| `category` | enum | Must match payload family. |
| `severity` | enum | `info`, `warning`, `error`, `critical`. |
| `event_type` | dotted string | Regex `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`. |
| `correlation_id` | UUID or null | Ties matching commands to later observations. |
| `causation_id` | UUID or null | Ties cause to effect. |
| `entity` | entity context | `entity_type`, `entity_id`, optional `location`/`cluster`. |
| `actor` | actor context | `actor_type` (`operator/service/system`) and optional `actor_id`. |
| `reason_code` | string or null | Stable machine label. |
| `reason_text` | string or null | Human-readable cause. |
| `payload` | discriminated union | Family field selects one of seven payload shapes. |

Reason fields are not prose logs. Use `reason_code` for routers and `reason_text` for display.

## Payload families and event catalog

| Family | Typical event_type | Severity | Payload highlights |
|---|---|---|---|
| relay | `relay.state_changed`, `relay.command_issued`, `relay.mismatch_detected`, `relay.recovered` | info/warning | `state`, `observed_state`, `command_mode`, details. |
| manual_override | `manual_override.started`, `manual_override.extended`, `manual_override.expired` | info | `mode`, `expires_at`, `duration_seconds`. |
| ramp | `ramp.started`, `ramp.midpoint_crossed`, `ramp.completed`, `ramp.interrupted` | info/warning | `ramp_type`, `start_value`, `target_value`, `phase`. |
| control | `control.adjusted`, `control.rule_matched`, `control.saturation_reached`, `control.failsafe_entered` | info/warning | `controller`, `sensor_value`, `effective_setpoint`, `output_percent`. |
| mutation | `mutation.created`, `mutation.updated`, `mutation.deleted`, `mutation.action_completed` | info | `operation`, allowlisted `changes`. |
| alarm | `alarm.opened`, `alarm.acknowledged`, `alarm.recovered`, `alarm.cleared` | error/critical | `alarm_code`, `state`, `detail`. |
| system | `system.failsafe_raised`, `system.failsafe_cleared`, `system.transport_degraded` | warning/error | `component`, `state`, `detail`. |

Producers must not emit more than one visible event for a single physical transition. For example, a relay command and the next matching observation share one `correlation_id`; the observation does not create a second `relay.state_changed` row.

## Stream bounds and key

Canonical source: [`Infrastructure/shared/redis_keys.py`](../../shared/redis_keys.py).

| Limit | Value |
|---|---|
| Stream key | `cea:events:operational` |
| Age bound | 24 hours (`OPERATIONAL_EVENTS_RETENTION_MS`) |
| Entry cap | exactly 50,000 (`OPERATIONAL_EVENTS_MAXLEN`) |
| Per-event size | 4,096 UTF-8 bytes (`OPERATIONAL_EVENT_MAX_SERIALIZED_BYTES`) |

The publisher uses one atomic Lua script that calls `TIME`, `XADD`, approximate `XTRIM MINID`, and exact `XTRIM MAXLEN`. This keeps age and count trimming coupled to the append.

## Publication path and metrics

[`operational_stream.py`](operational_stream.py) owns the non-blocking publisher.

- Producers call `emit_nowait()` synchronously and never await Redis.
- Two fixed-capacity queues: 1,792 routine slots and 256 error/critical priority slots.
- `drain()` always empties the priority queue first.
- A secondary sink (`AlarmJournal`) persists alarm/error events to Postgres only **after** successful Redis publication.

Dispatcher health fields:

| Field | Meaning |
|---|---|
| `queued_routine` | Events waiting in the routine buffer. |
| `queued_priority` | Events waiting in the priority buffer. |
| `dropped_routine` | Routine events discarded because the queue was full. |
| `dropped_priority` | Priority events discarded because the queue was full. |
| `published` | Events successfully written to Redis. |
| `failed_dispatches` | Events that failed serialization or Redis write. |
| `secondary_failures` | Alarm lifecycle rows that exhausted all retries. |

Reader health fields:

| Field | Meaning |
|---|---|
| `malformed_entries` | Stream entries that could not be parsed or had missing fields. |
| `read_failures` | Redis read failures. |

## Consumers: history and live SSE

Routes live in [`../routes/operational_events.py`](../routes/operational_events.py).

- `GET /api/events/history` returns a bounded, cursor-paged window. Default limit 200, max 500, bounded scan up to 5,000. Supports `before` or `after`, room, cluster, category, severity, and `type` filters.
- `GET /api/events/stream?after=<cursor>` returns `text/event-stream` with `Cache-Control: no-cache`. It replays strictly after the supplied Redis ID, then tails with `XREAD` blocking reads.
- SSE frames use `id:`, `event: operational_event`, and `data:` lines. Filtered events produce `id:`-only cursor frames. Heartbeats are comment frames emitted every 15 seconds of inactivity.

Authentication uses the existing `X-API-Key` header only. Query-string tokens are never accepted.

## Reset and reconnect behavior

| Situation | Server behavior | Client behavior |
|---|---|---|
| Trimmed cursor | HTTP 409 with `operational_event_cursor_trimmed`, `earliest_cursor`, and `latest_cursor`. | Clear resident rows, reset cursors, reload history from `earliest_cursor`. |
| Auth failure (401/403) | Returns the status. | Pause retries; surface auth failure. |
| Stale heartbeat | Connection stays open. | Reconnect if 45 seconds pass without data or heartbeat. |
| Disconnect / network error | Reader detects disconnect. | Jittered 1 to 30 second backoff, then reconnect at last seen cursor. |
| Malformed frame | Bad entries are skipped and counted. | Ignore unparsable frames; unknown event types render fallback UI. |

## Alarm lifecycle durability

Alarm and error lifecycle events are durable for 30 days using the existing `control_history` hypertable. [`alarm_journal.py`](alarm_journal.py) writes reserved rows:

- `channel = -1`
- `device_name = alarm:<alarm_name>`
- `mode = alarm:<lifecycle>:<severity>`
- `old_state`/`new_state` encode the lifecycle transition
- `reason` carries the event correlation information where it fits

Normal control-history reads keep `channel >= 0`, so alarm rows never appear as duplicate relay activity.

## Retention activation and rollback irreversibility

Control-history retention policies are in [`Infrastructure/database/operational_history_retention.sql`](../../database/operational_history_retention.sql). They are **operator-controlled** and **idempotent**.

- Raw `automation_state` and `effective_setpoints`: 7 days.
- `control_history`, `monitoring_automation_state_1min/5min`, `monitoring_effective_setpoints_1min/5min`: 30 days.
- Aggregate refresh policies start at a 6-day offset, leaving one day of margin before raw rows expire.
- The script aborts unless the database name matches `monitoring_test_*` and `app.operational_retention_disposable=1`. `cea_sensors` is explicitly rejected.
- The script is not referenced by `deploy.sh`, `finalize-deploy.sh`, service startup, or any supervisor path.

Rollback warning: removing or disabling a retention policy stops future deletion, but it cannot restore rows that the policy already dropped. Plan separate backups before enabling policies in production.

## Unknown type compatibility

Unknown `event_type` values must not break consumers. The backend validates only the envelope and the declared payload family; a new type with a known family parses successfully. The frontend registry renders a safe fallback label. Unknown payload keys inside an otherwise valid payload should be carried through so the event is still visible.

## Observability-only boundary

Operational events are read-only observability. No consumer may:

- Drive relay, dimming, PID, scheduler, notification, or configuration writes.
- Use consumer groups for browser fan-out.
- Accept the API key in a query parameter.
- Treat the event stream as a control or command bus.

A static integration test rejects any import or function call from event transport/store modules to control-write or hardware APIs.

## Adding a new event type

1. Add the typed payload model to [`operational_models.py`](operational_models.py) if the event introduces a new shape. Otherwise reuse an existing family.
2. Add the new payload to the `OperationalEventPayload` discriminated union.
3. Update `_payload_category()` if the family is new.
4. Emit the event at the authoritative decision or commit edge. Use a stable `correlation_id` to pair causes with effects.
5. If it is a persisted mutation, mark the route with `@emits_operational_mutation` or update the reviewed non-persistent exclusion map in [`mutation_exclusions.py`](mutation_exclusions.py).
6. Add a human label in [`Infrastructure/frontend/src/features/event-log/presentation/eventRegistry.ts`](../../../frontend/src/features/event-log/presentation/eventRegistry.ts) if the type should display a friendly name.
7. Add a contract round-trip test in [`app/tests/pure/test_operational_event_models.py`](../tests/pure/test_operational_event_models.py) and a parser/formatter test in the frontend feature tests.

Do not change the Redis transport, SSE framing, reconnect logic, or page integrations to add a type.

## Failure modes

| Failure | Effect | Detection |
|---|---|---|
| Redis unavailable at startup | Service starts with a no-op sink; events are silently dropped with no delay. | Dispatcher health `failed_dispatches` increments. |
| Queue saturation | Routine or priority events are dropped without blocking the control loop. | `dropped_routine` or `dropped_priority` increments. |
| Oversized payload | Serialization rejects before Redis. | `failed_dispatches` increments. |
| Secret-bearing diff key | Pydantic validation rejects the event; details in logs. | `failed_dispatches` increments. |
| Trimmed SSE cursor | Server returns 409. | Client reset metric triggers. |
| DB backup on alarm journal | Redis event still succeeds; alarm retries 0.1s, 0.5s, 2s. | `secondary_failures` increments after exhaustion. |
| Malformed stream entry | Isolated and skipped; later entries still delivered. | `malformed_entries` increments. |

## Static checks used as proof

The following checks are run against the source and documentation to reject forbidden patterns:

- No `params.set('token', ...)` or query-token path in event transport: `grep -R "params.set('token'" Infrastructure/frontend/src/features/event-log/` must be empty.
- No consumer-group usage in the operational reader: `grep -R "xreadgroup\|consumer.*group\|CREATEGROUP" Infrastructure/automation-service/app/events/` must be empty.
- No production policy invocation in automation/deploy paths: `grep -R "operational_history_retention.sql\|monitoring_read_models_activate_policies.sql" Infrastructure/automation-service Infrastructure/scripts/deploy.sh deploy.sh finalize-deploy.sh` must be empty, except for the disposable database test harness.
- No control-write or hardware API imports from event-log modules: enforced by `test_operational_event_end_to_end.py` AST assertions and by the frontend contract test.

Run the full Todo 19 gate set before declaring any change complete.
