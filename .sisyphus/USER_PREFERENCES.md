# User Preferences - Antoine

> Preferences and workflow notes for AI assistants working on ProjectCEA.

## Development Workflow

- **Primary editing:** Via SSH on the Pi (mothernode) using opencode/omo
- **Alternative:** Cursor IDE with SSH remote (keep as fallback option)
- **No local clone:** All development happens directly on the Pi
- **Testing:** Deploy to /opt/projectcea/, access via browser over Tailscale

## Network Setup

- **Tailscale:** All machines connected via Tailscale mesh
- **Pi hostname:** `mothernode` (accessed via Tailscale IP in ~/.ssh/config)
- **Services bind to:** `0.0.0.0` (accessible over Tailscale)

## Ownership & Security

- **Production directories:** Owned by root (more secure)
- **Secrets:** Store in EnvironmentFile, not inline in systemd units
- **User:** `antoine` for development, services run as `antoine`

## Git Comfort Level

- GitHub synced but not fully comfortable with git workflows
- Prefer simple commit/push, avoid complex rebasing
- May have uncommitted or unpushed changes - check before operations

## Critical Constraints

1. **Light control MUST keep working** - automation-service controls lights, cannot have extended downtime
2. **Rollback capability** - any deploy must be quickly reversible (<30 seconds)
3. **No password exposure** - never print/log actual password values

## Preferences

- Keep 10 releases for rollback capability
- Root ownership for /opt/projectcea/
- Push-based deploys (from dev area to production)
- Prefer fixing issues over workarounds

## Communication Style

- Explain technical decisions briefly
- Ask clarifying questions rather than assume
- Warn before any destructive operations

---

## ⚠️ NON-NEGOTIABLE: GRAFANA DATA PRECISION ⚠️

**This is a LIVE climate monitoring system. Sensor data arrives every second.**

### NEVER reduce aggregation granularity for "performance"

| Duration | MUST use | Points per sensor |
|----------|----------|-------------------|
| < 1 hour | RAW data | ~3600 |
| 1h - 24h | 1-minute aggregate | ~1440 |
| 1-7 days | 5-minute aggregate | ~2000 |
| > 7 days | Hourly aggregate | ~168 |

**WHY:** Temperature can swing 5°C in minutes, humidity 40% in 5 minutes. Humidity 20% in an hour. 
Users MUST see these swings to respond. Never optimize for fewer points.

**If Pi is slow:** Reduce refresh rate, not data granularity.
