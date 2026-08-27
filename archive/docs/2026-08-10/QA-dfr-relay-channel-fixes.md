# Manual QA Plan: `dfr-relay-channel-fixes`

**Commit:** `e927c56`  
**Branch:** `main` (pre-deploy)  
**Dashboard:** http://mothernode:8080  
**Automation API:** http://mothernode:8080 (proxied through Caddy)

---

## 1. Pre-Deploy Checklist

Verify these conditions **before** running `./deploy.sh`.

| # | Check | Command / Step | Expected Result |
|---|-------|----------------|-----------------|
| 1.1 | **Git status clean** | `git status` | Working tree clean; commit `e927c56` is on `main` |
| 1.2 | **Tests pass** | `cd Infrastructure/automation-service && python -m pytest tests/test_relay_redis_reconciliation.py -v` | 4/4 tests pass |
| 1.3 | **Frontend tests pass** | `cd Infrastructure/frontend && npm test -- --run --testPathPattern=DfrBoardsPanel` | 2/2 tests pass (label assertion + remove warning) |
| 1.4 | **Build succeeds** | `cd Infrastructure/frontend && npm run build` | Exit 0, no TypeScript errors |
| 1.5 | **Current relay state known** | `curl -s http://mothernode:8080/api/hardware/relays/state \| jq '.channels'` | Note which channels are `true` (will verify they go `false` after restart) |
| 1.6 | **Redis relay keys exist (or note if absent)** | `redis-cli GET "cea:relay:channels"` | Note current value (may be stale `true`s) |
| 1.7 | **Service healthy** | `systemctl is-active automation-service` | `active` |
| 1.8 | **No uncommitted config changes** | `git diff automation_config.yaml` | Empty (or documented) |

### Quick pre-deploy command block

```bash
# Run from repo root
echo "=== 1.1 Git status ==="
git log --oneline -1
git status --short

echo "=== 1.2 Backend tests ==="
cd Infrastructure/automation-service
python -m pytest tests/test_relay_redis_reconciliation.py -v
cd ../..

echo "=== 1.3 Frontend tests ==="
cd Infrastructure/frontend
npm test -- --run --testPathPattern=DfrBoardsPanel
cd ../..

echo "=== 1.5 Current relay state ==="
curl -s http://mothernode:8080/api/hardware/relays/state | jq '.channels'

echo "=== 1.6 Current Redis relay key ==="
redis-cli GET "cea:relay:channels"

echo "=== 1.7 Service status ==="
systemctl is-active automation-service
```

**All checks must pass before proceeding to deploy.**

---

## 2. Post-Deploy Verification Script

Run these steps **in order** after `./deploy.sh` completes successfully.

### Step 2.1 — Deploy & Confirm Service Restart

```bash
# From repo root
./deploy.sh

# Verify automation-service restarted and is active
systemctl is-active automation-service
# EXPECTED: active

# Verify no crash loops
systemctl status automation-service --no-pager | head -n 20
# EXPECTED: Active: active (running) ...
```

### Step 2.2 — QA Item 1: DFR Panel Labels

**Browser steps:**

1. Open http://mothernode:8080
2. Navigate to **Devices → DFR Boards** (or the DFR0971 dimming panel)
3. Inspect every channel slot

**Expected:**
- Every slot shows `DFR{n} · CH{0|1}` (e.g., `DFR0 · CH0`, `DFR0 · CH1`, `DFR1 · CH0`, etc.)
- **No** slot shows `R{n}` (relay number)
- **No** slot shows `GPA` or `GPB` (GPIO pin labels)

**Verification command (alternative / automated check):**

```bash
# This checks the built frontend bundle for the old strings
grep -r "R{getRelayNumber" Infrastructure/frontend/dist/ 2>/dev/null || echo "PASS: No old relay labels in bundle"
grep -r "GPA\|GPB" Infrastructure/frontend/dist/ 2>/dev/null | grep -v node_modules || echo "PASS: No GPIO pin labels in bundle"
# EXPECTED: Both lines print "PASS"
```

### Step 2.3 — QA Item 2: Remove-Light Warning Text

**Browser steps:**

1. On the DFR panel, find a channel that has a light assigned (shows a location name, not "Unassigned")
2. Click the **Remove** (or trash icon) button on that assigned slot
3. Read the confirmation warning text

**Expected:**
- Warning reads exactly: `Remove light? (Its relay will also be unbound.)`
- **No** relay number appears (e.g., must NOT say `This will also unbind relay R3.`)

**Verification via DOM (DevTools console):**

```javascript
// In browser DevTools on the DFR panel page
const warning = document.body.innerText;
console.assert(
  warning.includes("Remove light? (Its relay will also be unbound.)"),
  "FAIL: Expected remove warning not found"
);
console.assert(
  !warning.match(/unbind relay R\d+/),
  "FAIL: Old relay number still in warning text"
);
console.log("PASS: Remove warning text correct");
```

### Step 2.4 — QA Item 3: Redis Relay State Reconciliation

After the automation-service restarts, the new reconciliation block in `container.py` must:
1. Read actual MCP23017 hardware state (all OFF after `all_off()`)
2. Write `[false, false, ..., false]` (16×) to Redis `cea:relay:channels`
3. Write `[null, null, ..., null]` (16×) to Redis `cea:relay:timestamps`

```bash
# Check Redis relay channels
echo "=== Relay channels ==="
redis-cli GET "cea:relay:channels"
# EXPECTED: [false, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false]

echo "=== Relay timestamps ==="
redis-cli GET "cea:relay:timestamps"
# EXPECTED: [null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null]
```

**JSON validation (strict):**

```bash
redis-cli GET "cea:relay:channels" | jq -e 'length == 16 and all(. == false)'
# EXPECTED: true

redis-cli GET "cea:relay:timestamps" | jq -e 'length == 16 and all(. == null)'
# EXPECTED: true
```

### Step 2.5 — QA Item 4: Relay Matrix Shows All IDLE

**Browser steps:**

1. Open http://mothernode:8080
2. Navigate to the **Relay Matrix** (or Relays / Hardware Status page)
3. Inspect all 16 relay channels

**Expected:**
- Every channel shows `IDLE` or `OFF` (not `ON` / `ACTIVE`)
- No stale `ON` states from pre-restart Redis cache

**API verification:**

```bash
curl -s http://mothernode:8080/api/hardware/relays/state | jq '
  {
    all_off: (.channels | all(. == false)),
    channel_count: (.channels | length),
    mcp_connected: .mcp_connected
  }'
# EXPECTED:
# {
#   "all_off": true,
#   "channel_count": 16,
#   "mcp_connected": true
# }
```

---

## 3. Rollback Trigger Conditions

If **any** of the following occur, execute `./rollback-deploy.sh` immediately:

| Condition | Symptom |
|-----------|---------|
| Service fails to start | `systemctl is-active automation-service` returns `inactive` or `failed` |
| Redis keys missing | `redis-cli GET "cea:relay:channels"` returns `(nil)` **and** API also returns empty channels |
| Relay matrix shows ON after restart | Any channel `true` when all should be `false` |
| DFR labels reverted | Slots show `R{n}` or `GPA`/`GPB` instead of `DFR{n} · CH{ch}` |
| Remove warning wrong | Warning contains relay number (`R3`, etc.) |

---

## 4. One-Shot Post-Deploy Script

Save as `qa-dfr-relay-channel-fixes.sh` and run after deploy:

```bash
#!/usr/bin/env bash
set -euo pipefail

API="http://mothernode:8080"
PASS=0
FAIL=0

pass() { echo "  ✓ PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "  ✗ FAIL: $1"; FAIL=$((FAIL+1)); }

echo "=== Post-Deploy QA: dfr-relay-channel-fixes ==="
echo

# 4.1 Service health
echo "[4.1] Service health"
if systemctl is-active automation-service >/dev/null 2>&1; then
  pass "automation-service is active"
else
  fail "automation-service is NOT active"
fi

# 4.2 Redis relay channels
echo "[4.2] Redis relay channels (all false)"
RAW_CHANNELS=$(redis-cli GET "cea:relay:channels" 2>/dev/null || true)
if [ -n "$RAW_CHANNELS" ]; then
  ALL_FALSE=$(echo "$RAW_CHANNELS" | jq -e 'all(. == false)' 2>/dev/null || echo "false")
  if [ "$ALL_FALSE" = "true" ]; then
    pass "cea:relay:channels is all-false"
  else
    fail "cea:relay:channels is NOT all-false: $RAW_CHANNELS"
  fi
else
  fail "cea:relay:channels key is missing"
fi

# 4.3 Redis relay timestamps
echo "[4.3] Redis relay timestamps (all null)"
RAW_TS=$(redis-cli GET "cea:relay:timestamps" 2>/dev/null || true)
if [ -n "$RAW_TS" ]; then
  ALL_NULL=$(echo "$RAW_TS" | jq -e 'all(. == null)' 2>/dev/null || echo "false")
  if [ "$ALL_NULL" = "true" ]; then
    pass "cea:relay:timestamps is all-null"
  else
    fail "cea:relay:timestamps is NOT all-null: $RAW_TS"
  fi
else
  fail "cea:relay:timestamps key is missing"
fi

# 4.4 API relay state
echo "[4.4] API relay state (all off, 16 channels, MCP connected)"
RELAY_JSON=$(curl -s "${API}/api/hardware/relays/state" 2>/dev/null || true)
if [ -n "$RELAY_JSON" ]; then
  ALL_OFF=$(echo "$RELAY_JSON" | jq -r '.channels | all(. == false)' 2>/dev/null || echo "false")
  COUNT=$(echo "$RELAY_JSON" | jq -r '(.channels | length)' 2>/dev/null || echo "0")
  MCP=$(echo "$RELAY_JSON" | jq -r '.mcp_connected' 2>/dev/null || echo "false")
  if [ "$ALL_OFF" = "true" ] && [ "$COUNT" = "16" ] && [ "$MCP" = "true" ]; then
    pass "API relay state correct"
  else
    fail "API relay state wrong: all_off=$ALL_OFF count=$COUNT mcp=$MCP"
  fi
else
  fail "API relay state endpoint unreachable"
fi

# 4.5 Frontend bundle check (old strings absent)
echo "[4.5] Frontend bundle (no old DFR labels)"
if [ -d "Infrastructure/frontend/dist" ]; then
  if grep -rq "getRelayNumber\|getRelayPinLabel" Infrastructure/frontend/dist/ 2>/dev/null; then
    fail "Old relay view model imports still in bundle"
  else
    pass "No old relay view model imports in bundle"
  fi
  if grep -rq "GPA\|GPB" Infrastructure/frontend/dist/ 2>/dev/null; then
    fail "Old GPIO pin labels still in bundle"
  else
    pass "No old GPIO pin labels in bundle"
  fi
else
  echo "  ⚠ SKIP: frontend/dist not present (run npm run build first)"
fi

echo
echo "=== Results ==="
echo "Passed: $PASS"
echo "Failed: $FAIL"

if [ "$FAIL" -eq 0 ]; then
  echo "VERDICT: READY FOR QA — all automated checks passed."
  echo "Next: perform manual browser checks (DFR panel labels, remove warning, relay matrix)."
  exit 0
else
  echo "VERDICT: BLOCKED — $FAIL check(s) failed."
  echo "Consider: ./rollback-deploy.sh"
  exit 1
fi
```

**Usage:**

```bash
chmod +x qa-dfr-relay-channel-fixes.sh
./qa-dfr-relay-channel-fixes.sh
```

---

## 5. Manual Browser Checklist (Required)

The automated script covers backend state. These **must** be verified visually in the browser:

| # | Step | Location | Expected |
|---|------|----------|----------|
| 5.1 | Open DFR Boards panel | Devices → DFR Boards | Every slot: `DFR{n} · CH{ch}` |
| 5.2 | Click Remove on assigned light | DFR Boards panel | Warning: `Remove light? (Its relay will also be unbound.)` |
| 5.3 | Open Relay Matrix | Devices → Relays (or Hardware Status) | All 16 channels show `IDLE`/`OFF` |
| 5.4 | Hard-refresh frontend | Ctrl+Shift+R (or Cmd+Shift+R) | No cached old JS/CSS |

---

## 6. Verdict

**READY FOR QA** — This plan can be executed immediately after `./deploy.sh`.

- **Automated checks:** Redis keys, API response, frontend bundle strings
- **Manual checks:** Browser visual verification of DFR labels, remove warning, relay matrix
- **Rollback:** `./rollback-deploy.sh` if any check fails

---

## Appendix: Changed Files Summary

| File | Change | QA Impact |
|------|--------|-----------|
| `DfrBoardsPanel.tsx` | Slot labels: `R{n} · {pin}` → `DFR{board_id} · CH{ch}` | Item 1 |
| `DfrBoardsPanel.tsx` | Remove warning: no relay number | Item 2 |
| `container.py` | Reconciliation block after `all_off()` → writes Redis | Item 3, 4 |
| `redis/__init__.py` | Added `.set()` method to `AutomationRedisClient` | Item 3 (enabler) |
| `test_relay_redis_reconciliation.py` | 4 TDD tests for reconciliation | Pre-deploy check |
