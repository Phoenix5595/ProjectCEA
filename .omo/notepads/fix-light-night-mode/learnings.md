Title: Fix scheduler ramp-down to allow 0% night intensity

What I changed:
- Moved effective minimum calculation for ramp-down out of the inner ramp_key-not-in-state block to ensure it exists for both paths.
- Introduced explicit calculation of effective_minimum at the start of the ramp-down branch, handling NIGHT (0%) and DAY (min 10%) correctly.
- Reworked ramp-down logic to rely on effective_minimum for target_intensity, so 0% can be reached during NIGHT schedules.
- Replaced several hard-coded references to MINIMUM_LIGHT_INTENSITY in ramp-down with ramp-state target_intensity where appropriate, preserving ramp-up behavior.

Verification notes:
- Added defensive coding to prevent undefined variable usage when ramp-down path is revisited within a running ramp.
- Ensured no syntax errors; runtime behavior should ramp from target_intensity down to 0% for NIGHT, while DAY keeps 10% minimum during ramp-down when target_intensity >= 10.

Next steps / potential follow-ups:
- Run unit tests for scheduler ramp logic and simulate NIGHT vs DAY schedules.
- Add targeted tests for ramp-down edge cases where target_intensity is 0 or very low.
- If needed, refactor to reduce branching and improve type hints for static analysis.

---

## 2026-01-29 20:41 UTC: Deployment Verification (Atlas)

**Production verified working:**
- Deployed to `/opt/projectcea/current/`
- Service restarted: `systemctl is-active automation-service` → `active`

**Log evidence (Flower Room lights at NIGHT):**
```
Set light_1 (Flower Room/main) to 0% (intensity: 0.0)
Device Flower Room/main/light_1 (channel 3) set to OFF
Set light_2 (Flower Room/main) to 0% (intensity: 0.0)
Device Flower Room/main/light_2 (channel 4) set to OFF
Set light_3 (Flower Room/main) to 0% (intensity: 0.0)
Device Flower Room/main/light_3 (channel 5) set to OFF
```

**Commit:** `edda103` - fix(scheduler): allow 0% intensity for NIGHT schedules
