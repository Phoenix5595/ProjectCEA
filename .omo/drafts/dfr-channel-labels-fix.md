# Draft: dfr-channel-labels-fix

## Status
`status: awaiting-approval`
Pending action: write `.omo/plans/dfr-channel-labels-fix.md`
Approval required from: user

## Goal
Stop mislabelling DFR0971 dimming channels as MCP23017 relay channels in the DFR board panel. Each DFR slot must show a DFR-specific identity (board + dimming channel), never a relay number or relay GPIO pin label. The fix must obey AGENTS.md's hardware separation rule: "MCP23017 = relays only / DFR0971 = dimming only / Never swap roles / Bus separation mandatory".

## What I found (evidence with paths)

### Bug A — DFR slot header shows relay identity (PRIMARY)
- File: `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx`
- Line 8: `import { getRelayNumber, getRelayPinLabel } from './relayViewModel'`
- Line 579: `<div className="text-xs font-semibold text-text-default">R{getRelayNumber(ch)} · {getRelayPinLabel(ch)}</div>`
  - `ch` here is the DFR dimming channel index (`0 | 1`) of a `DfrBoard` (each DFR0971 board has exactly 2 dimming channels).
  - `getRelayNumber(ch)` returns `CHANNEL_TO_RELAY[ch]` from `relayViewModel.ts:31` — a relay number from the MCP23017 channel→relay map. So every DFR channel renders as "R{n} · GPA{n}" — making the DFR channel visibly "a relay".
  - This is what the user means by "DFR channels are relays and they seem locked to these relays".

### Bug B — Remove-light confirm uses the wrong relay number
- File: same file, `DfrBoardsPanel.tsx:737`
- Line 737 (JSX string): `"This will also unbind relay R{getRelayNumber(ch)}. Remove light?"`
  - `ch` here is the DFR dimming channel (0 or 1), NOT a relay channel. The actual bound relay channel comes from the light's `bound_relay_channel` field (returned by `getLightsByRoom`).
  - So the warning tells the user the wrong relay will be unbound.

### Hardware-rule violation (AGENTS.md "Hardware Rules")
- MCP23017 = I2C bus 0, address 0x27, channels 0-15 = RELAYS (on/off).
- DFR0971 = I2C bus 1, addresses 0x88/0x89/0x90, 2 channels per board (ch0/ch1) = DIMMING (analog 0-10V).
- The two planes must NEVER share identifiers; mixing them is the documented most-common bug pattern.

## Root cause
`DfrBoardsPanel.tsx` imports from `./relayViewModel` — a module whose purpose is to render MCP23017 relay channel state. DFR dimming-channel slots need their own DFR labelling helper, or the existing `relayViewModel` import is wrong and must be replaced with DFR-specific identifiers (board_id + dimming_channel).

## Components (topology lock)
- **C1 — DFR slot header relabelling** (`DfrBoardsPanel.tsx:579`): replace `R{getRelayNumber(ch)} · {getRelayPinLabel(ch)}` with a DFR-specific label using the board and dimming channel.
- **C2 — Remove-light warning correctness** (`DfrBoardsPanel.tsx:737`): show the light's actual `bound_relay_channel` (lookup from `lightsByKey`/`roomLightsCache`), or omit the unbind warning when no relay binding exists.
- **C3 — Decouple DFR panel from relayViewModel**: after C1+C2, remove the `import { getRelayNumber, getRelayPinLabel } from './relayViewModel'` line entirely so the DFR panel carries no relay model. Optional: add a small `dfrViewModel.ts` helper for DFR label formatting if reused.
- **C4 — Tests**: existing tests are limited to relay (`relayMap.test.ts`, `relayMatrix.test.tsx`) — none cover DfrBoardsPanel. Add unit tests asserting (i) DFR slot label contains "DFR{board}" + "CH{ch}" and NO "R{n}"; (ii) remove-light warning shows the light's real `bound_relay_channel` when present, and the unbind suffix is omitted when none.

## Out of scope (explicitly — separate plan)
- The empty `device_registry` table (alembic at version `008_device_registry`, seed migration `009_seed_device_registry_from_yaml.py` did not run). This is the root cause of the "fully empty device and relay mapping" symptom the user reported in the prior turn, but the current request is *specifically* about the DFR-as-relays labelling bug. It will be addressed by a separate plan.
- Backend `device_registry` schema, alembic, or any data-shape change.
- The other "is the automation loop using a stale relay assignment" question — also separate.

## Approach (planned, post-approval)
- One wave, single-file focused (DfrBoardsPanel.tsx) with a new tiny helper (`dfrViewModel.ts`) if reused; else inline the labels.
- TDD discipline: tests written first asserting the bad label is absent and the good label is present, then make the change.
- No backend changes — the DFR assignments API already returns `dimming_board_id` and `dimming_channel` per light; the frontend just isn't using them for the label.

## Open questions (owner-decisions, asked with WHY)
1. **Scope confirmation** — does this plan stay focused on Bug A + Bug B + tests only, OR should the seed migration / empty `device_registry` table be folded in too? Default: STAY FOCUSED — the labelling bug is one plan, the data-seeding bug is another; bundling them risks the labelling fix dragging on the data-seeding debate.
2. **DFR slot label TEXT** — what should each DFR slot show as its identity? Options:
   - `DFR{board_id} · CH{ch}` (default) — matches the `DfrBoard` model; clearest for hardware-tracing
   - `Board {board_id} · CH {ch}`
   - `CH {ch}` (board id only in the section header, not per slot)
3. **Test strategy** — TDD (write the failing test first)? Default: YES TDD. The test is small and concrete and locks the regression.

## Approval gate
Awaiting user approval to write `.omo/plans/dfr-channel-labels-fix.md`. Once approved, run scaffold, Metis gap analysis (mandatory), append todos, backfill human TL;DR; deliver CLEAR-path question (start now / high-accuracy review?).
