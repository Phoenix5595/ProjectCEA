# relay-pin-labels-raw-control — PLANNER DRAFT (resumable)

## Status
- phase: PLAN WRITTEN + METIS GAP ANALYSIS COMPLETE, findings folded in, awaiting user start-work decision
- pending_action: user runs `$start-work` (or opts into high-accuracy review)
- plan_file: `.omo/plans/relay-pin-labels-raw-control.md` (COMPLETE, decision-complete, gap-analyzed)

## Intent routing
- CLEAR — user knows the outcome; only genuine forks were the 3 interview questions.

## User decisions (answered 2026-07-05)
1. Pin label format → `GPA0` / `GPB7` (keep existing `getRelayPinLabel` output). REJECTED the shorter `PA0/PB7` form.
2. Unassigned relay timer → BACKEND-based (Redis override record + control-loop sweep). User explicitly: "if the current version isn't also backend-based [...] it should be so it survives pages closing and will turn off by itself after the time set, even if the frontend page is fully closed."
3. 'Auto' on unassigned relays → HIDE. Unassigned relays only show ON-timers + Off.

## Metis gap analysis — COMPLETE (run 2026-07-05)
Metis verdict: APPROVE-WITH-FINDINGS. All 8 findings folded into the plan:

| ID | Sev | Finding | Fix applied |
|----|-----|---------|-------------|
| F1 | BLOCKER | `AutomationRedisClient` has no `set`/`setex`/`delete` (only `get` at __init__.py:135) | Use `automation_redis.redis_client.setex(...)` / `.delete(...)` (raw `redis.Redis` at __init__.py:76); wrap in `await asyncio.to_thread(...)` in async context (matches StateManager pattern at app/state/__init__.py:443) |
| F2 | MAJOR | `ControlEngine` has no `self.redis_client` attr | Sweep reaches client via `self.database._automation_redis.redis_client` (access path already used at control_engine.py:109) |
| F3 | MAJOR | DeviceManager imports inverted — plan said `getRelayNumber` IS imported (false), `getRelayPinLabel` is NOT (also false) | INDEPENDENTLY VERIFIED: `getRelayPinLabel` IS imported (DeviceManager.tsx:21), `getRelayNumber` is NOT. Todos 6+7 corrected: ADD `getRelayNumber` to imports |
| F4 | MAJOR | Dependency matrix contradiction — Todo 5 claimed parallelizable with Todo 8 (which 8 blocks on 5); Todos 6+7 both edit DeviceManager.tsx | Split Wave 2 into 2a (4,5,7) + 2b (6,8); 6+7 serialized |
| F5 | MAJOR | Indefinite ON (state==1, no duration) didn't delete prior timed override key → sweep would later turn OFF | Added: when state==1 + no duration, DELETE existing override key first; verified at Todo 2 acceptance (`redis-cli GET` returns nil after indefinite ON) |
| F6 | MINOR | Frontend QA scenarios described UI interactions without specifying tool | Moved interactive UI verification to Todo 9 post-deploy (steps 1-6); Todos 5+6 scoped to tsc+build+grep |
| F7 | MINOR | Todo 2 didn't explicitly say to add `automation_redis` param to route signature | Added explicit instruction |
| F8 | NIT | Template-string constant inconsistent with existing helper-function pattern | Use `relay_raw_override_key(channel: int) -> str` helper function |

All Metis findings independently verified against source files before plan rewrite. BLOCKER + 4 MAJOR confirmed accurate; fixes folded in.

## Discovered facts (verified by Metis + my own exploration)
- `relay_manager.set_channel_state` (relay_manager.py:179-196) ALREADY fixed in-session to drop the `_channel_map` guard ✅
- `POST /api/hardware/relays/channel/{channel}/state` (hardware.py:98-134) ALREADY exists; only injects `relay_manager` (line 107-111) — Todo 2 adds `automation_redis` param ✅
- `AutomationRedisClient.redis_client` is a PUBLIC `redis.Redis | None` attribute (redis/__init__.py:76) — confirmed ✅
- `ControlEngine.__init__` (control_engine.py:45-120): has `relay_manager` (66), `database` (67), NO `redis_client` attr; `database._automation_redis` is reachable (used at line 109) ✅
- Device-assigned timer path (devices.py:219-234): `manual_expires_at` confirmed real + backend-based ✅
- Existing `_expire_manual_overrides` (control_engine.py:427) is async; call site at line 268 inside `run_control_loop` ✅
- StateManager pattern `await asyncio.to_thread(self._redis_client.setex, key, ttl, value)` at state/__init__.py:443 — the established async-call-to-sync-redis pattern to mirror ✅
- DeviceManager.tsx:17-24 imports: `getRelayPinLabel` IS (line 21), `getRelayNumber` is NOT ✅
- LSP "Import shared.infra_logging could not be resolved" is PRE-EXISTING (runtime-only path) on every automation-service file — NOT introduced by this plan.

## Scope guardrails recorded
- Internal `channel` int field (0-15) is NOT renamed — it's the MCP23017 hardware address.
- No DB migration (Redis-only for raw overrides).
- No `control_history` rows for raw channel overrides.
- No frontend `setTimeout` for the OFF timer.
- Device-assigned path untouched.
- Do NOT add `set`/`setex`/`delete` to `AutomationRedisClient` — use the raw redis.Redis directly.

## Plan structure (post-Metis)
- 9 todos across 3 waves (Wave 1 backend sequential 1→2→3; Wave 2a frontend parallel 4/5/7; Wave 2b serialized 6 then 8; Wave 3 deploy=9).
- Backend commits: Todo 2 (route+Redis override), Todo 3 (sweep).
- Frontend commits: single bundled `feat(frontend): relay pin labels + unassigned relay control`.
- Deploy: single `./deploy.sh` + post-deploy interactive verification (6 steps including page-reload-survival test).

## Open questions for user at delivery (CLEAR path: ONE question)
- Start work now, or run a high-accuracy Momus review first?
