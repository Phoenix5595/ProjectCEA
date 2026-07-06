# Draft: dfr-relay-channel-fixes

## Status
`status: awaiting-approval`
Pending action: write `.omo/plans/dfr-relay-channel-fixes.md`
Approval required from: user

## Goal
Kill two confirmed channel-identity bugs in one focused plan:
- **Bug 1 (frontend)**: DFR0971 dimming-channel slots are labelled with MCP23017 relay numbers and relay GPIO pin labels, falsely coupling the two hardware planes and "locking" each DFR channel to a relay.
- **Bug 2 (backend)**: After service restart, MCP23017 init forces all relays OFF in hardware, but the stale `cea:relay:channels` Redis key is never reconciled — so `/api/hardware/relays/state` returns the pre-restart ON states and the frontend shows dead relays as ON.

Both bugs violate AGENTS.md hardware separation ("MCP23017 = relays / DFR0971 = dimming / Never swap roles / Bus separation mandatory") and the live-state contract (Redis must reflect hardware truth).

## Evidence (with paths)

### Bug 1 — DFR slot shows relay identity
- `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:8` — `import { getRelayNumber, getRelayPinLabel } from './relayViewModel'`
- `DfrBoardsPanel.tsx:579` — `<div ...>R{getRelayNumber(ch)} · {getRelayPinLabel(ch)}</div>` where `ch` is the DFR dimming channel (0|1) of a `DfrBoard` (each DFR0971 board has 2 channels). `getRelayNumber(ch)` returns `CHANNEL_TO_RELAY[ch]` (an MCP23017 relay number) from `relayViewModel.ts:31`.
- `DfrBoardsPanel.tsx:737` — remove-light confirm string: `"This will also unbind relay R{getRelayNumber(ch)}...`" — uses the DFR dimming channel as a relay channel in the warning, so the warning shows the WRONG relay number.

### Bug 2 — Stale Redis relay state after restart
- `redis-cli GET "cea:relay:channels"` returns `[false, false, false, true, true, true, false, false, false, false, false, false, false, false, false, false]` — channels 3/4/5 marked ON even though hardware forced OFF at init.
- `Infrastructure/automation-service/app/hardware/mcp23017.py:68-89` — `_initialize_hardware()` writes `safe_off = 0xFF` to BOTH `GPIOA` and `GPIOB` on init → all 16 relays physically OFF after restart. NO Redis reconciliation happens here.
- `Infrastructure/automation-service/app/routes/hardware.py:176-229` — `relay_state()` endpoint reads `cea:relay:channels` from Redis FIRST (line 188), only falls through to `mcp.get_all_channels()` on a cache miss (line 198-200). Stale Redis wins over fresh hardware.
- `Infrastructure/automation-service/app/redis/schema.py:76` — `RELAY_CHANNELS = "cea:relay:channels"` (the stale key).
- `Infrastructure/automation-service/app/redis/schema.py` (also defines `RELAY_TIMESTAMPS` — same staleness class, needs the same reconciliation).

## Root cause
- **Bug 1**: DFR panel imports from `relayViewModel` (a module scoped to MCP23017 relay rendering) and applies relay-specific identifiers to DFR dimming-channel slots. The two hardware planes have separate identifiers by design; mixing them is the documented most-common bug pattern.
- **Bug 2**: The MCP23017 driver owns hardware init but has no Redis-side reconciliation step. The relay state endpoint trusts the Redis cache over the hardware read, and nothing on startup refreshes the Redis cache after `_initialize_hardware()` mutates the physical GPIO state.

## Components (topology lock)
- **C1 — DFR slot header relabelling** (`DfrBoardsPanel.tsx:579`): replace `R{getRelayNumber(ch)} · {getRelayPinLabel(ch)}` with a DFR-specific label using `board.board_id` + dimming-channel index `ch`. Adopted default: `DFR{board_id} · CH{ch}`.
- **C2 — Remove-light warning correctness** (`DfrBoardsPanel.tsx:737`): show the light's actual `bound_relay_channel` (from the light record) when present; omit the unbind suffix when no relay binding exists. Never use the DFR dimming-channel index as a relay channel.
- **C3 — Decouple DFR panel from relayViewModel** (`DfrBoardsPanel.tsx:8`): after C1+C2, remove the `import { getRelayNumber, getRelayPinLabel } from './relayViewModel'` line so the DFR panel carries no relay model. Add an inline DFR label helper (or a tiny `dfrViewModel.ts` if reused) only if needed.
- **C4 — Post-init Redis reconciliation** (`app/container.py` or `app/hardware/mcp23017.py` post-init hook): after `MCP23017Driver._initialize_hardware()` completes, read the actual hardware state via `mcp.get_all_channels()` and write it to Redis `cea:relay:channels`; clear `cea:relay:timestamps` (set to all-`None` array) so stale per-channel timestamps don't survive a restart. This pins Redis to actual post-init hardware truth on every startup.
- **C5 — Tests**:
  - Frontend (Bug 1): unit tests asserting (i) DFR slot label contains `DFR{board_id}` + `CH{ch}` and contains NO `R{n}` / `GPA` / `GPB`; (ii) remove-light warning shows the light's real `bound_relay_channel` when present, omits the unbind suffix when none.
  - Backend (Bug 2): unit test asserting that after `_initialize_hardware()` the Redis `cea:relay:channels` key matches `mcp.get_all_channels()` (i.e., all OFF post-init for active-LOW board), and `cea:relay:timestamps` is the all-`None` array.

## Out of scope (explicitly — separate plan)
- The empty `device_registry` table (alembic at version `008_device_registry`, seed migration `009_seed_device_registry_from_yaml.py` did not run). This is the root cause of the "fully empty device and relay mapping" symptom from the prior turn. Will be a separate plan.
- Backend `device_registry` schema, alembic, or any data-shape change.
- The "is the automation loop using a stale relay assignment" question — partially answered by Bug 2 fix, but the deeper device_registry-empty issue is separate.

## Approach (planned, post-approval)
- **One wave, two files** — frontend (`DfrBoardsPanel.tsx`) for Bug 1, backend (`mcp23017.py` or `container.py` startup) for Bug 2.
- TDD: tests written first asserting the bug, then make the change.
- Frontend uses the `DfrBoard` model already returned by `/api/lights/dfr/assignments` (`board_id`, `i2c_address`) — no API changes.
- Backend reconciliation runs after `_initialize_hardware()` so it always reflects whatever the init wrote (today all-OFF, but robust to future init changes).

## Adopted defaults (not asked — recorded)
- DFR slot label text: `DFR{board_id} · CH{ch}` (matches the `DfrBoard` model; clearest for hardware-tracing; reversible if you dislike).
- Bug 2 fix approach: post-init Redis reconciliation (read hardware, write Redis) — pins cache to hardware truth on every restart. Reversible; doesn't change the endpoint's read-order semantics (still Redis-first, hardware-fallback) because cache will now be correct post-init.
- Test strategy: TDD. Tests-first for both bugs.
- No backend API/schema changes.
- No frontend API contract changes.

## Open questions (owner-decisions, asked with WHY)
1. **DFR slot label text** — `DFR{board_id} · CH{ch}` (recommended, matches `DfrBoard` model and is hardware-traceable), or `Board {board_id} · CH {ch}`, or `CH {ch}` only (board id only in section header, not per slot)? Why: this label appears on every DFR slot you look at — picking it now prevents a follow-up change.
2. **Bug 2 fix location** — post-init reconciliation inside `MCP23017Driver._initialize_hardware()` (recommended, keeps the fix attached to the hardware driver that owns init), or in `container.py` startup after the driver is instantiated? Why: the former is the simpler blast radius; the latter keeps the driver unaware of Redis.

## Approval gate
Awaiting user approval to write `.omo/plans/dfr-relay-channel-fixes.md`. Once approved, run scaffold, Metis gap analysis (mandatory), append todos, backfill human TL;DR; deliver CLEAR-path question (start now / high-accuracy review?).
