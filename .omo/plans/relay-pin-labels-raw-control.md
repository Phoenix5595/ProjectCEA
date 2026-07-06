# relay-pin-labels-raw-control - Work Plan

## TL;DR (For humans)

**What you'll get:** Relay channels that have no device assigned can still be toggled ON for a timed duration (5m/10m/30m/1h) straight from the relay matrix — the timer runs on the server, so it survives page closes and turns OFF by itself. Every place the UI previously showed a raw channel integer (0–15) now shows the relay number (R1–R16) plus its MCP23017 pin label (GPA0–GPA7, GPB0–GPB7).

**Why this approach:** The existing device-assigned timer path was just made backend-based (boulder: `manual_expires_at` column + control-loop sweep). To meet the same survival guarantee for unassigned relays — which have no device row to write to `control_history` — we mirror the pattern with a lightweight Redis override record and a parallel sweep tick operating on the raw `redis.Redis` client (the `AutomationRedisClient` wrapper only exposes `get`). Pin labels reuse the existing `getRelayPinLabel()` which already returns `GPA0`/`GPB7`, so zero churn in `relayViewModel.ts`.

**What it will NOT do:**
- Will NOT change the internal `channel` integer field (0–15) — it's the MCP23017 hardware address index and stays as-is for hardware addressing.
- Will NOT add DB `control_history` rows for unassigned raw-channel overrides (Redis-only; raw toggles are commissioning/test actions, not audited control actions).
- Will NOT show an "Auto" button on unassigned relays (there's no device to set a mode on).
- Will NOT rename `RelayChannelViewModel.channel` or break the `RELAY_TO_CHANNEL` mapping.

**Effort:** Short
**Risk:** Low — mirrors an existing, just-validated pattern; no schema migration needed.
**Decisions to sanity-check:** GPA0/GPB7 label format (kept as-is); Redis-only override record (no DB audit for raw toggles); sweep runs on the existing control-loop tick; raw `redis.Redis` SET/DELETE calls wrapped in `asyncio.to_thread` from async contexts.

Your next move: approve, then `$start-work`. Full execution detail follows below.

---

> TL;DR (machine): Short, Low-risk — backend raw-channel timer via Redis+sweep (using raw redis.Redis client) + frontend pin-label display (R# · GPA0/GPB7), remove all raw channel-number UI.

## Scope
### Must have
- `POST /api/hardware/relays/channel/{channel}/state` accepts optional `duration_seconds`; when state=1 + duration set, writes a Redis override record `cea:relay:manual_override:{channel}` with `expires_at`; the control loop auto-turns the channel OFF when it expires (survives page close). When state=1 with NO duration, any prior override record is DELETED (cancels prior timer → indefinite ON). When state=0, the override record is DELETED first, then the channel is turned OFF.
- `controlChannel(channel, state, durationSeconds?)` method on the frontend `apiClient`.
- `RelayChannelBox` displays `R{relayNum} · {pinLabel}` (e.g. `R1 · GPB7`); tooltip updated; no raw `CH {channel}` anywhere in the UI.
- Menu opens on unassigned relays (no device bound); 'Auto' button hidden when unassigned; ON-timer + Off work via the raw channel endpoint.
- `handleRelayMenuAction` branches: assigned → existing `controlDevice` path; unassigned → `controlChannel`.
- Channel assignment table in `DeviceManager` shows `R{relayNum}` + `{pinLabel}` instead of the raw channel integer.
- Every remaining user-facing reference to the raw channel number (`CH {n}`, `Channel {n}`) is replaced with relay# + pin label.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do NOT rename the internal `channel` field on `RelayChannelViewModel`/`ChannelInfo` — it's the hardware address index.
- Do NOT remove `RELAY_TO_CHANNEL` / `CHANNEL_TO_RELAY` / `getRelayNumber` / `getRelayPinLabel` — they're reused.
- Do NOT write `control_history` rows for unassigned raw-channel overrides (Redis-only).
- Do NOT add `set`/`setex`/`delete` methods to `AutomationRedisClient` — use the raw `redis.Redis` instance via `automation_redis.redis_client` directly (it's already exposed as a public attribute at `app/redis/__init__.py:76`).
- Do NOT touch the existing device-assigned timer path (boulder work, already deployed & verified).
- Do NOT add a DB migration / schema change.
- Do NOT use frontend `setTimeout` for the OFF timer — MUST be backend-driven.
- Do NOT make blocking sync calls inside the control loop — wrap raw `redis.Redis` calls with `await asyncio.to_thread(...)` (matches `StateManager` pattern at `app/state/__init__.py:443`).

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after. Backend: pytest (existing `automation-service/tests/` harness). Frontend: `tsc --noEmit` + `npm run build`.
- Evidence: `.omo/evidence/task-N-relay-pin-labels-raw-control.<ext>`
- Interactive UI verification moved to Todo 9 (post-deploy) — see F6.
- Deploy: single `./deploy.sh` after all todos pass.

## Execution strategy
### Parallel execution waves
- **Wave 1 (backend, sequential):** Todo 1 (helper function) → Todo 2 (route + duration_seconds + Redis override write via `automation_redis.redis_client`) → Todo 3 (control-loop sweep using `self.database._automation_redis.redis_client`). 1, 2, 3 are sequential (each builds on the prior's symbols).
- **Wave 2a (frontend, parallel):** Todo 4 (apiClient), Todo 5 (RelayChannelBox), Todo 7 (table column) — three different files, no conflicts.
- **Wave 2b (frontend, after Wave 2a):** Todo 6 (handleRelayMenuAction branch — depends on Todo 4's apiClient method AND must be serialized with Todo 7 since both edit `DeviceManager.tsx`), then Todo 8 (UI reference sweep — depends on Todos 5 & 7).
- **Wave 3 (final):** Todo 9 (build + tests + deploy). Depends on ALL of Wave 1+2.

### Dependency matrix (corrected per Metis F4)
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | 2, 3 | — |
| 2 | 1 | 3, 4, 6 | — |
| 3 | 1, 2 | 9 | — |
| 4 | 2 | 6 | 5, 7 |
| 5 | — | 8 | 4, 7 |
| 6 | 2, 4, 7-done | — | — (serialized with 7: same file) |
| 7 | — | 6, 8 | 4, 5 |
| 8 | 5, 7 | 9 | — |
| 9 | 3, 5, 6, 7, 8 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [x] 1. Add `relay_raw_override_key(channel)` helper to Redis schema module
  What to do / Must NOT do: Add a helper function `relay_raw_override_key(channel: int) -> str` to `app/redis/schema.py` returning `f"cea:relay:manual_override:{channel}"`. This matches the existing helper-function convention (`sensor_key`, `mode_key`) rather than introducing a template-string constant (Metis F8). Do NOT add a TTL constant — the override record carries `expires_at` in its JSON value; the Redis key gets a 25h cleanup TTL set at write time (Todo 2) as a safety net only, not the expiry mechanism.
  Parallelization: Wave 1 | Blocked by: — | Blocks: 2, 3
  References (executor has NO interview context - be exhaustive):
  - `Infrastructure/automation-service/app/redis/schema.py` (existing `RELAY_CHANNELS`/`RELAY_TIMESTAMPS` constants and any existing `*_key()` helper functions — match their style)
  - `Infrastructure/AGENTS.md` → "REDIS ARCHITECTURE" (key schema conventions, `cea:` prefix)
  - `Infrastructure/automation-service/AGENTS.md` → "Key Schema (cea:* prefix)"
  Acceptance criteria (agent-executable): `grep -n "def relay_raw_override_key" Infrastructure/automation-service/app/redis/schema.py` returns one line. `cd Infrastructure/automation-service && ruff check app/redis/schema.py` passes. Importing and calling `relay_raw_override_key(15)` returns `"cea:relay:manual_override:15"`.
  QA scenarios: happy — function exists and formats correctly. failure — (none, trivial helper). Evidence `.omo/evidence/task-1-relay-pin-labels-raw-control.txt`
  Commit: N | (bundled with Todo 2/3 commit)

- [x] 2. Extend raw channel control route: accept `duration_seconds`, write Redis override record via raw `redis.Redis` client
  What to do / Must NOT do:
  - Add `duration_seconds: int | None = Field(default=None, ge=1, le=3600)` to `RelayChannelControlRequest` in `app/routes/hardware.py`.
  - **Add `automation_redis: AutomationRedisClient = Depends(get_automation_redis)` to the `set_relay_channel_state` route function signature** (Metis F7). The `get_automation_redis` dependency is already declared at `hardware.py:33-35`. Currently the route only injects `relay_manager`.
  - In the route body, after argument validation:
    - **state==1 + `duration_seconds` set:** call `await relay_manager.set_channel_state(channel, 1)`. Compute `expires_at = datetime.now(UTC) + timedelta(seconds=duration_seconds)`. Write Redis override key via `automation_redis_key = relay_raw_override_key(channel)`, `payload = json.dumps({"expires_at": expires_at.isoformat(), "state": 1})`, `await asyncio.to_thread(automation_redis.redis_client.setex, automation_redis_key, duration_seconds + 86400, payload)` (25h safety TTL — the sweep in Todo 3 is the actual expiry driver). (Metis F1: use `automation_redis.redis_client.setex` — the raw `redis.Redis` instance at `app/redis/__init__.py:76` — NOT a nonexistent `automation_redis.set`).
    - **state==1 + NO `duration_seconds`:** DELETE any existing override key FIRST (Metis F5 — cancel prior pending timer), `await asyncio.to_thread(automation_redis.redis_client.delete, relay_raw_override_key(channel))`, then call `await relay_manager.set_channel_state(channel, 1)` (indefinite ON until manual Off).
    - **state==0:** DELETE the override key first `await asyncio.to_thread(automation_redis.redis_client.delete, relay_raw_override_key(channel))`, then `await relay_manager.set_channel_state(channel, 0)`. Order matters: DELETE first so a sweep firing in the window is a no-op on this channel; even if the sweep sees the key before DELETE, it only turns OFF (which state==0 also does — harmless).
  - Add imports to `hardware.py`: `import asyncio` (already imported at line 5), `from datetime import datetime, timedelta, timezone` (verify current imports; add if missing), `import json` (line 6 already imported), `from app.redis.schema import relay_raw_override_key` (add to existing import on line 14).
  - Must NOT write to `control_history` table for raw channel overrides (Redis-only per scope).
  - Must NOT touch the device-assigned path in `app/routes/devices.py`.
  - Must NOT add `set`/`setex`/`delete` methods to `AutomationRedisClient` — use the public `automation_redis.redis_client` attribute directly.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 3, 4, 6
  References:
  - `Infrastructure/automation-service/app/routes/hardware.py:98-134` (current `RelayChannelControlRequest` + `set_relay_channel_state` — already added in-session, worker MUST verify and extend; current signature at line 107-111 only injects `relay_manager`)
  - `Infrastructure/automation-service/app/routes/hardware.py:33-35` (`get_automation_redis` dependency declaration)
  - `Infrastructure/automation-service/app/redis/__init__.py:76,99,135-146` (`AutomationRedisClient.redis_client` public attr + existing `get` method — confirms no `set`/`setex`/`delete` on wrapper)
  - `Infrastructure/automation-service/app/state/__init__.py:443,501` (the established `await asyncio.to_thread(self._redis_client.setex, key, ttl, value)` / `await asyncio.to_thread(self._redis_client.delete, key)` pattern — mirror it)
  - `Infrastructure/automation-service/app/routes/devices.py:219-234` (existing device-path `manual_expires_at` pattern to mirror stylistically)
  - `Infrastructure/automation-service/app/control/relay_manager.py:179-196` (current `set_channel_state` — already fixed to allow unmapped channels, worker MUST verify)
  Acceptance criteria:
  - `curl -X POST http://mothernode:8080/api/hardware/relays/channel/15/state -d '{"state":1,"duration_seconds":300}'` returns `{"channel":15,"state":1,"ok":true}` and `redis-cli GET cea:relay:manual_override:15` returns JSON blob with `expires_at` ~5min in the future.
  - `curl -X POST http://mothernode:8080/api/hardware/relays/channel/15/state -d '{"state":0}'` returns ok and `redis-cli GET cea:relay:manual_override:15` returns nil.
  - `curl -X POST http://mothernode:8080/api/hardware/relays/channel/15/state -d '{"state":1}'` (no duration) returns ok and `redis-cli GET cea:relay:manual_override:15` returns nil (indefinite ON does not leave a stale override key — Metis F5 verification).
  - `cd Infrastructure/automation-service && ruff check app/routes/hardware.py` passes.
  QA scenarios: happy — timed ON writes override record + turns channel ON; indefinite ON deletes any prior override; manual OFF deletes override + turns OFF. failure — `duration_seconds: 0` rejected by pydantic (`ge=1`); `duration_seconds: 3601` rejected (`le=3600`); channel 16 rejected by existing 0-15 guard; `state: 2` rejected by existing `ge=0,le=1`. Evidence `.omo/evidence/task-2-relay-pin-labels-raw-control.txt`
  Commit: Y | feat(hardware): raw channel control with backend timer via Redis override

- [x] 3. Add `_expire_raw_channel_overrides()` sweep to ControlEngine using `database._automation_redis`
  What to do / Must NOT do:
  - Add `async def _expire_raw_channel_overrides(self) -> None` on `ControlEngine` in `app/control/control_engine.py`, modeled on the existing `_expire_manual_overrides()` at line 427.
  - Reach the raw Redis client via `self.database._automation_redis.redis_client` (Metis F2: there is NO `self.redis_client` on ControlEngine. `self.database._automation_redis` IS the `AutomationRedisClient` already used at line 109 to construct `SetpointManager`. The `AutomationRedisClient.redis_client` attr is the raw `redis.Redis` instance per `app/redis/__init__.py:76`).
  - Logic: for `channel` in `range(16)`: `key = relay_raw_override_key(channel)`; `raw = await asyncio.to_thread(self.database._automation_redis.redis_client.get, key)`; if `raw is None`, continue; parse JSON, read `expires_at`; `if datetime.fromisoformat(payload["expires_at"]) <= datetime.now(UTC)`: `await self.relay_manager.set_channel_state(channel, 0)`, `await asyncio.to_thread(self.database._automation_redis.redis_client.delete, key)`, `logger.info(f"Raw channel {channel} manual override expired, turned OFF")`.
  - Add `await self._expire_raw_channel_overrides()` to `run_control_loop` immediately AFTER the existing `await self._expire_manual_overrides()` call at line 268 (inside the try/except at lines 267-270, or as a sibling try/except — match the existing error-isolation pattern).
  - Wrap ALL raw `redis.Redis` calls in `await asyncio.to_thread(...)` — the control loop runs every 1-5s and MUST NOT make blocking sync calls (AGENTS.md "Never use sleep() or blocking calls — Kills deterministic timing"). This matches the StateManager pattern at `app/state/__init__.py:443,501`.
  - Add imports: `from app.redis.schema import relay_raw_override_key` (verify; add if missing); `import json` (verify; add if missing); `from datetime import datetime, timezone` (verify existing datetime imports; add timezone if missing).
  - Must NOT write `control_history` rows. Must NOT use `set_channel_state` on a Future `expires_at` window (skip those).
  - Verify `self.relay_manager` exists on ControlEngine (line 66 — confirmed yes).
  Parallelization: Wave 1 | Blocked by: 1, 2 | Blocks: 9
  References:
  - `Infrastructure/automation-service/app/control/control_engine.py:45-120` (`__init__` — confirms `self.relay_manager` at line 66, `self.database` at line 67, `self.database._automation_redis` at line 109; NO `self.redis_client` attr exists)
  - `Infrastructure/automation-service/app/control/control_engine.py:260-270,427-447` (existing `_expire_manual_overrides` call site + pattern)
  - `Infrastructure/automation-service/app/redis/__init__.py:76,99,135-146,146` (`redis_client` raw attr + `get` method — confirms only `get` on wrapper)
  - `Infrastructure/automation-service/app/state/__init__.py:443,501` (the `await asyncio.to_thread(self._redis_client.setex/delete, ...)` template to mirror)
  - `Infrastructure/automation-service/app/control/relay_manager.py:179-196` (`set_channel_state` is async — `await` is correct)
  Acceptance criteria:
  - New pytest `Infrastructure/automation-service/tests/test_raw_channel_expiry.py`: set a fake override record (via a mocked `redis_client.get` returning `{"expires_at":"<10s ago>","state":1}`) and run the sweep; assert `relay_manager.set_channel_state` was called with `(channel, 0)` and the redis key was deleted. Also: future `expires_at` (10s ahead) → sweep does NOT turn it off; missing key (`get` returns None) → sweep no-ops for that channel.
  - `cd Infrastructure/automation-service && pytest tests/test_raw_channel_expiry.py -v` passes.
  - `cd Infrastructure/automation-service && ruff check app/control/control_engine.py` passes.
  QA scenarios: happy — expired override → channel turned OFF + key deleted + log emitted. failure — override with future `expires_at` is left alone; missing key (no override) → sweep no-ops for that channel. Evidence `.omo/evidence/task-3-relay-pin-labels-raw-control.txt`
  Commit: Y | feat(control): raw channel manual-override auto-expiry sweep

- [x] 4. Add `controlChannel()` method to frontend apiClient
  What to do / Must NOT do:
  - Add to `ApiClient` in `Infrastructure/frontend/src/services/api.ts` (model on the existing `controlDevice` at line 561):
    ```ts
    async controlChannel(channel: number, state: 0 | 1, durationSeconds?: number): Promise<JsonObject> {
      const response = await this.automationClient.post(
        `/api/hardware/relays/channel/${channel}/state`,
        { state, duration_seconds: durationSeconds ?? null }
      );
      return response.data;
    }
    ```
  - Use the existing `automationClient` (Caddy :8080 → :8001). Do NOT add a new axios instance.
  - `JsonObject` is module-local at `api.ts:26` (`type JsonObject = Record<string, unknown>`) — already in scope (Metis F6 verified).
  - Do NOT touch `controlDevice` / `setDeviceMode` (used by the assigned path).
  Parallelization: Wave 2a | Blocked by: 2 (endpoint contract) | Blocks: 6 | Can parallelize with: 5, 7
  Acceptance criteria: `grep -n "controlChannel" Infrastructure/frontend/src/services/api.ts` returns the method. `cd Infrastructure/frontend && npx tsc --noEmit` passes.
  QA scenarios: happy — method compiles, types match `0|1` state. failure — passing `state: 2` is a TS error (the `0|1` union rejects it). Evidence `.omo/evidence/task-4-relay-pin-labels-raw-control.txt`
  Commit: N | (bundled with Wave 2 commit)

- [x] 5. Update `RelayChannelBox` display: pin label, drop raw channel number, remove canControl gate, hide Auto for unassigned
  What to do / Must NOT do:
  - Replace line 123 `<span ...>R{relayNum} · CH {channel.channel}</span>` with `R{relayNum} · {channel.pinLabel}` (the `pinLabel: string` field already exists on `RelayChannelViewModel` at `relayViewModel.ts:66` and is populated at line 194 — Metis F7 VERIFIED OK).
  - Update the tooltip at line 85: drop `CH ${channel.channel}`, use `R{relayNum} · ${channel.pinLabel}`.
  - Remove the `canControl` gate (line 66): replace `const canControl = isAssignedToRoom && Boolean(channel.assignedDeviceName && channel.location && channel.cluster)` with `const canControl = true` (or remove the variable and simplify the button `disabled` to `false`). Per Metis F8, `canControl` is only used at lines 66, 130, 133, 138, 139 — replacing with `true` leaves all branches valid (the dead `!canControl` branch is harmless).
  - Conditionally render the 'Auto' button (lines 92-94): wrap in `{channel.isAssigned && (...)}`. The ON-timer buttons and 'Off' button remain always-visible. Keep the `'auto'` action in the `onMenuAction` prop type (still used for assigned relays).
  - Update the `'Assign a device first'` title (line 139) to `'Toggle relay channel'` since unassigned relays are now controllable.
  - Must NOT change `getRelayPinLabel()` (already returns `GPA0`/`GPB7` — the chosen format).
  Parallelization: Wave 2a | Blocked by: — | Blocks: 8 | Can parallelize with: 4, 7
  Acceptance criteria: `grep -n "CH {channel" Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx` returns nothing. `grep -n "pinLabel" Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx` returns the display + tooltip lines. `cd Infrastructure/frontend && npx tsc --noEmit && npm run build` pass. (Per Metis F6, interactive UI verification is deferred to Todo 9.)
  QA scenarios: happy — `CH {channel}` pattern is gone; `pinLabel` is rendered in display + tooltip; tsc + build green. failure — `grep "CH {"` returns a hit (regression). Evidence `.omo/evidence/task-5-relay-pin-labels-raw-control.txt`
  Commit: N | (bundled with Wave 2 commit)

- [x] 6. Update `handleRelayMenuAction` to branch: assigned vs unassigned (SERIALIZED WITH TODO 7 — same file)
  What to do / Must NOT do:
  - In `Infrastructure/frontend/src/components/DeviceManager.tsx` `handleRelayMenuAction` (lines 338-387): branch on whether the channel has a device assignment.
  - **Assigned** (existing path, unchanged): `setDeviceMode('manual')` + `controlDevice(...,1, reason, durationSeconds)` for timers; `setDeviceMode('auto')` for 'auto'; `setDeviceMode('manual')` + `controlDevice(...,0,...)` for 'off'.
  - **Unassigned** (new): `controlChannel(channel, 1, durationSeconds)` for timers (5m=300, 10m=600, 30m=1800, 1h=3600); `controlChannel(channel, 0)` for 'off'; 'auto' is not sent (hidden in Todo 5, but defensively: if received, treat as `controlChannel(channel, 0)` and ignore).
  - Use `channelInfo?.device_name && channelInfo.location && channelInfo.cluster` as the "is assigned" test (matches the prior `canControl` guard at line 343 — Metis F9 VERIFIED OK).
  - Toast messages: unassigned path says `Relay R{relayNum} ({pinLabel}) ON for {duration}` / `Relay R{relayNum} ({pinLabel}) turned off`.
  - Keep the existing try/catch + `refreshRelayState()` + `setMenuOpenChannel(null)` after success.
  - **Imports (Metis F3 — corrected):** the current import block at lines 17-24 contains `getRelayPinLabel` (line 21) but NOT `getRelayNumber`. Add `getRelayNumber` to the import list. Do NOT re-add `getRelayPinLabel` (it's already imported).
  - Must be done AFTER Todo 7 (both edit DeviceManager.tsx — Todo 7 edits the table at lines 576-620, Todo 6 edits `handleRelayMenuAction` at lines 338-387; sequenced to avoid merge conflicts, per Metis F4).
  Parallelization: Wave 2b | Blocked by: 2, 4, 7-done | Blocks: —
  References:
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:17-24` (CURRENT imports — confirmed `getRelayPinLabel` IS imported, `getRelayNumber` is NOT; Metis F3 finding)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:338-387` (current `handleRelayMenuAction`)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:343` (the guard condition — invert for the branch test)
  - `Infrastructure/frontend/src/components/devices/relayViewModel.ts:31-33,82-88` (`getRelayNumber`, `getRelayPinLabel`)
  - `Infrastructure/frontend/src/services/api.ts` (Todo 4's `controlChannel` method)
  Acceptance criteria: `grep -n "controlChannel" Infrastructure/frontend/src/components/DeviceManager.tsx` returns the unassigned branch. `grep -n "getRelayNumber" Infrastructure/frontend/src/components/DeviceManager.tsx | head -2` returns BOTH the import + at least one usage. `cd Infrastructure/frontend && npx tsc --noEmit && npm run build` pass.
  QA scenarios: happy — `grep` confirms the unassigned branch and `getRelayNumber` import; tsc/build green. failure — unassigned path still falls through to `controlDevice` (regression); `getRelayNumber used but not imported` TS error. Evidence `.omo/evidence/task-6-relay-pin-labels-raw-control.txt`
  Commit: N | (bundled with Wave 2 commit)

- [x] 7. Update DeviceManager channel assignment table: drop channel number, show relay# + pin label (SERIALIZED WITH TODO 6)
  What to do / Must NOT do:
  - In `Infrastructure/frontend/src/components/DeviceManager.tsx` table row (lines 610-620). Currently line 618 shows `<div>{relayChannel.channel}</div>` (raw integer), line 619 shows `<div ...>{getRelayPinLabel(relayChannel.channel)}</div>`.
  - Replace line 618 with `<div>R{getRelayNumber(relayChannel.channel)}</div>` (e.g. renders `R1`). Line 619 unchanged (already renders pin label).
  - Rename the column header from `Channel` to `Relay` (line 577-579).
  - **Imports (Metis F3 — corrected):** `getRelayPinLabel` IS already imported (line 21). ADD `getRelayNumber` to the existing import block at lines 17-24. This is the SAME import addition Todo 6 needs — they share the edit (which is WHY Todo 6 and Todo 7 are serialized, per Metis F4: both touch the import block + DeviceManager.tsx).
  - Must NOT remove the `id={`channel-row-${relayChannel.channel}`}` (line 600 — needed for scroll-into-view on edit).
  - Must NOT change the `key` props (line 599 — uses `relayChannel.channel` as stable internal id).
  - Must NOT change the `channel` numeric prop sent to `onSelectChannel`/`onMenuAction` callbacks — it's the hardware address index.
  Parallelization: Wave 2a | Blocked by: — | Blocks: 6, 8 | Can parallelize with: 4, 5
  References:
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:576-620` (table header + channel cell)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:17-24` (imports — confirmed `getRelayNumber` is NOT in the current import block; Metis F3)
  - `Infrastructure/frontend/src/components/devices/relayViewModel.ts:31-33,82-88`
  Acceptance criteria: `grep -n "{relayChannel.channel}</div>" Infrastructure/frontend/src/components/DeviceManager.tsx` returns nothing (the raw integer cell is gone). `grep -n "getRelayNumber" Infrastructure/frontend/src/components/DeviceManager.tsx | head -3` returns the import line + at least one usage (the table cell). `cd Infrastructure/frontend && npx tsc --noEmit && npm run build` pass.
  QA scenarios: happy — table column header is `Relay`; column cell shows `R1` over `GPB7`. failure — raw `15` shown anywhere in the column. Evidence `.omo/evidence/task-7-relay-pin-labels-raw-control.txt`
  Commit: N | (bundled with Wave 2 commit)

- [x] 8. Sweep for remaining raw channel-number UI references and replace with pin label / relay#
  What to do / Must NOT do:
  - Run `grep -rn "CH {channel\|CH \${channel\|Channel {channel\|channel.channel}" Infrastructure/frontend/src --include="*.tsx" --include="*.ts"` and audit every hit.
  - User-facing (rendered text, tooltips, titles, toast messages): replace `channel.channel` / `CH {channel.channel}` with `getRelayNumber(channel.channel)` + `getRelayPinLabel(channel.channel)` as appropriate to context.
  - Internal (keys, ids, callbacks, prop values sent to the backend, the `channel` numeric prop): leave as the raw `channel.channel` integer — it's the hardware address.
  - Check `RelayChannelMatrix.tsx` (already shows relay# labels via `leftRelayNum`/`rightRelayNum` at lines 118-119,128,139 — verify, no change expected).
  - Check `DfrBoardsPanel.tsx` and `SystemSettingsPanel.tsx` for any stray channel-number display.
  - Must NOT change backend `channel` path params or the `channel` field in API request/response bodies.
  Parallelization: Wave 2b | Blocked by: 5, 7 | Blocks: 9
  References:
  - `Infrastructure/frontend/src/components/devices/RelayChannelMatrix.tsx:118-119,128,139` (verify relay# labels, not channel#)
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:85,123` (Todo 5 covers these; this todo double-checks nothing else surfaced)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx`
  - `Infrastructure/frontend/src/components/devices/SystemSettingsPanel.tsx`
  Acceptance criteria: `grep -rn "CH {" Infrastructure/frontend/src --include="*.tsx"` returns nothing. `cd Infrastructure/frontend && npm run build` passes.
  QA scenarios: happy — no "CH 5" or raw channel-integer display anywhere in the relay UI. failure — any stray raw channel integer in a tooltip/title. Evidence `.omo/evidence/task-8-relay-pin-labels-raw-control.txt`
  Commit: N | (bundled with Wave 2 commit)

- [x] 9. Final: build + type-check + backend tests + deploy + post-deploy interactive verification
  What to do / Must NOT do:
  - `cd Infrastructure/automation-service && ruff check . && pytest tests/ -q` (must include the new test from Todo 3).
  - `cd Infrastructure/frontend && npx tsc --noEmit && npm run build`.
  - `./deploy.sh` from repo root (single deploy).
  - **Post-deploy interactive verification (moved here from Todos 5+6 per Metis F6):** in a browser at `http://mothernode:8080`, navigate to Device Manager → relay matrix:
    1. Confirm a box for an unassigned relay (e.g. relay 1 / GPB7) displays `R1 · GPB7` (NOT `CH 15`).
    2. Confirm the 'Auto' button is hidden on that unassigned box; the ON-timers + Off buttons are visible.
    3. Click 'ON 5m' on the unassigned box → confirm toast `Relay R1 (GPB7) ON for 5m`; confirm `redis-cli GET cea:relay:manual_override:15` returns JSON with `expires_at` ~5min ahead.
    4. Reload the browser page; confirm the channel stays ON (timer survived the page close).
    5. Wait ~5 minutes; confirm the channel has turned OFF by itself and `redis-cli GET cea:relay:manual_override:15` returns nil.
    6. Confirm the channel-assignment table column header is `Relay` (not `Channel`) and shows `R1` over `GPB7`.
  - Verify curl-level: `curl http://mothernode:8080/api/hardware/relays/state` returns 16-channel state; `curl -X POST http://mothernode:8080/api/hardware/relays/channel/15/state -d '{"state":1,"duration_seconds":60}'` returns ok.
  - Must NOT roll back unless health checks fail per deploy.sh's built-in behavior.
  Parallelization: Wave 3 | Blocked by: 3, 5, 6, 7, 8 | Blocks: —
  References:
  - `deploy.sh` (root), `rollback-deploy.sh`
  - `Infrastructure/automation-service/tests/` (pytest harness)
  Acceptance criteria: deploy succeeds (health checks pass), the manual QA sequence (steps 1-6) verifies the end-to-end timer SURVIVES a page reload (step 4) and turns the relay OFF after the duration (step 5), and the curl sequence confirms the API contract.
  QA scenarios: happy — deploy + timed-ON + page-reload-then-OFF all green. failure — deploy rolls back on health fail; timer doesn't survive page reload (regression to frontend setTimeout — would indicate backend override record wasn't written); timer doesn't expire (sweep broken). Evidence `.omo/evidence/task-9-relay-pin-labels-raw-control.txt`
  Commit: Y | (deploy only; no separate code commit — Wave 1+2 commits already made)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — every todo has References + Acceptance + QA + Commit; dependency matrix consistent (Metis F4 fixes verified); no decision left to the implementer.
- [x] F2. Code quality review — ruff clean (backend), tsc strict clean (frontend), no `any`/`unwrap`/bare except; matches the repo's existing patterns (repo-pattern, relay_manager, control_engine, StateManager's `asyncio.to_thread` usage).
- [x] F3. Real manual QA — the deploy's interactive sequence (Todo 9 steps 1-6) actually toggles an unassigned relay, the timer survives a full page reload (step 4), and the sweep turns it OFF after 5 minutes; the frontend matrix shows `R1 · GPB7` (not `CH 15`); the 'Auto' button is hidden on an unassigned relay box.
- [x] F4. Scope fidelity — internal `channel` field NOT renamed; `control_history` NOT written for raw overrides; frontend `setTimeout` NOT used; device-assigned path untouched; `set`/`delete` methods NOT added to `AutomationRedisClient` (raw `redis.Redis` used directly).

## Commit strategy
- Wave 1 (backend): one commit per todo that has `Commit: Y` (Todo 2, Todo 3). Todo 1 bundles into Todo 2.
- Wave 2 (frontend): all frontend todos (4-8) bundled into ONE commit at Wave 2 completion (since Todos 6+7 share DeviceManager.tsx and must be merged together): `feat(frontend): relay pin labels + unassigned relay control`.
- Wave 3 (deploy): Todo 9 produces no separate code commit; `deploy.sh` tags the release. If a hotfix is needed, use `rollback-deploy.sh`.

## Success criteria
- An unassigned relay (e.g. relay 1 / GPB7) can be turned ON for 5 minutes from the frontend relay matrix; the relay turns OFF by itself after 5 minutes EVEN IF the browser is closed and reopened (page reload does not cancel the timer — verified at Todo 9 step 4); the OFF is driven by the backend control-loop sweep reading a Redis override record, not a frontend timer.
- An indefinite ON (no duration) on a channel that previously had a timed override CANCELS the prior timer (the Redis override key is deleted — Metis F5 fix verified at Todo 2 acceptance criteria: `redis-cli GET` returns nil after indefinite ON).
- No user-facing string in the frontend shows the raw MCP23017 channel integer (0–15); everywhere it shows the relay number (R1–R16) and/or the pin label (GPA0–GPA7, GPB0–GPB7).
- The existing device-assigned relay control path is unchanged and regression-free.
- `ruff check .` (backend) and `tsc --noEmit` + `npm run build` (frontend) pass; the new pytest for the raw-channel sweep passes.
- Single deploy via `deploy.sh` succeeds and health checks stay green.
