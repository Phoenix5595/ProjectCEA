# ProjectCEA Optimization Master Plan - FINAL

Version: 3.0 Final | Created: 2026-01-13 | Status: APPROVED

## LOCKED DECISIONS

Control: Self-tuning PID with UI reset and K-value display, VPD as master
Data: 1/sec sampling, 100ms batch, 1yr retention, hybrid AI (Pi + server)
Infrastructure: 512GB NVMe, night lighting, CAN now/MQTT later, software safety

## PHASE 1: Critical Reliability (Week 1)

1.1 Bug Fixes
- Fix device_type argument mismatch
- Fix 42 bare except clauses  
- Replace 23 print statements with logging

1.2 Rollback System
- Document symlink deploy strategy
- Create /opt/projectcea/rollback.sh script
- Test rollback procedure
- Verify 10 releases kept

1.3 Service Hardening
- Implement 100ms batched DB writes
- Add hardware watchdog (15s timeout)
- Add graceful shutdown handlers

Success: Zero bare excepts, rollback tested, watchdog working

## PHASE 2: Self-Tuning PID (Week 1-2)

- Implement relay feedback auto-tuning
- Add deadbands (0.5C temp, 2% humidity)
- Add anti-windup
- Frontend: Display Kp/Ki/Kd values, reset button, tuning status

Success: Auto-tunes in 2-3 cycles, K values in UI, reset works

## PHASE 3: Leaf Temperature UI (Week 2)

- Add leaf_delta_day, leaf_delta_night columns
- Frontend inputs for day/night delta
- Time-varying delta interpolation
- Integrate with VPD calculation

Success: Deltas in UI, correct delta per mode, VPD uses them

## PHASE 4: VPD Cascade Control (Week 2-3)

- VPDCascadeController (VPD master, humidity slave)
- Heating failure safety logic
- Dashboard VPD display

Success: VPD drives humidity, safety works, dashboard shows VPD

## PHASE 5: Database Optimization (Week 3)

- Convert automation_state to hypertable
- Enable compression (7 day policy)
- Create continuous aggregates
- AI training data export function

Success: Hypertables working, compression active, export tested

## PHASE 6: Home Server AI (Week 4+)

- Data sync Pi to server
- XGBoost spike prediction
- Prediction API integration

Success: Predictions in dashboard, latency under 210ms

## PHASE 7-8: Code Quality and TDD (Ongoing)

- Type hints, structured logging, config externalization
- Fix existing tests, add unit/integration tests

## METRICS

Temperature: +/- 0.5C | VPD: +/- 0.1 kPa | Uptime: 99.9% | Rollback: <30s
