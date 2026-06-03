# User Preferences - Antoine

> Essential preferences for AI assistants. Updated: 2026-01-13

## Workflow

- SSH to mothernode via Tailscale
- Dev at /home/antoine, prod at /opt/projectcea
- Full TDD for all new code
- Prefer fixing over workarounds

## Non-Negotiables

1. 1/sec data sampling - never reduce
2. 100ms max DB batch delay
3. Live dashboard instant via Redis
4. Light control must keep working
5. Rollback in <30 seconds

## Decisions Made

| Topic | Choice |
|-------|--------|
| PID | Self-tuning (auto Kp/Ki/Kd) |
| Leaf temp | Daily manual + time-varying |
| AI | Hybrid Pi + home server |
| Storage | NVMe SSD (already optimal) |
| Safety | Software-only for now |
| Lighting | Both rooms night cycle |
| CO2 | ASC on, design for FRC |
| MQTT | Add when expanding rooms |

## Data Goals

- 1yr full resolution for AI training
- Indefinite hourly aggregates  
- Predict spikes, detect degradation
- Auto-tune PID, guide grower

## Reminders for Future

- MQTT: When adding Lab/Water Management
- IR Camera: Replace manual leaf temp delta
- CO2 FRC: If enrichment added, disable ASC
- Safety hardware: If scaling beyond 2 rooms
